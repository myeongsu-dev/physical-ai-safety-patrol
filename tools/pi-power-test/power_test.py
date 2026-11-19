"""Pi 5 보조배터리 시험: 이 보조배터리로 Pi 5 + AI 추론을 돌려도 되는지 판정하고, 몇 분 버티는지 잰다.

  python tools/pi-power-test/power_test.py check                  시험 전 점검
  python tools/pi-power-test/power_test.py run --label 이름 ...   시험 1회 (README 참고)
  python tools/pi-power-test/power_test.py report                 전체 판정 표 + 진단서
  python tools/pi-power-test/power_test.py selfcheck              판정 규칙 셀프 체크 (PC·CI)

판정 원칙: '사용 불가'는 Pi 펌웨어의 전압 부족 기록, 재부팅으로 확인된 전원 꺼짐, 실측 시간 부족이 있을 때만.
배터리 탓인지 모르는 문제(발열, 프로그램 죽음, XIAO만 끊김, 부하가 안 걸림)는 '재시험'으로 돌린다.
"""
import argparse
import csv
import datetime as dt
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
PHASES = [("대기", 120, 0), ("평상", 480, 1), ("최대", 300, 2)]  # (이름, 초, 추론 프로세스 수)
DRAIN = "방전"
STD_SECONDS = sum(p[1] for p in PHASES)
EVERY = 2.0      # 기록 간격(초)
WARMUP = 20      # 구간 시작 뒤 모델을 불러오는 동안은 부하 검사에서 뺌(초)
HOT_C = 80.0     # Pi 5가 CPU 속도를 낮추기 시작하는 온도
LOW_V = 4.8      # 이보다 낮으면 '여유 적음' 참고 표시 (판정은 펌웨어 비트로만)
EFF = (0.75, 0.90)  # 보조배터리 변환 효율 범위 (3.7V 셀 → 5V 출력)
EFF_MID = 0.85
XIAO_VID = "303a"   # Espressif USB 제조사 번호

# vcgencmd get_throttled 비트: 아래 16비트는 '지금', 위 16비트는 '부팅 뒤 한 번이라도'
UNDERVOLT = 0x1 | 0x10000
THROTTLED = 0x4 | 0x40000
COLS = ["t", "phase", "ext5v", "throttled", "temp", "arm_mhz", "pmic_w", "fps1", "fps2", "alive", "xiao"]


# ── 측정 ────────────────────────────────────────────────────────────────
def vc(*args):
    out = subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=5)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"vcgencmd {' '.join(args)} 실패: {out.stderr.strip() or out.stdout.strip() or '출력 없음'}")
    return out.stdout


def parse_throttled(text):
    return int(re.search(r"throttled=(0x[0-9a-fA-F]+)", text).group(1), 16)


def parse_temp(text):
    return float(re.search(r"temp=([\d.]+)", text).group(1))


def parse_clock(text):
    return int(re.search(r"=(\d+)", text).group(1)) / 1e6


def parse_pmic(text):
    """vcgencmd pmic_read_adc → (EXT5V 전압, 레일 전력 합 W). 레일은 이름_A(전류)와 이름_V(전압)를 짝지음."""
    amps, volts = {}, {}
    for name, kind, val in re.findall(r"(\S+)_([AV]) (?:current|volt)\(\d+\)=([\d.]+)", text):
        (amps if kind == "A" else volts)[name] = float(val)
    if "EXT5V" not in volts:
        raise ValueError("EXT5V_V 값이 없음")
    return volts["EXT5V"], sum(a * volts[n] for n, a in amps.items() if n in volts)


def read_file(path, binary=False):
    try:
        return Path(path).read_bytes() if binary else Path(path).read_text().strip("\x00\n ")
    except OSError:
        return None


def boot_id():
    return read_file("/proc/sys/kernel/random/boot_id")


def max_current():
    raw = read_file("/proc/device-tree/chosen/power/max_current", binary=True)
    return str(int.from_bytes(raw[:4], "big")) if raw and len(raw) >= 4 else "읽지 못함"


def xiao_present():
    return any(read_file(p) == XIAO_VID for p in Path("/sys/bus/usb/devices").glob("*/idVendor"))


def sample():
    """한 번 잰 값. 실패한 항목은 빈칸으로 남기고 판정에서 '측정 실패'로 센다."""
    row = {}
    for key, fn in [("throttled", lambda: f"0x{parse_throttled(vc('get_throttled')):x}"),
                    ("temp", lambda: parse_temp(vc("measure_temp"))),
                    ("arm_mhz", lambda: round(parse_clock(vc("measure_clock", "arm")))),
                    ("pmic", lambda: parse_pmic(vc("pmic_read_adc")))]:
        try:
            row[key] = fn()
        except Exception:
            row[key] = ""
    v, w = row.pop("pmic") or ("", "")
    row["ext5v"], row["pmic_w"] = (round(v, 3), round(w, 2)) if v != "" else ("", "")
    row["xiao"] = int(xiao_present())
    return row


# ── 추론 부하 ───────────────────────────────────────────────────────────
def infer_worker(model, size, count, stop):
    from ultralytics import YOLO
    from ultralytics.utils import ASSETS
    import cv2
    m, frame = YOLO(model), cv2.imread(str(ASSETS / "bus.jpg"))
    while not stop.is_set():
        m.predict(frame, imgsz=size, verbose=False)
        with count.get_lock():
            count.value += 1


class Load:
    """추론 프로세스 n개를 켜고 끈다. spawn이라 부모에서 torch를 불러도 안전."""

    def __init__(self, model, size):
        import multiprocessing as mp
        self.mp, self.model, self.size, self.procs = mp.get_context("spawn"), model, size, []

    def set(self, n):
        while len(self.procs) > n:
            p, _, stop = self.procs.pop()
            stop.set()
            p.join(10)
            if p.is_alive():
                p.terminate()
        while len(self.procs) < n:
            count, stop = self.mp.Value("q", 0), self.mp.Event()
            p = self.mp.Process(target=infer_worker, args=(self.model, self.size, count, stop), daemon=True)
            p.start()
            self.procs.append((p, count, stop))

    def counts(self):
        return [c.value for _, c, _ in self.procs] + [0] * (2 - len(self.procs))

    def alive(self):
        return sum(p.is_alive() for p, _, _ in self.procs)


# ── 시험 전 점검 ─────────────────────────────────────────────────────────
def preflight(model, size):
    """(문제 목록, 기록할 정보). 문제가 하나라도 있으면 시험을 시작하지 않는다."""
    problems, info = [], {}
    if not shutil.which("vcgencmd"):
        return ["vcgencmd가 없습니다. Raspberry Pi OS(라즈베리 파이 5)에서 실행해야 합니다."], info
    for args, parse, key in [(("get_throttled",), parse_throttled, "start_throttled"),
                             (("measure_temp",), parse_temp, "start_temp"),
                             (("measure_clock", "arm"), parse_clock, None),
                             (("pmic_read_adc",), parse_pmic, "start_pmic")]:
        try:
            raw = vc(*args)
        except Exception as e:
            problems.append(f"{e}")
            continue
        try:
            val = parse(raw)
            if key == "start_throttled":
                val = f"0x{val:x}"
            if key:
                info[key] = val
        except Exception as e:
            problems.append(f"vcgencmd {' '.join(args)} 출력을 읽지 못함 ({e}). 출력 원문:\n{raw.strip()}")
    try:
        info["usb_max_current_enable"] = vc("get_config", "usb_max_current_enable").strip()
    except Exception:
        info["usb_max_current_enable"] = "읽지 못함"
    info["max_current"] = max_current()
    info["boot_id"] = boot_id() or ""
    info["serial"] = read_file("/proc/device-tree/serial-number") or ""
    if not info["boot_id"]:
        problems.append("부팅 ID(/proc/sys/kernel/random/boot_id)를 읽지 못함. 전원 꺼짐을 확인할 수 없습니다.")
    if not xiao_present():
        problems.append("XIAO ESP32-S3가 USB에 보이지 않습니다. Pi USB 포트에 꽂고 `lsusb`에 Espressif가 나오는지 확인하세요.")
    try:
        from ultralytics import YOLO
        from ultralytics.utils import ASSETS
        t = time.monotonic()
        YOLO(model).predict(str(ASSETS / "bus.jpg"), imgsz=size, verbose=False)
        info["first_infer_s"] = round(time.monotonic() - t, 1)
    except ImportError:
        problems.append("ultralytics가 없습니다. venv를 켜고 `pip install ultralytics` (README 'Pi 준비').")
    except Exception as e:
        problems.append(f"추론 한 장이 실패했습니다 ({e}). 모델 {model}이 없으면 인터넷 되는 곳에서 check를 한 번 돌려 받아 두세요.")
    try:
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / ".write_test").write_text("ok")
        (RESULTS / ".write_test").unlink()
    except OSError as e:
        problems.append(f"results 폴더에 쓸 수 없습니다 ({e}).")
    return problems, info


# ── CSV 읽고 쓰기 ────────────────────────────────────────────────────────
def read_run(path):
    meta, rows, end = {}, [], None
    with open(path, encoding="utf-8", newline="") as f:
        lines = f.read().splitlines()
    body = []
    for line in lines:
        if line.startswith("#"):
            k, _, v = line[1:].strip().partition("=")
            meta[k] = v
        elif line.startswith("END,"):
            end = line[4:]
        else:
            body.append(line)
    for r in csv.DictReader(body):
        try:  # 전원이 꺼지며 반쯤 쓰인 마지막 줄은 버림
            rows.append({"t": float(r["t"]), "phase": r["phase"], "ext5v": num(r["ext5v"]),
                         "throttled": int(r["throttled"], 16) if r["throttled"] else None,
                         "temp": num(r["temp"]), "arm_mhz": num(r["arm_mhz"]), "pmic_w": num(r["pmic_w"]),
                         "fps1": float(r["fps1"]), "fps2": float(r["fps2"]), "alive": int(r["alive"]),
                         "xiao": int(r["xiao"])})
        except (KeyError, ValueError, TypeError):
            pass
    return {"path": Path(path), "meta": meta, "rows": rows, "end": end}


def num(s):
    return float(s) if s not in ("", None) else None


# ── 판정 ────────────────────────────────────────────────────────────────
def fmt_t(sec):
    m = int(sec // 60)
    return f"{m // 60}시간 {m % 60}분" if m >= 60 else f"{m}분 {int(sec % 60)}초"


def at(r):
    return f"{fmt_t(r['t'])} 지점({r['phase']} 구간, 전압 {r['ext5v']}V, {r['temp']}℃)"


def need_procs(phase):
    return dict((p[0], p[2]) for p in PHASES).get(phase, 1)


def phase_start(phase):
    s = 0
    for name, sec, _ in PHASES:
        if name == phase:
            return s
        s += sec
    return STD_SECONDS


def judge(run, all_runs=(), current_boot=None):
    """→ (판정, 이유, 다음에 할 일, 근거 줄 목록, 실측 시간(초)|None).
    판정: 사용 가능 · 사용 불가 · 재시험 · 시험 무효 · 기준 완료(어댑터)"""
    v = _judge(run, all_runs, current_boot)
    return v if len(v) == 5 else (*v, None)


def _judge(run, all_runs, current_boot):
    meta, rows, end = run["meta"], run["rows"], run["end"]
    adapter, drain = meta.get("kind") == "adapter", meta.get("drain") == "1"
    need = float(meta.get("need_min", 60)) * 60
    std = [r for r in rows if r["phase"] != DRAIN]
    ev = [f"기록 {len(rows)}줄, 마지막 {fmt_t(rows[-1]['t'])}" if rows else "기록 0줄"]
    start_flags = int(meta.get("start_throttled", "0x0"), 16)
    uv_rows = [r for r in rows if r["throttled"] is not None and r["throttled"] & UNDERVOLT]
    first_uv = uv_rows[0] if uv_rows else None
    cable = "굵고 짧은 다른 USB-C 케이블로 한 번 더 시험해 보고, 그래도 같으면 이 배터리는 쓰지 마세요."

    # 1. 기록이 믿을 만한가
    same_boot = [o for o in all_runs if o is not run and o["meta"].get("boot_id") == meta.get("boot_id")
                 and meta.get("boot_id") and o["meta"].get("started", "") < meta.get("started", "")
                 and o["meta"].get("label") != meta.get("label")]
    if same_boot:
        return ("재시험", f"'{same_boot[0]['meta'].get('label')}' 회차와 같은 부팅 중에 기록됨",
                "보조배터리를 바꾸면 Pi가 반드시 꺼졌다 켜집니다. 이름(--label)을 잘못 붙였거나 배터리를 바꾸지 않은 것입니다. 배터리를 바꿔 꽂고(재부팅) 다시 시험하세요.", ev)
    if not rows:
        return ("재시험", "기록이 한 줄도 없음", "시작하자마자 끊겼습니다. check부터 다시 돌리세요.", ev)
    if adapter and (start_flags & UNDERVOLT or first_uv):
        ev.append(f"전압 부족 기록: {at(first_uv) if first_uv else '시작할 때 이미 있음'}")
        return ("시험 무효", "어댑터(기준)인데 전압 부족이 났음",
                "기준이 잘못됐습니다. 정품 27W(5V 5A) 어댑터나 다른 케이블로 바꿔 기준부터 다시 재세요.", ev)

    # 2. 확실한 불가 증거
    if start_flags & UNDERVOLT:
        ev.append(f"시험 시작 때 get_throttled={meta.get('start_throttled')} (부팅 뒤 전압 부족이 이미 있었음)")
        return ("사용 불가", "이 배터리로 부팅하는 중에 전압 부족이 났음", cable, ev)
    if first_uv and first_uv["phase"] != DRAIN:
        ev.append(f"처음 전압 부족: {at(first_uv)}, get_throttled=0x{first_uv['throttled']:x}")
        ev.append(f"그때 추론 {first_uv['fps1']}+{first_uv['fps2']}장/초, 추정 소비 {first_uv['pmic_w']}W")
        return ("사용 불가", f"{first_uv['phase']} 구간에서 Pi가 전압 부족을 기록함", cable, ev)

    lost = end is None and current_boot and meta.get("boot_id") and current_boot != meta.get("boot_id")
    last = rows[-1]
    if end is None and not current_boot:
        return ("재시험", "끝 표시(END)가 없는데 지금 부팅 ID를 몰라 전원 꺼짐인지 알 수 없음",
                "report는 시험한 그 Pi에서 돌려야 합니다.", ev)
    if end is None and not lost:
        return ("재시험", "프로그램만 멈춤 (Pi는 계속 켜져 있었음)",
                "SSH 창을 닫았거나 프로그램이 죽었습니다. tmux 안에서 다시 시험하세요.", ev)
    runtime = None
    if lost:
        ev.append(f"끝 표시 없이 끊긴 뒤 Pi가 다시 부팅됨. 마지막 기록: {at(last)}")
        if not (drain and last["phase"] == DRAIN):
            return ("사용 불가", f"{last['phase']} 구간에서 전원이 꺼짐",
                    "배터리가 전원을 끊었습니다(일부러 뽑지 않았다면). 대기 구간이면 전류가 적을 때 꺼지는 '자동 꺼짐' 배터리일 수 있습니다. " + cable, ev)
        runtime = last["t"]
    if drain and first_uv:  # 방전 끝 무렵 전압이 처지면 그 순간까지만 쓸 수 있는 시간으로 봄
        runtime = first_uv["t"]
        ev.append(f"방전 중 전압 부족 시작: {at(first_uv)} → 여기까지를 실측 시간으로 봄")
    if runtime is not None and runtime < need:
        return ("사용 불가", f"실측 {fmt_t(runtime)}로 필요한 {fmt_t(need)}보다 짧음",
                "용량이 더 큰 배터리가 필요합니다.", ev, runtime)
    # 아래 검사는 '쓸 수 있던 시간' 안의 기록만 본다 (방전 끝 무렵 전압이 처지며 생기는 일은 빼고)
    valid = [r for r in rows if runtime is None or r["t"] < runtime]

    # 3. 배터리 탓인지 모르는 문제 → 재시험
    if not std or std[-1]["t"] < STD_SECONDS - 3 * EVERY:
        return ("재시험", f"시험을 다 마치지 못함 ({std[-1]['phase'] if std else '-'} 구간에서 멈춤, 끝 이유: {end or '-'})",
                "기본 15분(대기 2 · 평상 8 · 최대 5)을 끝까지 돌려야 판정할 수 있습니다.", ev)
    missing = sum(1 for r in std if r["throttled"] is None or r["ext5v"] is None)
    if missing > len(std) * 0.1:
        return ("재시험", f"측정 실패가 많음 ({missing}/{len(std)}줄)", "vcgencmd가 응답하지 않았습니다. check로 확인하고 다시 시험하세요.", ev)
    for name, n in [(p[0], p[2]) for p in PHASES[1:]] + [(DRAIN, 1)]:
        loaded = [r for r in valid if r["phase"] == name and r["t"] >= phase_start(name) + WARMUP]
        if name == DRAIN and not loaded:
            continue
        dead = [r for r in loaded if r["alive"] < n]
        if dead:
            return ("재시험", f"{name} 구간에서 추론 프로세스가 멈춤 ({at(dead[0])})",
                    "부하가 덜 걸려 배터리가 실제보다 좋아 보일 수 있습니다. 화면의 오류를 확인하고 다시 시험하세요.", ev)
        fps = [r["fps1"] + r["fps2"] for r in loaded]
        if not fps or sum(fps) / len(fps) <= 0 or (n == 2 and not any(r["fps2"] > 0 for r in loaded)):
            return ("재시험", f"{name} 구간에서 추론이 돌지 않음 (부하가 안 걸림)", "모델과 ultralytics 설치를 확인하고 다시 시험하세요.", ev)
    # 전압 부족 없이 속도를 낮췄다면 열 때문 (전압 부족은 위에서 이미 걸러짐)
    hot = [r for r in valid if (r["temp"] or 0) >= HOT_C
           or ((r["throttled"] or 0) & THROTTLED and not (r["throttled"] or 0) & UNDERVOLT)]
    if hot:
        ev.append(f"발열: {at(hot[0])}, get_throttled=0x{(hot[0]['throttled'] or 0):x}")
        return ("재시험", "Pi가 뜨거워져 속도를 낮춤 (배터리 문제 아님)",
                "Pi가 느려지면 전기를 덜 써서 배터리가 실제보다 좋아 보일 수 있습니다. 쿨러를 확인하고 다시 시험하세요.", ev)
    gone = [r for r in valid if not r["xiao"]]
    if gone:
        ev.append(f"XIAO 끊김: {at(gone[0])}")
        return ("재시험", "XIAO 카메라가 USB에서 사라짐 (전압 부족 기록은 없음)",
                "케이블을 다시 꽂고 재시험하세요. 또 끊기면 Pi USB 전원 부족(3A 전원일 때 USB 합계 0.6A 제한)을 의심합니다.", ev)

    if adapter:
        return ("기준 완료", "어댑터 기준값을 잼", "보조배터리마다 재부팅하며 시험하세요.", ev)
    return ("사용 가능", "전 구간에서 전압 부족·꺼짐 없음", "", ev, runtime)


def phase_avg(rows, phase, key):
    vals = [r[key] for r in rows if r["phase"] == phase and r[key] is not None and r["t"] >= phase_start(phase) + WARMUP]
    return sum(vals) / len(vals) if vals else None


def energy_wh(rows, until):
    """PMIC 추정 전력을 시간으로 적분 (Wh)."""
    e, prev = 0.0, 0.0
    for r in rows:
        if r["t"] > until:
            break
        if r["pmic_w"]:
            e += r["pmic_w"] * (r["t"] - prev) / 3600
        prev = r["t"]
    return e


def battery_wh(meta):
    """(정격 Wh, 효율 범위). 5V 정격용량이 있으면 그 값이 곧 꺼내 쓸 수 있는 양."""
    if meta.get("rated5v"):
        return float(meta["rated5v"]) * 5 / 1000, (1.0, 1.0)
    if meta.get("capacity"):
        return float(meta["capacity"]) * 3.7 / 1000, EFF
    return None, None


def calibration(all_runs, serial, current_boot):
    """같은 Pi의 drain 실측 회차로 'PMIC 추정 W → 실제 소모' 비율 k를 구함. 없으면 None."""
    ks = []
    for o in all_runs:
        m = o["meta"]
        if m.get("drain") != "1" or m.get("serial") != serial:
            continue
        verdict, why, _, _, runtime = judge(o, all_runs, current_boot)
        wh, eff = battery_wh(m)
        # 끝까지 잰 회차만 (실측이 짧아 불가가 된 배터리도 소모량 측정으로는 유효)
        if runtime is None or not wh or not (verdict == "사용 가능" or why.startswith("실측")):
            continue
        pm = energy_wh(o["rows"], runtime)
        mid = 1.0 if eff == (1.0, 1.0) else EFF_MID
        if pm > 0:
            k = wh * mid / pm
            if 0.8 <= k <= 3.0:  # 밖이면 측정이 이상한 것이므로 쓰지 않음
                ks.append(k)
    return sum(ks) / len(ks) if ks else None


def estimate(run, k):
    """→ (문장, 방법). 평상·최대 부하에서 몇 분 가는지 범위로."""
    meta, rows = run["meta"], run["rows"]
    wh, eff = battery_wh(meta)
    if not wh:
        return "용량(--capacity 또는 --rated5v)을 넣지 않아 계산하지 않음", "-"
    p_norm, p_max = phase_avg(rows, "평상", "pmic_w"), phase_avg(rows, "최대", "pmic_w")
    if not p_norm or not p_max:
        return "평상·최대 구간의 전력 기록이 없어 계산하지 않음", "-"
    if meta.get("watts"):
        w_norm = float(meta["watts"])
        w_max, how = w_norm * p_max / p_norm, "측정기"
    elif k:
        w_norm, w_max, how = k * p_norm, k * p_max, "보정됨"
    else:
        w_norm, w_max, how = p_norm, p_max, "보정 전"
    rng = lambda w: f"{fmt_t(wh * eff[0] / w * 3600)}~{fmt_t(wh * eff[1] / w * 3600)}" if eff[0] != eff[1] else fmt_t(wh / w * 3600)
    text = f"평상 약 {rng(w_norm)}, 최대 부하 약 {rng(w_max)} ({how}, 평상 {w_norm:.1f}W)"
    if how == "보정 전":
        text += " ※ Pi가 USB로 내주는 전기와 변환 손실이 빠진 값이라 실제는 이보다 짧을 수 있음"
    return text, how


def summarize(run, all_runs, current_boot, k):
    verdict, why, todo, ev, measured = judge(run, all_runs, current_boot)
    meta, rows = run["meta"], run["rows"]
    vmin = min((r["ext5v"] for r in rows if r["ext5v"]), default=None)
    notes = []
    if vmin is not None and vmin < LOW_V:
        notes.append(f"최저 전압 {vmin}V (4.8V 미만, 여유 적음)")
    base = [o for o in all_runs if o is not run and o["meta"].get("kind") == "adapter" and o["meta"].get("serial") == meta.get("serial")
            and o["meta"].get("size") == meta.get("size") and judge(o, all_runs, current_boot)[0] == "기준 완료"]
    fps, ratio = phase_avg(rows, "평상", "fps1"), None
    if base and fps:
        b = phase_avg(base[-1]["rows"], "평상", "fps1")
        ratio = round(fps / b * 100) if b else None
    est, how = estimate(run, k) if verdict == "사용 가능" else ("-", "-")
    return {"verdict": verdict, "why": why, "todo": todo, "ev": ev, "vmin": vmin, "fps": fps, "ratio": ratio,
            "measured": measured, "est": est, "how": how, "notes": notes}


def diagnosis_text(run, s):
    m = run["meta"]
    lines = [f"[{s['verdict']}] {m.get('label')} · 케이블 {m.get('cable') or '-'}",
             f"이유: {s['why']}", ""]
    if s["todo"]:
        lines += [f"다음에 할 일: {s['todo']}", ""]
    if s["measured"] is not None:
        lines.append(f"실측 사용 시간: {fmt_t(s['measured'])} (끝까지 방전, 평상 부하 위주)")
    if s["est"] != "-":
        lines.append(f"예상 사용 시간: {s['est']}")
    lines += [f"최저 전압: {s['vmin']}V" if s["vmin"] else "최저 전압: -",
              f"평상 추론: {s['fps']:.1f}장/초" + (f" (어댑터 대비 {s['ratio']}%)" if s["ratio"] else "") if s["fps"] else "평상 추론: -"]
    lines += [f"참고: {n}" for n in s["notes"]]
    lines += ["", "근거:"] + [f"- {e}" for e in s["ev"]]
    lines += ["", "시험 조건:"] + [f"- {k}: {v}" for k, v in m.items()]
    lines += ["", "판정은 '배터리 + 케이블' 조합 기준입니다. 판정 규칙은 tools/pi-power-test/README.md."]
    return "\n".join(lines) + "\n"


def diag_path(csv_path):
    return csv_path.with_name(csv_path.stem + "_진단.txt")


def load_all():
    runs = [read_run(p) for p in sorted(RESULTS.glob("*.csv"))]
    return sorted(runs, key=lambda r: r["meta"].get("started", ""))


def report(write=True):
    runs, cur = load_all(), boot_id()
    if not runs:
        print("results/에 시험 기록이 없습니다.")
        return
    out = []
    for r in runs:
        k = calibration(runs, r["meta"].get("serial"), cur)
        s = summarize(r, runs, cur, k)
        if write:
            diag_path(r["path"]).write_text(diagnosis_text(r, s), encoding="utf-8")
        time_txt = f"실측 {fmt_t(s['measured'])}" if s["measured"] is not None else s["est"]
        out.append((r["meta"].get("label", "?"), r["meta"].get("cable", "-"), s["verdict"], s["why"],
                    f"{s['vmin']}V" if s["vmin"] else "-", f"{s['ratio']}%" if s["ratio"] else "-", time_txt))
    print("배터리 | 케이블 | 판정 | 이유 | 최저 전압 | 어댑터 대비 속도 | 사용 시간")
    for row in out:
        print(" | ".join(str(c) for c in row))
    print(f"\n진단서: {RESULTS / '*_진단.txt'}")


# ── 시험 실행 ────────────────────────────────────────────────────────────
class Stop(Exception):
    pass


def safe_label(s):
    return re.sub(r"[^\w가-힣-]", "_", s)


def fail_diag(label, problems):
    RESULTS.mkdir(exist_ok=True)
    p = RESULTS / f"{safe_label(label)}_{dt.datetime.now():%Y%m%d-%H%M%S}_진단.txt"
    body = "[시험 못 함] 준비 단계에서 멈춤\n\n" + "\n".join(f"- {x}" for x in problems) + \
           "\n\n배터리 판정과는 상관없습니다. 위 문제를 고치고 check를 다시 돌리세요.\n"
    p.write_text(body, encoding="utf-8")
    return p


def cmd_check(a):
    problems, info = preflight(a.model, a.size)
    for k, v in info.items():
        print(f"  {k}: {v}")
    if problems:
        print("\n점검 실패:\n" + "\n".join(f"- {x}" for x in problems))
        print(f"진단서: {fail_diag(a.label or 'check', problems)}")
        return 1
    print("\n점검 통과. 시험을 시작해도 됩니다.")
    return 0


def cmd_run(a):
    if not a.adapter and a.capacity is None and a.rated5v is None:  # --capacity 0(모름)은 통과
        print("보조배터리는 --capacity(mAh) 또는 --rated5v(5V 정격 mAh)를 넣어야 사용 시간을 계산합니다. 모르면 --capacity 0")
        return 2
    problems, info = preflight(a.model, a.size)
    if problems:
        print("점검 실패로 시작하지 않습니다:\n" + "\n".join(f"- {x}" for x in problems))
        print(f"진단서: {fail_diag(a.label, problems)}")
        return 1
    started = dt.datetime.now()
    path = RESULTS / f"{safe_label(a.label)}_{started:%Y%m%d-%H%M%S}.csv"
    meta = {"label": a.label, "kind": "adapter" if a.adapter else "battery", "cable": a.cable,
            "capacity": a.capacity or "", "rated5v": a.rated5v or "", "watts": a.watts or "", "need_min": a.need,
            "drain": "1" if a.drain else "0", "model": a.model, "size": a.size, "note": a.note,
            "started": started.isoformat(timespec="seconds"), **info}
    def on_signal(n, _frame):
        raise Stop(f"신호 {signal.Signals(n).name}")

    for sig in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), on_signal)
    load = Load(a.model, a.size)
    f = open(path, "w", encoding="utf-8", newline="")
    for k, v in meta.items():
        f.write(f"# {k}={v}\n")
    w = csv.DictWriter(f, COLS)
    w.writeheader()
    end = "완료"
    plan = PHASES + ([(DRAIN, None, 1)] if a.drain else [])
    print(f"기록: {path}\n중간에 멈추려면 Ctrl+C (전원을 뽑지 마세요: '전원 꺼짐'으로 판정됩니다)")
    t0 = time.monotonic()
    try:
        prev_counts, prev_t, tick = load.counts(), t0, 0
        for name, sec, n in plan:
            load.set(n)
            prev_counts = load.counts()
            phase_end = (time.monotonic() + sec) if sec else float("inf")
            print(f"[{fmt_t(time.monotonic() - t0)}] {name} 구간 시작 (추론 {n}개)")
            while time.monotonic() < phase_end:
                tick += 1
                time.sleep(max(0.0, t0 + tick * EVERY - time.monotonic()))
                now, counts = time.monotonic(), load.counts()
                fps = [(c - p) / (now - prev_t) for c, p in zip(counts, prev_counts)]
                prev_counts, prev_t = counts, now
                row = {"t": round(now - t0, 1), "phase": name, "fps1": round(fps[0], 2), "fps2": round(fps[1], 2),
                       "alive": load.alive(), **sample()}
                w.writerow(row)
                f.flush()
                os.fsync(f.fileno())
                if tick % 15 == 0:
                    print(f"  {fmt_t(row['t'])} {name} {row['ext5v']}V {row['temp']}℃ throttled={row['throttled']} "
                          f"추론 {row['fps1']}+{row['fps2']}장/초 XIAO={'O' if row['xiao'] else 'X'}")
    except KeyboardInterrupt:
        end = "중단(Ctrl+C)"
    except Stop as e:
        end = str(e)
    except Exception as e:
        end = f"오류: {e}"
    finally:
        load.set(0)
        f.write(f"END,{end}\n")
        f.close()
    runs = load_all()
    run = next(r for r in runs if r["path"] == path)
    s = summarize(run, runs, boot_id(), calibration(runs, info.get("serial"), boot_id()))
    diag_path(path).write_text(diagnosis_text(run, s), encoding="utf-8")
    print("\n" + diagnosis_text(run, s))
    print(f"진단서: {diag_path(path)}")
    return 0


# ── 셀프 체크 ────────────────────────────────────────────────────────────
def fake_run(kind="battery", drain=False, end="완료", boot="b1", label="X", started="2026-11-20T10:00:00",
             upto=STD_SECONDS, start_thr="0x0", serial="s1", capacity="5000", watts="", **patch):
    """가짜 회차. patch: {'uv_at': 초, 'hot_at': 초, 'xiao_off_at': 초, 'dead_at': 초, 'nofps': 구간, 'gap': True}"""
    rows, t = [], EVERY
    while t <= upto:
        phase = next((n for n, s, _ in PHASES if t <= phase_start(n) + s), DRAIN)
        n = need_procs(phase)
        thr = 0x50005 if patch.get("uv_at") and t >= patch["uv_at"] else 0
        thr |= 0x40004 if patch.get("hot_at") and t >= patch["hot_at"] else 0
        fps1 = 0 if patch.get("nofps") == phase else (5.0 if n >= 1 else 0)
        rows.append({"t": t, "phase": phase, "ext5v": 5.05 if n < 2 else 4.95, "throttled": thr,
                     "temp": 55.0, "arm_mhz": 2400, "pmic_w": 3.0 if n == 0 else 4.0 * n,
                     "fps1": fps1, "fps2": 4.0 if n == 2 else 0.0,
                     "alive": n - (1 if patch.get("dead_at") and t >= patch["dead_at"] and n else 0),
                     "xiao": 0 if patch.get("xiao_off_at") and t >= patch["xiao_off_at"] else 1})
        t += EVERY
    if patch.get("gap"):
        for r in rows[::3]:
            r["throttled"] = None
    meta = {"label": label, "kind": kind, "drain": "1" if drain else "0", "boot_id": boot, "started": started,
            "start_throttled": start_thr, "need_min": "60", "serial": serial, "capacity": capacity,
            "rated5v": "", "watts": watts, "size": "320", "cable": "c"}
    return {"path": Path(f"{label}.csv"), "meta": meta, "rows": rows, "end": end}


def selfcheck():
    # 1. vcgencmd 출력 읽기
    assert parse_throttled("throttled=0x50005\n") == 0x50005
    assert parse_temp("temp=48.3'C\n") == 48.3
    assert parse_clock("frequency(0)=2400023808\n") > 2399
    pmic = ("     VDD_CORE_A current(7)=2.00000000A\n     VDD_CORE_V volt(15)=0.80000000V\n"
            "      3V3_SYS_A current(1)=0.10000000A\n      3V3_SYS_V volt(9)=3.30000000V\n"
            "     EXT5V_V volt(24)=5.12000000V\n     HDMI_V volt(23)=5.10000000V\n")
    v, w = parse_pmic(pmic)
    assert v == 5.12 and abs(w - (1.6 + 0.33)) < 1e-9, (v, w)

    # 2. 판정 표의 줄마다
    cur = "b9"  # 지금 부팅 ID (회차들과 다르면 그 사이 재부팅된 것)
    cases = [
        ("정상", fake_run(), "사용 가능"),
        ("어댑터 기준", fake_run(kind="adapter"), "기준 완료"),
        ("어댑터인데 전압 부족", fake_run(kind="adapter", uv_at=300), "시험 무효"),
        ("최대에서 전압 부족", fake_run(uv_at=700), "사용 불가"),
        ("부팅 때 이미 전압 부족", fake_run(start_thr="0x50000"), "사용 불가"),
        ("전원 꺼짐 + 재부팅", fake_run(end=None, upto=400), "사용 불가"),
        ("대기 중 꺼짐(자동 꺼짐)", fake_run(end=None, upto=60), "사용 불가"),
        ("프로그램만 죽음", fake_run(end=None, upto=400, boot=cur), "재시험"),
        ("중간 정지", fake_run(end="중단(Ctrl+C)", upto=400), "재시험"),
        ("추론 안 돎", fake_run(nofps="평상"), "재시험"),
        ("추론 프로세스 죽음", fake_run(dead_at=500), "재시험"),
        ("발열", fake_run(hot_at=600), "재시험"),
        ("XIAO 끊김", fake_run(xiao_off_at=500), "재시험"),
        ("측정 실패 많음", fake_run(gap=True), "재시험"),
        ("drain 충분", fake_run(drain=True, end=None, upto=5400), "사용 가능"),
        ("drain 부족", fake_run(drain=True, end=None, upto=2400), "사용 불가"),
        ("drain 끝 무렵 전압 처짐 → 그 시점까지", fake_run(drain=True, end=None, upto=5400, uv_at=3000), "사용 불가"),
        ("drain 중 표준 구간 전압 부족", fake_run(drain=True, end=None, upto=5400, uv_at=500), "사용 불가"),
        ("drain 끝 전압 처짐은 발열로 안 봄", fake_run(drain=True, end=None, upto=5400, uv_at=5000), "사용 가능"),
        ("drain 중 추론 멈춤 → 시간이 부풀려짐", fake_run(drain=True, end=None, upto=5400, dead_at=2000), "재시험"),
        ("drain 중 XIAO 끊김", fake_run(drain=True, end=None, upto=5400, xiao_off_at=3000), "재시험"),
        ("drain 중 Ctrl+C (시간 미확인)", fake_run(drain=True, end="중단(Ctrl+C)", upto=2000), "사용 가능"),
    ]
    for name, run, want in cases:
        got = judge(run, [run], cur)
        assert got[0] == want, f"{name}: {got[0]} ≠ {want} ({got[1]})"
    a = fake_run(label="A", started="2026-11-20T10:00:00")
    b = fake_run(label="B", started="2026-11-20T10:20:00")  # 같은 부팅 ID인데 이름이 다름
    assert judge(a, [a, b], cur)[0] == "사용 가능" and judge(b, [a, b], cur)[0] == "재시험"
    assert judge(fake_run(end=None), [], None)[0] == "재시험"  # 부팅 ID를 모르면 꺼짐으로 단정하지 않음

    # 3. 지속 시간: 측정기 > 보정 > 보정 전
    run = fake_run()
    assert estimate(run, None)[1] == "보정 전" and "실제는 이보다 짧을" in estimate(run, None)[0]
    assert estimate(run, 1.5)[1] == "보정됨"
    assert estimate(fake_run(watts="6"), 1.5)[1] == "측정기"
    assert "계산하지 않음" in estimate(fake_run(capacity=""), None)[0]
    d = fake_run(drain=True, end=None, upto=5400, label="D", boot="b2")
    k = calibration([d], "s1", cur)
    pm = energy_wh(d["rows"], 5400)
    assert k and abs(k - 5000 * 3.7 / 1000 * EFF_MID / pm) < 1e-9, k
    assert calibration([d], "다른Pi", cur) is None
    short = fake_run(drain=True, end=None, upto=2400, label="S", boot="b3", capacity="1500")  # 실측 부족 배터리도 보정에는 씀
    assert calibration([short], "s1", cur)
    assert judge(d, [d], cur)[4] == 5400 and judge(short, [short], cur)[4] == 2400

    # 4. CSV 왕복 + 진단서
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "x.csv"
        r = fake_run(uv_at=700)
        with open(p, "w", encoding="utf-8", newline="") as f:
            for key, val in r["meta"].items():
                f.write(f"# {key}={val}\n")
            wr = csv.DictWriter(f, COLS)
            wr.writeheader()
            for row in r["rows"]:
                wr.writerow({**row, "throttled": f"0x{row['throttled']:x}"})
            f.write("123.0,평상,5.0")  # 꺼지며 반쯤 쓰인 줄
        back = read_run(p)
        assert len(back["rows"]) == len(r["rows"]) and back["end"] is None
        back["end"] = "완료"
        s = summarize(back, [back], cur, None)
        text = diagnosis_text(back, s)
        assert s["verdict"] == "사용 불가" and "처음 전압 부족" in text and "다음에 할 일" in text, text
    print("selfcheck ok")


def main():
    ap = argparse.ArgumentParser(description="Pi 5 보조배터리 시험")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run"):
        p = sub.add_parser(name)
        p.add_argument("--model", default="yolo11n.pt", help="추론 모델 (기본: 안전모 모델과 같은 nano 크기)")
        p.add_argument("--size", type=int, default=320, help="추론 입력 크기 (320 또는 640)")
        p.add_argument("--label", required=name == "run", help="배터리 이름 (예: 명수30000)")
    run = sub.choices["run"]
    run.add_argument("--adapter", action="store_true", help="어댑터 기준 회차")
    run.add_argument("--capacity", type=int, help="배터리 용량 mAh (겉에 적힌 큰 숫자)")
    run.add_argument("--rated5v", type=int, help="5V 정격용량 mAh (적혀 있으면 더 정확)")
    run.add_argument("--watts", type=float, help="USB-C 측정기로 본 평상 구간 W")
    run.add_argument("--cable", default="", help="케이블 설명 (예: C to C 30cm)")
    run.add_argument("--note", default="", help="메모 (쿨러, 장소 등)")
    run.add_argument("--need", type=int, default=60, help="필요한 사용 시간(분), drain 판정 기준")
    run.add_argument("--drain", action="store_true", help="기본 15분 뒤 꺼질 때까지 이어서 실측")
    sub.add_parser("report")
    sub.add_parser("selfcheck")
    a = ap.parse_args()
    if a.cmd == "selfcheck":
        selfcheck()
        return 0
    if a.cmd == "report":
        report()
        return 0
    return cmd_check(a) if a.cmd == "check" else cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
