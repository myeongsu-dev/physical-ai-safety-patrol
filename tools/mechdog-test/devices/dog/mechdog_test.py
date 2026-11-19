# MechDog 동작 시험용 BLE 프로그램 (07_corp-project/tools/mechdog-test)
# Hiwonder Python Editor로 MechDog 본체에 올린다. 기본 앱(Blue.py)과 같은 BLE 이름·방식(FFE0)을 쓴다.
# 명령 형식: CMD|명령|값…|$  →  응답 CMD|명령|…|$ , 오류 CMD|!|명령|이유|$
# 정보: CMD|V|$ → CMD|V|버전|명령,…|숫자 채널,…|이벤트 채널,…|$ (앱이 이것을 보고 쓸 수 있는 시험만 연다)
# 확장: 새 동작은 COMMANDS에, 새 측정값은 CHANNELS(숫자) 또는 EVENTS(바뀔 때만 보냄)에 함수 하나를 등록한다.
import Hiwonder
import time
import machine
import Hiwonder_IIC
from Hiwonder_BLE import BLE
from HW_MechDog import MechDog

VERSION = "dogtest-1"
MAX_MS = 15000  # 이동 명령 한 번의 최대 시간 (연결이 끊기지 않아도 저절로 멈추게)

ble = BLE(BLE.MODE_BLE_SLAVE, "MechDog_{:02X}".format(machine.unique_id()[5]))
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
line_color = getattr(cam, "YELLOW", 0) if cam else 0

# 지금 하고 있는 일: until(이동 끝 시각), mode(None/move/follow/guard), 스트림 주기·채널
st = {"until": None, "mode": None, "args": None, "period": 0, "chans": [], "events": [], "t0": 0, "last": {}, "guard": None}


def now():
    return time.ticks_ms()


def send(*parts):
    ble.send_data("CMD|" + "|".join(str(p) for p in parts) + "|$")


def r1(x):
    return round(x, 1) if isinstance(x, float) else x


# ---------- 측정 채널 ----------
def ch_line():
    c = cam.line_follow(line_color)
    return -1 if (c[0] == 0 and c[1] == 0) else c[0]


def ev_color():
    found = cam.color_recognition()
    return "+".join(str(c) for c in found) if found else "-"


CHANNELS = {
    "bat": lambda: Hiwonder.Battery_power(),
    "sonar": lambda: r1(sonar.getDistance()),
}
if imu:
    CHANNELS["roll"] = lambda: r1(imu.read_angle()[0])
    CHANNELS["pitch"] = lambda: r1(imu.read_angle()[1])
if cam:
    CHANNELS["line"] = ch_line
EVENTS = {"color": ev_color} if cam else {}


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


def c_led(mode, value=""):
    if not tube:
        raise ValueError("LED 없음")
    if mode == "num":
        tube.showNum(int(value))
    elif mode == "bright":
        tube.setBrightness(int(value))
    else:  # text·clear 등은 펌웨어에 그 함수가 있을 때만 (실기 확인 필요)
        fn = {"text": "showString", "char": "showChar", "clear": "clear"}.get(mode)
        if not fn or not hasattr(tube, fn):
            raise ValueError("지원 안 함 " + mode)
        getattr(tube, fn)(*([value] if value != "" else []))


def c_color(name):
    global line_color
    line_color = getattr(cam, name.upper())


def c_stream(period, chans=""):
    names = [c for c in chans.split(",") if c]
    st["chans"] = [c for c in names if c in CHANNELS]
    st["events"] = [c for c in names if c in EVENTS]
    st["last"], st["t0"], st["period"] = {}, now(), int(period)


def c_info():
    send("V", VERSION, ",".join(COMMANDS), ",".join(CHANNELS), ",".join(EVENTS))


COMMANDS = {
    "M": c_move, "F": c_follow, "U": c_guard, "G": c_gait,
    "S": lambda: stop(), "P": lambda: dog.set_default_pose(),
    "L": c_led, "C": c_color, "T": c_stream, "V": c_info,
    "6": lambda: send("6", Hiwonder.Battery_power()),  # 기본 앱과 같은 배터리 조회
}
QUIET = ("V", "6")  # 스스로 답을 보내는 명령


def handle(parts):
    name, args = parts[0], parts[1:]
    try:
        COMMANDS[name](*args)
        if name not in QUIET:
            send(name, "ok")
    except Exception as e:
        send("!", name, str(e) or "오류")


# ---------- 반복 작업 ----------
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
    connected = False
    while True:
        is_on = ble.is_connected()
        if connected and not is_on:  # 연결이 끊기면 바로 멈춤
            stop()
            st["period"] = 0
        connected = is_on
        if is_on and ble.contains_data("CMD"):
            raw = ble.read_uart_cmd()
            if raw:
                handle(ble.parse_uart_cmd(raw))
        tick_motion()
        time.sleep_ms(20)


def stream():
    while True:
        if st["period"] and ble.is_connected():
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
Hiwonder.startMain(main)
Hiwonder.startMain(stream)
