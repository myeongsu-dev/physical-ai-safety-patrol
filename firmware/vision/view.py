"""비전 모듈 영상 보기 (patrol_cam 펌웨어, 디버깅용). Pi·PC에서 켜 둔 동안만 브라우저로 본다.

  python firmware/vision/view.py [포트] [--url URL] [--thr N] [--http 8090]
  → 같은 네트워크의 브라우저에서 http://<이 기기 주소>:8090
  python firmware/vision/view.py [포트] --wifi   # 모듈에 Wi-Fi 저장 (USB로 한 번)
  화면은 web/view.html. "카메라 각도 맞추기 → 시작"을 누른 동안만 각도를 재고, MechDog 옆모습(web/mechdog.glb,
  CAD 빌드 결과 복사본)의 카메라를 같이 돌린다. three.js도 web/three/에 있어 인터넷 없이 돈다.

영상은 USB 포트가 있으면 USB, 없으면 Wi-Fi(모듈의 /stream), 둘 다 없으면 붙을 때까지 기다린다. 꽂고 뽑는 대로 바뀐다.
포트를 안 주면 .env의 VISION_PORT, URL은 VISION_URL(없으면 http://patrol-cam.local/stream).
켜는 동안만 영상을 보내고, 끄면 멈춘다. 검은 선 찾기는 영상과 상관없이 계속 돈다.
--thr로 검정 기준 밝기를 바꾸면 모듈에 저장된다(USB일 때만).
"""
import argparse
import getpass
import json
import math
import os
import sys
import urllib.request
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from edge import config  # noqa: E402
from edge.camera import split_jpegs  # noqa: E402

BAUD = 460800  # patrol_cam.ino와 같게
WEB = Path(__file__).resolve().parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".glb": "model/gltf-binary"}
latest = {"jpg": None, "n": 0, "fps": 0.0, "src": "", "calib": False, "kind": "", "title": "", "text": "", "deg": None, "lens_cm": None}

# ---- 출발 전 카메라 각도 판정 (곧은 테이프 한 줄을 가운데에 두고) ----
# 바닥의 곧은 테이프 폭 w는 화면 줄 v(가운데 0, 아래 +)에 대해 직선 w = k·(v - v_h)이고, v_h는 지평선 줄.
# 광축이 수평에서 아래로 꺾인 각 θ = atan(-v_h / f) → 테이프 폭·렌즈 높이와 상관없이 각도만 나옴.
# f = 548 px(320x240, 세로 시야 25° · 가로 33°): 바닥에 자를 놓고 두 자세(37° · 26°)로 찍어 1 cm 눈금 12개와
# 테이프 폭(24 mm)을 함께 맞춤(줄 오차 1.5 px 안, 렌즈 높이 4.1 · 3.7 cm). 목표 20° ± 1°(가만히 있을 때 판정 흔들림 0.1°): 가운데 구역이 렌즈 앞
# 약 11 cm · 바닥 폭 약 6.5 cm, 화면 맨 위가 지평선보다 8° 아래. 기록은 08_memo/실험_MechDog_곡선주행.md "카메라 각도"
AIM_F = 548
AIM_DEG = (19, 21)
AIM_TAPE_CM = 1.85  # 테이프 폭 (2026-11-26 밤 새로 그린 길 18~19 mm, 전에는 24 mm). 기울기 k = 폭·cosθ / 렌즈 높이 → 렌즈 높이를 재서 "바닥 몇 cm 앞이 보이는지"에만 씀
AIM_DARK_MAX = 90  # 줄에서 가장 어두운 값이 이보다 밝으면 테이프 없음 (테이프 6~22, 그늘진 바닥 111~123, 2026-11-26)
AIM_ROWS = [y for y in range(4, 156) if abs(y - 80) > 3]  # 먼·가운데 구역 줄 (회색 경계줄 80 · 160은 피함)


def tape_rows(gray):
    """줄마다 가장 긴 검은 연속 구간 폭 → (v, w) 목록. 테이프가 없거나 화면 끝에 닿은 줄은 뺌"""
    import numpy as np

    out = []
    for y in AIM_ROWS:
        row = gray[y].astype(int)
        lo, hi = row.min(), row.max()
        if lo > AIM_DARK_MAX or hi - lo < 50:  # 50은 펌웨어 CONTRAST_MIN과 같음
            continue
        d = np.flatnonzero(row < (lo + hi) // 2)
        run = max(np.split(d, np.flatnonzero(np.diff(d) > 2) + 1), key=len)
        if run[0] > 0 and run[-1] < len(row) - 1:
            out.append((y - (gray.shape[0] - 1) / 2, len(run)))
    return out


def pitch(rows):
    """(v, w) 목록 → (각도°, 직선에서 벗어난 정도 px, 렌즈 높이 cm). 줄이 모자라거나 폭이 위로 갈수록 넓어지면 None"""
    import numpy as np

    if len(rows) < 40:
        return None
    v, w = np.array(rows, float).T
    keep = np.ones(len(v), bool)
    for _ in range(2):  # 찢어진 테이프 끝 · 그늘 같은 튀는 줄을 빼고 다시 맞춤
        k, b = np.polyfit(v[keep], w[keep], 1)
        err = np.abs(w - (k * v + b))
        keep = err <= max(3.0, 2.5 * np.median(err[keep]))
    if k <= 0:
        return None
    th = math.atan(b / k / AIM_F)
    return math.degrees(th), float(np.std(w[keep] - (k * v[keep] + b))), AIM_TAPE_CM * math.cos(th) / k


def aim(p):
    """pitch() 결과 → (종류, 제목, 설명)"""
    if p is None:
        return "none", "테이프가 안 보임", "곧은 테이프 위 가운데에 세우기"
    deg, res = p[:2]
    if res > 4:
        return "curve", "곧은 테이프가 아님", "곧은 곳에 세우기 (직선에서 %.0f px 벗어남)" % res
    # 모듈은 경첩에 매달려 있고, 아래 끝을 뒤로 접을수록 렌즈가 아래를 봄 (CAD bCam · 사진 2026-11-26)
    nums = "지금 %.1f°, 목표 %d~%d°, 가운데 %d°" % (deg, *AIM_DEG, sum(AIM_DEG) / 2)
    if deg < AIM_DEG[0]:
        return "down", "너무 멀리 봄", "모듈 아래 끝을 뒤로 약 %.0f° 접기 (%s)" % (sum(AIM_DEG) / 2 - deg, nums)
    if deg > AIM_DEG[1]:
        return "up", "발 앞만 봄", "모듈 아래 끝을 앞으로 약 %.0f° 세우기 (%s)" % (deg - sum(AIM_DEG) / 2, nums)
    return "good", "좋음", nums


def measure(jpg):
    """JPEG 한 장 → latest의 판정 칸 (각도 맞추기 중에만 부름)"""
    import cv2
    import numpy as np

    gray = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return
    p = pitch(tape_rows(gray))
    latest["kind"], latest["title"], latest["text"] = aim(p)
    ok = latest["kind"] in ("good", "up", "down")
    latest["deg"], latest["lens_cm"] = (round(p[0], 1), round(p[2], 2)) if ok else (None, None)


def selftest():
    import numpy as np

    def frame(deg, width=AIM_TAPE_CM * 10, h=4.0):  # 각도 deg로 내려다본 곧은 테이프 화면 (바늘구멍 모형)
        g = np.full((240, 320), 210, np.uint8)
        th = math.radians(deg)
        for y in range(240):
            v = y - 119.5
            a = th + math.atan(v / AIM_F)
            if a <= 0:
                continue
            z = h / math.tan(a) * math.cos(th) + h * math.sin(th)
            half = AIM_F * width / 10 / z / 2
            g[y, max(1, int(160 - half)):min(318, int(160 + half))] = 20
        return g

    for deg, want in ((20, "good"), (12, "down"), (30, "up")):
        got = aim(pitch(tape_rows(frame(deg))))
        assert got[0] == want and "지금 %d." % deg in got[2], (deg, got)
    assert abs(pitch(tape_rows(frame(20)))[2] - 4.0) < 0.15      # 그린 렌즈 높이 4.0 cm가 다시 나옴
    g = frame(20)
    g[30:50, :] = 210                                     # 먼 구역 일부가 비어도 맞춤
    assert aim(pitch(tape_rows(g)))[0] == "good"
    g = np.full((240, 320), 200, np.uint8)
    g[:, 140:180] = 115                                   # 그늘진 바닥만 있는 화면
    assert aim(pitch(tape_rows(g)))[0] == "none"
    print("selftest ok")


def open_port(port):
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, BAUD, 0.1
    # 모든 OS에서 열기 전에 끔. Linux에서 커널이 켠 그대로 두면 열려 있는 동안 모듈이 리셋에 묶여 선 찾기·영상이 멈춤
    # (2026-11-26 Pi 5 실기). 대신 Linux에서는 열 때 한 번 재시작하므로 켜질 때까지 기다림
    s.dtr = s.rts = False
    s.open()
    if sys.platform != "win32":
        time.sleep(2)
        s.reset_input_buffer()
    return s


def reader(read):
    """read()가 주는 바이트에서 JPEG를 잘라 latest에 넣음. read()가 None이면 끝"""
    buf = b""
    while (chunk := read()) is not None:
        buf += chunk
        frames, buf = split_jpegs(buf)
        for f in frames:
            latest["jpg"], latest["n"] = f, latest["n"] + 1
        if len(buf) > 200_000:  # 깨진 장이 쌓이지 않게
            buf = b""


usb = {"s": None}  # 지금 열린 USB 포트 (끌 때 STREAM 0)


def read_usb(port, thr):
    s = usb["s"] = open_port(port)
    try:
        if thr:
            s.write(b"THR %d\n" % thr)
        s.write(b"STREAM 1\n")
        latest["src"] = "USB"
        last = [time.time(), latest["n"]]

        def read():
            if latest["n"] != last[1]:
                last[:] = [time.time(), latest["n"]]
            elif time.time() - last[0] > 2:  # 모듈이 다시 켜지면(전원 흔들림 · USB 다시 꽂음) 영상이 꺼진 채라 다시 켬
                s.write(b"STREAM 1\n")
                last[0] = time.time()
            return s.read(s.in_waiting or 1)
        reader(read)
    finally:
        usb["s"] = None
        try:
            s.close()
        except OSError:
            pass


def read_wifi(url, port):
    with urllib.request.urlopen(url, timeout=5) as r:
        latest["src"] = "Wi-Fi"
        reader(lambda: None if port and os.path.exists(port) else (r.read1(65536) or None))  # USB가 꽂히면 USB로


def sources(port, url, thr):
    """USB 포트가 있으면 USB, 없으면 Wi-Fi, 둘 다 안 되면 1초 뒤 다시"""
    while True:
        try:
            if port and os.path.exists(port):
                read_usb(port, thr)
            else:
                read_wifi(url, port)
        except OSError:  # 뽑힘 · Wi-Fi 끊김 · 모듈 꺼짐 (serial.SerialException도 OSError)
            pass
        latest["src"] = ""
        time.sleep(1)


def set_wifi(port):
    """USB로 모듈에 Wi-Fi 이름·비밀번호를 저장. 비밀번호는 화면·기록에 남기지 않음"""
    s = open_port(port)
    s.write(b"STREAM 0\nSCAN\n")
    print("모듈에 보이는 Wi-Fi (2.4GHz만, 세기 이름):")
    end, found = time.time() + 8, False
    while time.time() < end:
        ln = s.readline().decode(errors="ignore").strip()
        if ln.startswith("SCAN "):
            print("  " + ln[5:])
            found = True
        elif ln.startswith("INFO") and found:  # SCAN 목록 뒤의 INFO = 목록 끝
            break
    ssid = input("Wi-Fi 이름 (비우면 Wi-Fi 끔): ").strip()
    pw = getpass.getpass("비밀번호: ") if ssid else ""
    s.write(("WIFI %s\t%s\n" % (ssid, pw)).encode())
    end = time.time() + 20
    while time.time() < end:  # 붙었는지 INFO로 확인
        time.sleep(2)
        s.write(b"INFO\n")
        for ln in s.read(s.in_waiting or 1).decode(errors="ignore").splitlines():
            if ln.startswith("INFO") and "wifi=" in ln:
                ip = ln.split("wifi=")[1].split()[0]
                if ip not in ("connecting",):
                    print("Wi-Fi:", ip, "(영상 http://patrol-cam.local/stream)" if ip != "off" else "")
                    return s.close()
    print("20초 안에 못 붙음. 이름·비밀번호(대소문자)와 2.4GHz인지 확인")
    s.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/state", "/calib"):
            if path == "/calib":
                latest["calib"] = query == "on=1"
                latest.update(kind="", title="", text="", deg=None, lens_cm=None)
            st = {k: v for k, v in latest.items() if k != "jpg"}
            st.update(target=AIM_DEG, vfov=round(2 * math.degrees(math.atan(120 / AIM_F)), 1))
            return self.send(json.dumps(st, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if path != "/stream":
            f = (WEB / (path[len("/web/"):] if path.startswith("/web/") else "view.html")).resolve()
            if WEB not in f.parents or not f.is_file():  # web/ 밖은 안 내줌
                return self.send(b"not found", "text/plain", 404)
            return self.send(f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
        self.end_headers()
        seen = -1
        try:
            while True:
                if latest["n"] == seen or latest["jpg"] is None:
                    time.sleep(0.02)
                    continue
                seen, jpg = latest["n"], latest["jpg"]
                self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=config._get("VISION_PORT", None))
    ap.add_argument("--thr", type=int, help="검정 기준 밝기 1~254 (작을수록 더 검은 것만)")
    ap.add_argument("--http", type=int, default=8090)
    ap.add_argument("--url", default=config._get("VISION_URL", "http://patrol-cam.local/stream"), help="Wi-Fi 영상 주소")
    ap.add_argument("--wifi", action="store_true", help="모듈에 Wi-Fi 저장 (USB 필요)")
    ap.add_argument("--selftest", action="store_true", help="각도 판정 자체 확인만 하고 끝냄")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.wifi:
        if not (a.port and os.path.exists(a.port)):
            sys.exit("모듈 USB를 꽂고 포트를 주거나 .env에 VISION_PORT를 적으세요 (찾기: python -m edge.hwcheck ports)")
        return set_wifi(a.port)
    threading.Thread(target=sources, args=(a.port, a.url, a.thr), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.http), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("영상: http://<이 기기 주소>:%d  (끝내기 Ctrl-C)" % a.http)
    try:
        last, tick = 0, 0
        while True:
            time.sleep(0.5)
            if latest["calib"] and latest["jpg"] is not None:
                measure(latest["jpg"])
            tick += 1
            if tick % 4 == 0:
                latest["fps"] = (latest["n"] - last) / 2
                last = latest["n"]
                print("%s 받은 장 %d (초당 %.1f)" % (latest["src"] or "연결 없음", last, latest["fps"])
                      + (" | 각도: %s %s" % (latest["title"], latest["text"]) if latest["calib"] else ""))
    except KeyboardInterrupt:
        pass
    finally:
        s = usb["s"]
        if s:
            try:
                s.write(b"STREAM 0\n")
                time.sleep(0.2)
            except OSError:
                pass


if __name__ == "__main__":
    main()
