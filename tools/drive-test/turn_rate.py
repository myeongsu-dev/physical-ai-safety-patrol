"""걷는 속도(초음파) · 조향별 도는 속도(자이로) 재기 (Pi ~/psp-dev에서, ssh -tt로 실행)
  python ~/psp-tools/turn_rate.py speed   벽을 보고 앞뒤로 걸어 초음파 거리 변화로 속도 (보폭 40 · 60 · 80)
  python ~/psp-tools/turn_rate.py turn    조향별 도는 속도
안전: 앞으로 걷는 중 초음파 STOP_CM 안이면 그 회차를 바로 멈춤(보드도 10 cm에서 멈춤),
      /tmp/walk.stop이 생기거나 ssh가 끊기면(SIGHUP) 로봇을 세우고 끝냄. 원 기록은 ~/test_logs/speed · turn/날짜_시각.log"""
import os
import signal
import sys
import time

sys.path.insert(0, ".")
from edge import hwcheck
from edge.dog_link import BoardState

STOP_CM = 20.0
SKIP_S = 0.8  # 걸음이 바뀌는 첫 구간은 빼고 기울기를 구함
STOP = "/tmp/walk.stop"
mode = sys.argv[1] if len(sys.argv) > 1 else "turn"
if mode == "speed":
    RUNS = []
    for s in (40, 60, 80, 60):
        RUNS += [(s, 0, 4000), (-s, 0, 4000)]
elif mode == "rest":  # 벽에 막혀 못 잰 것만
    RUNS = [(60, -30, 3000), (60, 40, 3000), (60, -40, 3000), (60, 0, 3000), (30, 40, 3000), (30, -40, 3000)]
else:
    RUNS = [(0, 25, 3000), (0, -25, 3000), (0, 40, 3000), (0, -40, 3000)]
    for a in (10, 20, 30, 40):
        RUNS += [(60, a, 3000), (60, -a, 3000)]
    RUNS += [(60, 0, 3000), (30, 40, 3000), (30, -40, 3000)]
if os.path.exists(STOP):
    os.remove(STOP)


def hup(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGHUP, hup)
signal.signal(signal.SIGTERM, hup)


class A:
    port = None


link = hwcheck._open_dog(A())
st = BoardState("yaw,sonar,bat")
link.send("T", 50, "yaw,sonar,bat")
hwcheck._read(link, st, 0.5)
LOG = os.path.expanduser(time.strftime("~/test_logs/%s/%%Y%%m%%d_%%H%%M%%S.log" % ("speed" if mode == "speed" else "turn")))
os.makedirs(os.path.dirname(LOG), exist_ok=True)
raw = open(LOG, "w", buffering=1)


def watch(secs, rec, forward):
    end, last_v = time.monotonic() + secs, 0
    while time.monotonic() < end:
        if os.path.exists(STOP):
            raise KeyboardInterrupt
        if time.monotonic() - last_v > 0.5:  # 보드 2초 감시에 걸리지 않게
            link.send("V")
            last_v = time.monotonic()
        p = hwcheck._read(link, st, 0.05, until=lambda p: p[0] in ("D", "E"))
        if not p:
            continue
        if p[0] == "E" and len(p) > 2 and p[2] == "stop":
            return "보드 정지 %s cm" % p[3]
        v = st.values
        if p[0] == "D" and v.get("yaw") is not None:
            rec.append((time.monotonic(), v["yaw"], v.get("sonar", -1)))
            if forward and 0 <= v.get("sonar", -1) < STOP_CM:
                return "앞 %.1f cm" % v["sonar"]
    return ""


def slope(pts):
    n = len(pts)
    if n < 4:
        return float("nan")
    mt, my = sum(t for t, _ in pts) / n, sum(y for _, y in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (y - my) for t, y in pts) / den if den else float("nan")


try:
    print("배터리 %s mV" % st.values.get("bat"), flush=True)
    for stride, angle, ms in RUNS:
        rec = []
        t0 = time.monotonic()
        link.send("M", stride, angle, ms)
        why = watch(ms / 1000 + 0.2, rec, stride > 0)
        link.send("S")
        for t, y, d in rec:
            raw.write("%s %d %d %.2f %.1f %.1f\n" % (mode, stride, angle, t - t0, y, d))
        win = [(t - t0, y, d) for t, y, d in rec if SKIP_S <= t - t0 <= ms / 1000]
        yaw_rate = slope([(t, y) for t, y, _ in win])
        sd = [(t, d) for t, _, d in win if 0 <= d < 300]
        pairs = sorted((d2 - d1) / (t2 - t1) for i, (t1, d1) in enumerate(sd) for t2, d2 in sd[i + 1:] if t2 - t1 > 0.5)
        sonar_rate = pairs[len(pairs) // 2] if pairs else float("nan")  # 튀는 값에 안 흔들리게 두 점 기울기들의 가운데 값
        print("보폭 %+4d 조향 %+4d → 돎 %+6.1f °/s · 초음파 %+6.1f cm/s %s" % (stride, angle, yaw_rate, sonar_rate, why), flush=True)
        watch(1.0, [], False)
except KeyboardInterrupt:
    print("멈춤", flush=True)
finally:
    link.send("S")
    hwcheck._read(link, st, 0.5)
    link.send("T", 0)
    link.close()
    raw.close()
