"""셀프 체크: python devices/dog/sim_check.py
가짜 Hiwonder 모듈로 mechdog_test.py를 PC에서 불러 명령 처리·자동 정지·초음파 정지 규칙을 확인한다 (실제 로봇 없이)."""
import importlib.util
import sys
import time
import types
from pathlib import Path

sent, moves, clock = [], [], [0]
sonar_cm = [60.0]


def mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m


class BLE:
    MODE_BLE_SLAVE = 0
    def __init__(self, *a): pass
    def send_data(self, s): sent.append(s)
    def is_connected(self): return True


class Dog:
    def move(self, s, a): moves.append((s, a))
    def set_default_pose(self, *a): pass
    def set_gait_params(self, *a): moves.append(("gait",) + a)


class Sonar:
    def __init__(self, *a): pass
    def getDistance(self): return sonar_cm[0]


class Cam:
    YELLOW = 4
    def __init__(self, *a): pass
    def line_follow(self, c): return [80, 70]
    def color_recognition(self): return [1]


class Tube:
    def showNum(self, n): sent.append(f"tube {n}")
    def setBrightness(self, n): pass


mod("Hiwonder", Battery_power=lambda: 8520, Digitaltube=Tube, startMain=lambda f: None)
mod("Hiwonder_IIC", IIC=lambda n: n, I2CSonar=Sonar, MPU=lambda: types.SimpleNamespace(read_angle=lambda: [1.25, -0.5]), ESP32S3Cam=Cam)
mod("Hiwonder_BLE", BLE=BLE)
mod("HW_MechDog", MechDog=Dog)
mod("machine", unique_id=lambda: b"\x00\x00\x00\x00\x00\x48")
# MicroPython 시간 함수
time.ticks_ms = lambda: clock[0]
time.ticks_add = lambda a, b: a + b
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: None
time.sleep = lambda s: None

spec = importlib.util.spec_from_file_location("dt", Path(__file__).with_name("mechdog_test.py"))
dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dt)


def cmd(*parts):
    sent.clear()
    dt.handle([str(p) for p in parts])
    return sent[-1] if sent else ""


# 정보: 명령·숫자 채널·이벤트 채널
assert cmd("V") == "CMD|V|dogtest-1|M,F,U,G,S,P,L,C,T,V,6|bat,sonar,roll,pitch,line|color|$", sent
assert cmd("6") == "CMD|6|8520|$"
# 이동: 시간 뒤 자동 정지 + done 이벤트
assert cmd("M", 50, 25, 1000) == "CMD|M|ok|$" and moves[-1] == (50, 25)
clock[0] = 999; dt.tick_motion(); assert moves[-1] == (50, 25)
clock[0] = 1000; dt.tick_motion(); assert moves[-1] == (0, 0) and "done" in sent[-1]
# 시간 없는 이동·최대 시간
assert cmd("M", 50, 0, 0).startswith("CMD|!|M|")
cmd("M", 50, 0, 99999); assert dt.st["until"] == clock[0] + dt.MAX_MS
cmd("S")
# 초음파 정지 걷기: 14cm 안 3번 연속이면 정지, 18cm 밖이면 재출발
clock[0] = 0
cmd("U", 50, 14, 18, 10000); assert moves[-1] == (50, 0)
sonar_cm[0] = 13.0
dt.tick_motion(); dt.tick_motion(); assert moves[-1] == (50, 0)  # 2번은 아직
dt.tick_motion(); assert moves[-1] == (0, 0) and "|stop|13.0|" in sent[-1]
sonar_cm[0] = 25.0
dt.tick_motion(); assert moves[-1] == (50, 0) and "|resume|25.0|" in sent[-1]
cmd("S")
# 선 따라가기: 선 x=80(가운데)이면 직진
cmd("F", 50, 25, 2000); dt.tick_motion(); assert moves[-1] == (50, 0)
cmd("S")
# LED: 숫자는 됨, 펌웨어에 없는 함수는 오류로 답함
assert cmd("L", "num", 88) == "CMD|L|ok|$" and "tube 88" in sent
assert cmd("L", "text", "<").startswith("CMD|!|L|")
# 없는 명령
assert cmd("Z").startswith("CMD|!|Z|")
print("sim_check ok")
