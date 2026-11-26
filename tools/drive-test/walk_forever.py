"""선 따라가기를 시간 제한 없이: /tmp/walk.stop 파일이 생기거나 보드가 E lost를 보내면 S. 최대 5분.
   python walk_forever.py 보폭 최대각도 [기록 파일]   → 기본 ~/test_logs/drive/날짜_시각.log (전원이 꺼져도 남게) + 화면에도 한 줄씩"""
import os, signal, sys, time
sys.path.insert(0, ".")
from edge import hwcheck
from edge.dog_link import BoardState
stride, amax = int(sys.argv[1]), int(sys.argv[2])
STOP = "/tmp/walk.stop"
if os.path.exists(STOP):
    os.remove(STOP)
def _hup(*_):
    raise KeyboardInterrupt
signal.signal(signal.SIGHUP, _hup)
signal.signal(signal.SIGTERM, _hup)
class A: port = None
link = hwcheck._open_dog(A())
chans = "line,far,near,steer,yaw,sonar,bat"
st = BoardState(chans)
link.send("T", 200, chans)
hwcheck._read(link, st, 0.5)
LOG = sys.argv[3] if len(sys.argv) > 3 else os.path.expanduser(time.strftime("~/test_logs/drive/%Y%m%d_%H%M%S.log"))
os.makedirs(os.path.dirname(LOG), exist_ok=True)
_f = open(LOG, "w", buffering=1)


class _Tee:  # 파일과 화면(ssh로 PC에 바로 옴)에 같이
    def write(self, s):
        _f.write(s)
        sys.stdout.write(s)
        sys.stdout.flush()

    def close(self):
        _f.close()


log = _Tee()
# 배터리 (mV): 출발 전 BAT_START 밑이면 안 감, 걷는 중 BAT_STOP 밑이 3번 연속이면 멈춤(걸을 때 전압이 출렁여 한 번 값으론 안 봄).
# Pi는 6.4 V = 0 %, 8.4 V = 100 % → 7.0 V = 30 %, 6.7 V = 15 %(서버 BATTERY_EMERGENCY). MechDog 자체 부저는 약 6.8~6.9 V에서 울림(2026-11-27)
BAT_START, BAT_STOP = 7000, 6700
t0 = time.monotonic(); last_f = -99; last_v = 0; reason = "최대 5분"; low = 0
try:
    bat = st.values.get("bat")
    if bat is not None and 0 < bat < BAT_START:
        reason = "배터리 %.2f V, 충전 필요 (출발 안 함)" % (bat / 1000)
        raise KeyboardInterrupt
    while time.monotonic() - t0 < 300:
        now = time.monotonic() - t0
        if now - last_f > 10:  # 보드 한 번 이동은 최대 15초 → 10초마다 다시 보내 이어 감
            link.send("F", stride, amax, 15000); last_f = now
        if now - last_v > 1:  # 보드 2초 감시(QUIET_MS)에 걸리지 않게
            link.send("V"); last_v = now
        if os.path.exists(STOP):
            reason = "사용자 정지"; break
        p = hwcheck._read(link, st, 0.1, until=lambda p: p[0] in ("D", "E"))
        if p and p[0] == "D":
            v = st.values
            log.write("%5.1f line %s far %s near %s steer %s yaw %s sonar %s bat %s\n" % (now, v.get("line"), v.get("far"), v.get("near"), v.get("steer"), v.get("yaw"), v.get("sonar"), v.get("bat")))
            low = low + 1 if v.get("bat") and 0 < v["bat"] < BAT_STOP else 0
            if low >= 3:
                reason = "배터리 %.2f V, 충전 필요" % (v["bat"] / 1000); break
        elif p and p[0] == "E" and len(p) > 2 and p[2] == "lost":
            reason = "보드가 선을 못 찾음 (%s°)" % (p[3] if len(p) > 3 else "?"); break
        elif p and p[0] == "E" and len(p) > 2 and p[2] == "stop":
            reason = "앞 막힘 %s cm" % p[3]; break
except KeyboardInterrupt:
    if reason == "최대 5분":
        reason = "끊김"
finally:
    link.send("S"); hwcheck._read(link, st, 1.0, until=hwcheck._answer("S")); link.send("T", 0); link.close()
    log.write("끝: %s, %.1f초\n" % (reason, time.monotonic() - t0)); log.close()
