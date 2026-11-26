# MechDog 보드 프로그램 (firmware/dog). Pi 5와 USB 시리얼(115200, REPL과 같은 UART)로 CMD|…|$를 주고받는다.
# 시험 도구 tools/mechdog-test/devices/dog/mechdog_test.py(BLE)에서 받는 곳만 바꾸고 A(음성)·L arrow·2초 감시를 더했다.
# 명령 형식: CMD|명령|값…|$  →  응답 CMD|명령|…|$ , 오류 CMD|!|명령|이유|$   (docs/protocol.md 1절)
# 정보: CMD|V|$ → CMD|V|버전|명령,…|숫자 채널,…|이벤트 채널,…|$
# 셀프 체크: python firmware/dog/sim_check.py
import Hiwonder
import math
import sys
import time
import Hiwonder_IIC
from HW_MechDog import MechDog

VERSION = "dog-1.15.0"  # 올리는 규칙은 README "버전". upload.py가 올릴 때 뒤에 +해시를 붙임
MAX_MS = 15000   # 이동 명령 한 번의 최대 시간
QUIET_MS = 2000  # Pi가 이만큼 조용하면 정지 + LED 오류
# 걸음: 발 드는 시간 · 딛는 시간(ms) · 다리 드는 높이 (공식 예제 change_gait_parameter.py 값). 켤 때 넣고, G로 바꿀 수 있음.
# 2026-11-26 실기(무게중심 뒤쪽, 3D 출력물 달기 전): 기본 걸음은 오른쪽으로 옆걸음 · 화면이 걸음마다 ±15~20칸 흔들림,
# 이 걸음은 왼쪽으로 도는 버릇(초당 약 2.5°)이 있지만 흔들림이 작아 선을 가운데에 가장 잘 맞춤(T17). 쏠림은 선 따라가기의 쌓임이 메움
GAIT = (150, 500, 40)

dog = MechDog()
sonar = Hiwonder_IIC.I2CSonar(Hiwonder_IIC.IIC(1))


def optional(make):
    """없는 부품(비전 모듈·LED 등)이 있어도 프로그램이 죽지 않게"""
    try:
        return make()
    except Exception:
        return None


imu = optional(Hiwonder_IIC.MPU)
cam = optional(lambda: Hiwonder_IIC.ESP32S3Cam(Hiwonder_IIC.IIC(2)))
tube = optional(Hiwonder.Digitaltube)
# shortcut: MP3 클래스 이름·주소(포트 2, 0x7B)는 실기 확인 전 추정. 없으면 A 명령이 "MP3 없음"으로 답한다
mp3 = optional(lambda: Hiwonder_IIC.MP3(Hiwonder_IIC.IIC(2)))
# 미션맵 선은 검은 테이프. 비전 모듈에 firmware/vision/patrol_cam을 구워야 검정을 찾는다 (README)
line_color = getattr(cam, "BLACK", 0) if cam else 0

# 지금 하고 있는 일: until(이동 끝 시각), mode(None/move/follow/guard), 스트림 주기·채널, rx(Pi에게서 마지막으로 받은 시각)
st = {"until": None, "mode": None, "args": None, "period": 0, "chans": [], "events": [], "t0": 0, "last": {}, "guard": None,
      "rx": 0, "alarm": False, "led": None, "steer": 0,
      "i": 0.0, "ft": 0, "win": [], "search": None, "side": 0, "pivot": 0}
# i · ft · win: 선 따라가기의 쌓인 오차 · 지난 반복 시각 · 한 걸음 기록. search: 선을 찾는 중이면 (시작 시각, 시작 yaw). side: 마지막으로 본 선 쪽(+ 왼쪽)


def now():
    return time.ticks_ms()


def send(*parts):
    sys.stdout.write("CMD|" + "|".join(str(p) for p in parts) + "|$\n")  # 한 번에 써야 두 스레드의 줄이 섞이지 않음


def r1(x):
    return round(x, 1) if isinstance(x, float) else x


# ---------- 측정 채널 ----------
SONAR_MAX_CM = 500  # 못 잰 값은 6553.5(0xFFFF mm)로 온다. 공장 앱도 5000mm 넘으면 자름


def ch_sonar():
    d = sonar.getDistance()
    return r1(d) if 0 <= d <= SONAR_MAX_CM else -1


def line_xs():
    """(위 · 가운데 · 아래 구역 선 x), 못 찾으면 -1. x는 0~159, 가운데 80.
    patrol_cam의 0xB0을 읽으면 세 구역 7바이트 [ID, 중심x, 중심y, 좌상x, 좌상y, 우하x, 우하y]씩 21바이트.
    레지스터 번호를 먼저 따로 써야 함(readfrom_mem처럼 이어서 읽으면 비전 모듈이 지난 레지스터 값을 줌, 2026-11-26 실기)"""
    cam.iic.writeto(0x53, bytes([0xB0]))
    c = cam.iic.readfrom(0x53, 21)
    return tuple(c[i + 1] if c[i] else -1 for i in (0, 7, 14))


def ch_line():
    """선 중심 x, 못 찾으면 -1. 발 바로 앞(아래) → 가운데 → 위 구역 순서"""
    far, mid, near = line_xs()
    return near if near >= 0 else mid if mid >= 0 else far


def ev_color():
    found = cam.color_recognition()
    return "+".join(str(c) for c in found) if found else "-"


CHANNELS = {
    "bat": lambda: Hiwonder.Battery_power(),
    "sonar": ch_sonar,
}
if imu:
    CHANNELS["roll"] = lambda: r1(tilt()[0])
    CHANNELS["pitch"] = lambda: r1(tilt()[1])
    CHANNELS["yaw"] = lambda: r1(gyro["yaw"])
if cam:
    CHANNELS["line"] = ch_line
    CHANNELS["far"] = lambda: line_xs()[0]  # 위(먼) 구역 선 x (못 찾으면 -1)
    CHANNELS["near"] = lambda: line_xs()[2]  # 아래 구역(발 바로 앞) 선 x
CHANNELS["steer"] = lambda: st["steer"]  # 마지막으로 dog.move에 보낸 조향 각도 (+ 왼쪽)
EVENTS = {"color": ev_color}  # 비전 모듈이 없으면 읽기 오류 → stream이 건너뜀


try:  # 동작 스레드와 스트림 스레드가 I2C 센서를 동시에 읽지 않게
    import _thread
    bus = _thread.allocate_lock()
except Exception:
    bus = None


def locked(fn):
    if bus:
        bus.acquire()
    try:
        return fn()
    finally:
        if bus:
            bus.release()


def read(name):
    try:
        return locked(CHANNELS[name])
    except Exception:
        return -1


# ---------- 동작 ----------
# ---------- IMU (QMI8658) ----------
# 라이브러리 read_angle()은 자이로 기준값을 빼지 않고 쌓아서 가만히 있어도 초당 약 30°씩 돌아감
# (2026-11-26 실기: 가만히 자이로 (65.8, 6.2, 32.1)°/s, 가속도 (0.23, 0.98, 9.35) m/s²). 그래서 직접 계산:
# 기울기는 가속도로(공식 Arduino imu.cpp와 같은 식), 방향(yaw)은 기준값을 뺀 z축 자이로를 쌓음
gyro = {"bias": None, "yaw": 0.0, "t": 0}


def gyro_zero(n=80):
    """가만히 서 있을 때 자이로 기준값(°/s)을 재고 방향을 0으로 (한 번 읽기 약 3.5 ms → 약 0.3초)"""
    s = [0.0, 0.0, 0.0]
    for _ in range(n):
        s = [a + b for a, b in zip(s, locked(imu.read_gyro_data))]
    gyro["bias"] = [v / n for v in s]
    gyro["yaw"], gyro["t"] = 0.0, now()


def tick_yaw():
    """main 반복마다 z축 자이로를 시간만큼 쌓아 방향(°)을 갱신. 읽기 실패는 건너뜀 (로봇을 세우지 않게)"""
    if not imu or gyro["bias"] is None:
        return
    try:
        gz = locked(imu.read_gyro_data)[2] - gyro["bias"][2]
    except Exception:
        return
    t = now()
    gyro["yaw"] += gz * time.ticks_diff(t, gyro["t"]) / 1000
    gyro["t"] = t


def tilt():
    """(좌우, 앞뒤) 기울기 °. 가속도로 계산"""
    ax, ay, az = imu.read_accel_data()
    return math.degrees(math.atan2(ay, az)), math.degrees(math.atan2(ax, az))


def stop():
    dog.move(0, 0)
    st["until"] = st["mode"] = st["guard"] = None
    st["steer"] = 0


def run_for(ms, mode, args):
    ms = int(ms)
    if ms <= 0:
        raise ValueError("시간 필요")
    st["until"] = time.ticks_add(now(), min(ms, MAX_MS))
    st["mode"], st["args"] = mode, args


def c_move(stride, angle, ms):
    dog.move(int(stride), int(angle))
    run_for(ms, "move", None)


def c_follow(stride, angle, ms):
    """선 따라가기. angle은 최대 꺾는 각도 (+가 왼쪽). 꺾는 법은 tick_follow"""
    if not cam:
        raise ValueError("비전 모듈 없음")
    if st["mode"] != "follow":  # 걷는 중에 다시 오면(Pi가 시간을 늘림) 쌓인 보정 · 방향 기준을 그대로 둠
        gyro["yaw"] = 0.0  # 출발 방향 = 0
        st.update(i=0.0, ft=now(), win=[], search=None, side=0, pivot=0)
    run_for(ms, "follow", (int(stride), int(angle)))


def c_guard(stride, stop_cm, resume_cm, ms):
    """초음파 정지 걷기 (F-03·F-09): stop_cm 안 3번 연속 → 정지·알림, resume_cm 밖이면 다시 걸음"""
    st["guard"] = {"stride": int(stride), "stop": float(stop_cm), "resume": float(resume_cm), "near": 0, "stopped": False}
    dog.move(int(stride), 0)
    run_for(ms, "guard", None)


def c_gait(lift, touch, height):
    dog.set_gait_params(int(lift), int(touch), int(height))


ARROWS = {"left": "<", "right": ">", "up": "^", "x": "X"}


def c_led(mode, value=""):
    if not tube:
        raise ValueError("LED 없음")
    if mode == "arrow":  # shortcut: 화살표 그림 대신 글자 한 개. 그림이 필요하면 tube.setLedOnOff(x, y, 1)로 점을 찍음
        if value not in ARROWS:
            raise ValueError("모르는 화살표 " + value)
        mode, value = "text", ARROWS[value]
    if mode == "num":
        tube.showNum(int(value))
    elif mode == "bright":
        tube.setBrightness(int(value))
    elif mode == "clear":
        tube.clear()
    elif mode in ("text", "char"):
        tube.clear()
        tube.drawStr(0, 0, value)
    else:
        raise ValueError("지원 안 함 " + mode)


def c_color(name):
    global line_color
    line_color = getattr(cam, name.upper())


def c_stream(period, chans=""):
    names = [c for c in chans.split(",") if c]
    st["chans"] = [c for c in names if c not in EVENTS]  # 없는 채널도 자리를 지켜 -1로 (Pi가 순서로 이름을 붙임)
    st["events"] = [c for c in names if c in EVENTS]
    st["last"], st["t0"], st["period"] = {}, now(), int(period)


def c_voice(track, volume=20):
    """음성 재생: MP3 모듈의 track번 파일, 볼륨 0~30"""
    if not mp3:
        raise ValueError("MP3 없음")
    mp3.volume(max(0, min(30, int(volume))))
    mp3.playTrack(int(track))


def c_info():
    send("V", VERSION, ",".join(COMMANDS), ",".join(CHANNELS), ",".join(EVENTS))


COMMANDS = {
    "M": c_move, "F": c_follow, "U": c_guard, "G": c_gait,
    "S": lambda: stop(), "P": lambda: dog.set_default_pose(),
    "L": c_led, "A": c_voice, "C": c_color, "T": c_stream, "V": c_info,
    "6": lambda: send("6", Hiwonder.Battery_power()),  # 기본 앱과 같은 배터리 조회
}
QUIET = ("V", "6")  # 스스로 답을 보내는 명령


def handle(parts):
    name, args = parts[0], parts[1:]
    st["rx"] = now()
    if st["alarm"]:  # Pi가 돌아옴 → 오류 표시를 지우고 Pi가 마지막으로 띄운 LED(대피 화살표 등)로 되돌림
        st["alarm"] = False
        try:
            c_led(*(st["led"] or ("clear",)))
        except Exception:
            pass
    try:
        COMMANDS[name](*args)
        if name == "L":
            st["led"] = args
        if name not in QUIET:
            send(name, "ok")
    except Exception as e:
        send("!", name, str(e) or "오류")


# ---------- 반복 작업 ----------
def tick_watch():
    """안전: Pi가 QUIET_MS 넘게 아무것도 안 보내면 한 번 멈추고 LED에 오류 (스트림은 그대로 둠)"""
    if not st["alarm"] and time.ticks_diff(now(), st["rx"]) > QUIET_MS:
        st["alarm"] = True
        stop()
        try:
            c_led("char", "E")
        except Exception:
            pass


def on_line(line):
    """받은 한 줄 'CMD|F|50|25|1000|$' → handle. 형식이 아니면 버림 (시리얼 잡음)"""
    line = line.strip()
    if line.startswith("CMD|") and line.endswith("|$") and len(line) > 6:
        handle(line[4:-2].split("|"))


FOLLOW_CENTER = 80   # 로봇이 선 한가운데일 때의 x (카메라가 몸 가운데에서 비켜 달리면 고침)
# 바닥 카메라 기하: 각도 20° · 렌즈 높이 4.2 cm · 초점 548 px(320x240)에서 잰 값 (firmware/vision/view.py 각도 맞추기).
# 구역 가운데 줄이 보는 바닥 1칸(160 기준): 위 구역(렌즈 앞 약 20 cm) 0.075 cm, 가운데(약 11.5 cm) 0.045 cm, 아래(약 7.4 cm) 0.032 cm
CAM_CM_FAR, CAM_CM_MID, CAM_CM_NEAR = 0.075, 0.045, 0.032
# 구역별 무게: 발 앞(아래 · 가운데)을 먼저 맞추고 위 구역은 곡선을 미리 보는 데 절반만 (지금 밟는 선을 놓치지 않는 게 먼저)
BAND_W = ((CAM_CM_FAR, 0.5), (CAM_CM_MID, 1.0), (CAM_CM_NEAR, 1.0))
# 조향 목표: 영상에서 선이 가운데에 오게 (테이프가 조금 휘어도). 먼 · 가운데 구역 선이 가운데에서 비켜난 거리(cm)의 평균을
# 오차로 삼아 비례 + 쌓임(비켜난 채로 머무는 것 · 한쪽으로 쏠려 걷는 것 보정), 자이로로 도는 속도를 눌러 출렁임을 줄임.
# 먼 점 하나만 맞추면 몸이 비스듬한 채로 버티고(T12 · T13), 테이프 방향을 따로 계산하면 기울어 보이는 만큼 비켜난 채 멈춤(T14)
FOLLOW_KP = 10.0     # 1 cm 비켜나면 조향 10
FOLLOW_KI = 6.0      # 1 cm로 1초 머물면 조향 6씩 더
FOLLOW_I_MAX = 15.0  # 쌓임 한도
FOLLOW_KD = 1.0      # 초당 1° 돌 때마다 조향 1 덜 (자이로)
# 걸을 때 머리(카메라)가 걸음마다 좌우로 흔들림(T15: 약 0.7초 주기, 선 x 가운데 ±15칸 · 먼 ±20칸) → 한 걸음 동안의 평균으로 꺾음
FOLLOW_WIN_MS = 700
# 선을 놓치면 앞으로 가지 않고 마지막으로 본 선 쪽으로 제자리 돌기 (T18: 왼쪽 곡선에서 바깥으로 지나쳐 놓침).
# 보폭 0 · 각도 25에서 초당 왼쪽 약 6.5° · 오른쪽 약 4.6° 돎(T4, 조금씩 움직임). 90° 돌거나 8초 지나도 못 찾으면 멈추고 E lost
# 급한 곡선: 걸으며 돌 수 있는 가장 작은 원이 반지름 30~40 cm(보폭 60 약 5 cm/s, 최대로 꺾어 초당 6~10°)라
# 그보다 좁은 곡선(U자)은 바깥으로 밀려 놓침(2026-11-26 밤, 놓친 뒤 돌며 찾다 옆 줄을 잡음). 한 걸음 평균으로
# 선이 PIVOT_ON_CM 넘게 비켜나면 앞으로 가지 않고 제자리에서 선 쪽으로 돌고, PIVOT_OFF_CM 안으로 오면 다시 걸음
PIVOT_ON_CM = 1.5
PIVOT_OFF_CM = 0.4
SEARCH_DELAY_MS = 600  # 이만큼은 걸으면서 마지막으로 본 쪽으로 최대 각도 (한두 번 놓친 것으로 멈추지 않게, 순찰 속도)
SEARCH_ANGLE = 40
SEARCH_MAX_DEG = 90
SEARCH_MS = 8000


def clamp(v, lim):
    return max(-lim, min(lim, v))


def tape_off():
    """선이 가운데에서 비켜난 거리 cm (+는 오른쪽). 세 구역의 가중 평균(BAND_W), 가장자리에 가까운 점일수록 더 크게
    (곡선에서 가까운 점이 빠져나가는데 먼 점이 가운데라 평균이 작게 나와 늦게 꺾고 놓침, T18). 셋 다 없으면 None"""
    try:
        xs = locked(line_xs)
    except Exception:
        return None
    sw = se = 0.0
    for x, (k, bw) in zip(xs, BAND_W):
        if x >= 0:
            w = bw * (1 + ((x - FOLLOW_CENTER) / 40) ** 2)  # 가운데 1, 가장자리(±60칸) 약 3
            sw += w
            se += w * (x - FOLLOW_CENTER) * k
    return se / sw if sw else None


def tick_follow(stride, amax):
    """조향 = -(KP·오차 + 쌓임) - KD·도는 속도 (오차 · 속도는 한 걸음 평균), 최대 각도에서 자름. 조향 +와 yaw +는 왼쪽, 오차 +는 선이 오른쪽.
    보폭은 그대로 (보폭 30은 제자리, 50~70은 감). 크게 비켜나면 제자리에서 선 쪽으로(PIVOT_*),
    선을 놓치면 마지막으로 본 선 쪽으로 제자리 돌기(SEARCH_*)"""
    t = now()
    dt = min(time.ticks_diff(t, st["ft"]), 200) / 1000
    st["ft"] = t
    e = tape_off()
    if e is None:
        if st["search"] is None:
            st["search"] = (t, gyro["yaw"])
            st["win"] = []
        t0, y0 = st["search"]
        turned, lost_ms = gyro["yaw"] - y0, time.ticks_diff(t, t0)
        if abs(turned) > SEARCH_MAX_DEG or lost_ms > SEARCH_MS:
            stop()
            send("E", time.ticks_diff(t, st["t0"]), "lost", r1(turned))
            return
        side = 1 if st["side"] >= 0 else -1
        if lost_ms < SEARCH_DELAY_MS:  # 잠깐은 걸으면서 그쪽으로 크게 꺾음
            st["steer"] = side * amax
            dog.move(stride, side * amax)
        else:  # 그래도 없으면 제자리에서 돌며 찾음
            st["steer"] = side * SEARCH_ANGLE
            dog.move(0, side * SEARCH_ANGLE)
        return
    st["search"] = None
    e_now = e
    w = st["win"]
    w.append((t, e, gyro["yaw"]))
    while time.ticks_diff(t, w[0][0]) > FOLLOW_WIN_MS:
        w.pop(0)
    e = sum(x[1] for x in w) / len(w)  # 한 걸음 평균 오차
    if e:
        st["side"] = -1 if e > 0 else 1  # 선이 오른쪽이면 오른쪽(−)으로 찾음
    span = time.ticks_diff(t, w[0][0]) / 1000
    rate = (gyro["yaw"] - w[0][2]) / span if span > 0.1 else 0.0  # 한 걸음 동안 돈 속도 °/s
    # 들어갈 때는 한 걸음 평균(머리 흔들림에 안 속게), 나올 때는 지금 값: 평균은 0.35초 늦어 가운데를 지나쳐 반대쪽까지 돌고
    # 다시 반대로 돌기를 되풀이함(dog-1.14.0 21초 중 67%를 좌우로 제자리 돌기). 지금 값이 가운데 근처거나 반대쪽으로 넘어가면 바로 걸음
    if not st["pivot"] and abs(e) > PIVOT_ON_CM:
        st["pivot"] = -1 if e > 0 else 1
        st["win"] = [w[-1]]
    elif st["pivot"] and (abs(e_now) < PIVOT_OFF_CM or (e_now > 0) == (st["pivot"] > 0)):
        st["pivot"] = 0
        st["win"] = [w[-1]]
    if st["pivot"]:  # 제자리에서 선 쪽으로 (쌓임은 그대로 둠: 다시 걸을 때 반대로 크게 꺾지 않게)
        st["steer"] = a = st["pivot"] * SEARCH_ANGLE
        dog.move(0, a)
        return
    st["i"] = clamp(st["i"] + FOLLOW_KI * e * dt, FOLLOW_I_MAX)
    a = int(round(clamp(-(FOLLOW_KP * e + st["i"]) - FOLLOW_KD * rate, amax)))
    st["steer"] = a
    dog.move(stride, a)


def tick_motion():
    if st["until"] is not None and time.ticks_diff(now(), st["until"]) >= 0:
        stop()
        send("E", time.ticks_diff(now(), st["t0"]), "done", 1)
        return
    if st["mode"] == "follow":
        tick_follow(*st["args"])
    elif st["mode"] == "guard":
        g, d = st["guard"], read("sonar")
        if d < 0:
            return
        if not g["stopped"]:
            g["near"] = g["near"] + 1 if d < g["stop"] else 0
            if g["near"] >= 3:
                dog.move(0, 0)
                g["stopped"] = True
                send("E", time.ticks_diff(now(), st["t0"]), "stop", d)
        elif d > g["resume"]:
            g["stopped"], g["near"] = False, 0
            dog.move(g["stride"], 0)
            send("E", time.ticks_diff(now(), st["t0"]), "resume", d)


def main():
    import select
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    buf = ""
    st["rx"] = now()
    while True:
        while poll.poll(0):  # 와 있는 글자만 읽어 이동 반복이 막히지 않게
            ch = sys.stdin.read(1)
            if ch == "\n":
                on_line(buf)
                buf = ""
            elif len(buf) < 200:
                buf += ch
        try:
            tick_watch()
            tick_yaw()
            tick_motion()
        except Exception as e:  # 여기서 죽으면 15초 제한·2초 감시가 같이 꺼진 채 다리는 계속 걷는다
            stop()
            send("!", "loop", str(e) or "오류")
        time.sleep_ms(20)


def stream():
    me = st  # upload.py가 재시작 없이 main.py를 다시 실행하면 st가 새로 생긴다 → 이전 스레드는 끝남
    while st is me:
        if st["period"]:
            t = time.ticks_diff(now(), st["t0"])
            if st["chans"]:
                send("D", t, *[read(c) for c in st["chans"]])
            for name in st["events"]:
                try:
                    v = locked(EVENTS[name])
                except Exception:
                    continue
                if st["last"].get(name) != v:
                    st["last"][name] = v
                    send("E", t, name, v)
            time.sleep_ms(st["period"])
        else:
            time.sleep_ms(50)


dog.set_default_pose()
dog.set_gait_params(*GAIT)
time.sleep(1)
if imu:
    try:
        gyro_zero()  # 선 자세로 가만히 있을 때. 걷기 시작 때는 방향만 0으로 (c_follow)
    except Exception:
        imu = None
Hiwonder.startMain(stream)
# main은 스레드로 돌리지 않는다. 이 파일이 끝나면 REPL이 켜져 같은 시리얼에서 Pi 명령을 가로챈다.
# Ctrl-C(upload.py)로 끊으면 멈추고 REPL로 돌아간다. 스트림도 꺼서 파일 올리는 대화에 섞이지 않게 한다.
try:
    main()
finally:
    stop()
    st["period"] = 0
