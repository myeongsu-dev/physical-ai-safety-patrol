# MechDog 보드 프로그램 (firmware/dog). Pi 5와 USB 시리얼(115200, REPL과 같은 UART)로 CMD|…|$를 주고받는다.
# 시험 도구 tools/mechdog-test/devices/dog/mechdog_test.py(BLE)에서 받는 곳만 바꾸고 A(음성)·L arrow·2초 감시를 더했다.
# 명령 형식: CMD|명령|값…|$  →  응답 CMD|명령|…|$ , 오류 CMD|!|명령|이유|$   (docs/protocol.md 1절)
# 정보: CMD|V|$ → CMD|V|버전|명령,…|숫자 채널,…|이벤트 채널,…|$
# 셀프 체크: python firmware/dog/sim_check.py
import Hiwonder
import sys
import time
import Hiwonder_IIC
from HW_MechDog import MechDog

VERSION = "dog-1"
MAX_MS = 15000   # 이동 명령 한 번의 최대 시간
QUIET_MS = 2000  # Pi가 이만큼 조용하면 정지 + LED 오류

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
line_color = getattr(cam, "YELLOW", 0) if cam else 0

# 지금 하고 있는 일: until(이동 끝 시각), mode(None/move/follow/guard), 스트림 주기·채널, rx(Pi에게서 마지막으로 받은 시각)
st = {"until": None, "mode": None, "args": None, "period": 0, "chans": [], "events": [], "t0": 0, "last": {}, "guard": None,
      "rx": 0, "alarm": False, "led": None}


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


def ch_line():
    c = cam.line_follow(line_color)
    return -1 if (c[0] == 0 and c[1] == 0) else c[0]


def ev_color():
    found = cam.color_recognition()
    return "+".join(str(c) for c in found) if found else "-"


CHANNELS = {
    "bat": lambda: Hiwonder.Battery_power(),
    "sonar": ch_sonar,
}
if imu:
    CHANNELS["roll"] = lambda: r1(imu.read_angle()[0])
    CHANNELS["pitch"] = lambda: r1(imu.read_angle()[1])
if cam:
    CHANNELS["line"] = ch_line
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
def stop():
    dog.move(0, 0)
    st["until"] = st["mode"] = st["guard"] = None


def run_for(ms, mode, args):
    ms = int(ms)
    if ms <= 0:
        raise ValueError("시간 필요")
    st["until"] = time.ticks_add(now(), min(ms, MAX_MS))
    st["mode"], st["args"] = mode, args


def c_move(stride, angle, ms):
    dog.move(int(stride), int(angle))
    run_for(ms, "move", None)


def c_follow(stride, angle, ms, lo=60, hi=100):
    """선 따라가기 (공식 예제와 같은 방식: 선 x가 lo보다 작으면 왼쪽, hi보다 크면 오른쪽으로 꺾음)"""
    if not cam:
        raise ValueError("비전 모듈 없음")
    run_for(ms, "follow", (int(stride), int(angle), int(lo), int(hi)))


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


def tick_motion():
    if st["until"] is not None and time.ticks_diff(now(), st["until"]) >= 0:
        stop()
        send("E", time.ticks_diff(now(), st["t0"]), "done", 1)
        return
    if st["mode"] == "follow":
        stride, angle, lo, hi = st["args"]
        x = read("line")
        dog.move(stride, 0 if x < 0 or lo <= x <= hi else (angle if x < lo else -angle))
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
time.sleep(1)
Hiwonder.startMain(stream)
# main은 스레드로 돌리지 않는다. 이 파일이 끝나면 REPL이 켜져 같은 시리얼에서 Pi 명령을 가로챈다.
# Ctrl-C(upload.py)로 끊으면 멈추고 REPL로 돌아간다. 스트림도 꺼서 파일 올리는 대화에 섞이지 않게 한다.
try:
    main()
finally:
    stop()
    st["period"] = 0
