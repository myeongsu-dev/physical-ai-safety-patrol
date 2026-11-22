"""셀프 체크: python firmware/dog/sim_check.py
가짜 Hiwonder 모듈로 main.py를 PC에서 불러 명령 처리·자동 정지·초음파 정지·2초 감시를 확인한다 (실제 로봇 없이)."""
import importlib.util
import sys
import time
import types
from pathlib import Path

sent, moves, clock = [], [], [0]
sonar_cm = [60.0]
boom = [0]  # 0보다 크면 dog.move가 그 횟수만큼 예외 (라이브러리 오류 흉내)


def mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m


class Dog:
    def move(self, s, a):
        if boom[0]:
            boom[0] -= 1
            raise OSError("i2c")
        moves.append((s, a))
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
    def drawStr(self, x, y, s): sent.append(f"tube {s}")
    def clear(self): sent.append("tube clear")
    def setBrightness(self, n): pass


class MP3:
    def __init__(self, *a): pass
    def volume(self, v): sent.append(f"vol {v}")
    def playTrack(self, n): sent.append(f"track {n}")


mod("Hiwonder", Battery_power=lambda: 8520, Digitaltube=Tube, startMain=lambda f: None)
mod("Hiwonder_IIC", IIC=lambda n: n, I2CSonar=Sonar, MP3=MP3, MPU=lambda: types.SimpleNamespace(read_angle=lambda: [1.25, -0.5]), ESP32S3Cam=Cam)
mod("HW_MechDog", MechDog=Dog)
mod("select", POLLIN=1, poll=lambda: types.SimpleNamespace(register=lambda *a: None, poll=lambda t: []))  # 받은 글자 없음
# MicroPython 시간 함수
time.ticks_ms = lambda: clock[0]
time.ticks_add = lambda a, b: a + b
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: None
time.sleep = lambda s: None


class Halt(BaseException):
    """반복문을 한 바퀴 뒤 끝내려고 (Exception이 아니라서 main 안에서 안 잡힘)"""


def one_loop(fn):
    def halt(ms):
        raise Halt
    time.sleep_ms = halt
    try:
        fn()
    except Halt:
        pass
    time.sleep_ms = lambda ms: None


# main.py는 맨 끝에서 main()을 직접 돌리므로(REPL 막기) 불러올 때 한 바퀴 뒤 끊는다
spec = importlib.util.spec_from_file_location("dt", Path(__file__).with_name("main.py"))
dt = importlib.util.module_from_spec(spec)
one_loop(lambda: spec.loader.exec_module(dt))
assert moves[-1] == (0, 0)  # main이 끝나면 멈춤 (Ctrl-C로 끊을 때)
dt.sys = types.SimpleNamespace(stdin=None, stdout=types.SimpleNamespace(write=lambda s: sent.append(s.rstrip("\n"))))


def cmd(*parts):
    """Pi가 보내는 것과 같은 줄을 넣고 보드의 마지막 답을 돌려줌"""
    sent.clear()
    dt.on_line("CMD|" + "|".join(str(p) for p in parts) + "|$\n")
    return sent[-1] if sent else ""


# 정보: 명령·숫자 채널·이벤트 채널
assert cmd("V") == "CMD|V|dog-1|M,F,U,G,S,P,L,A,C,T,V,6|bat,sonar,roll,pitch,line|color|$", sent
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
sonar_cm[0] = 6553.5  # 못 잰 값: -1로 보내고 정지·재개 판단에 쓰지 않음
assert dt.read("sonar") == -1
dt.tick_motion(); assert moves[-1] == (0, 0)
sonar_cm[0] = 25.0
dt.tick_motion(); assert moves[-1] == (50, 0) and "|resume|25.0|" in sent[-1]
cmd("S")
# 선 따라가기: 선 x=80(가운데)이면 직진
cmd("F", 50, 25, 2000); dt.tick_motion(); assert moves[-1] == (50, 0)
cmd("S")
# LED: 숫자·글자는 됨, 모르는 모드는 오류로 답함
assert cmd("L", "num", 88) == "CMD|L|ok|$" and "tube 88" in sent
assert cmd("L", "text", "Hi") == "CMD|L|ok|$" and sent[:2] == ["tube clear", "tube Hi"]
assert cmd("L", "blink", 1).startswith("CMD|!|L|")
# LED 화살표: Pi의 guide 명령 (L|arrow|left), 모르는 방향은 오류
assert cmd("L", "arrow", "left") == "CMD|L|ok|$" and "tube <" in sent
assert cmd("L", "arrow", "down").startswith("CMD|!|L|")
assert cmd("L", "clear") == "CMD|L|ok|$"
# 음성: A|번호|볼륨, 볼륨은 0~30으로 자름
assert cmd("A", 1, 99) == "CMD|A|ok|$" and "vol 30" in sent and "track 1" in sent
# 없는 명령·형식이 아닌 줄(잡음)은 답 없이 버림
assert cmd("Z").startswith("CMD|!|Z|")
sent.clear(); dt.on_line("garbage\n"); dt.on_line("CMD||$"); assert sent == []
# 2초 감시: 걷는 중 Pi가 2초 넘게 조용하면 정지 + LED 오류, 다시 받으면 오류 끔
clock[0] = 10000
cmd("M", 50, 0, 10000)
clock[0] = 12000; dt.tick_watch(); assert moves[-1] == (50, 0) and not dt.st["alarm"]
clock[0] = 12001; dt.tick_watch(); assert moves[-1] == (0, 0) and dt.st["alarm"] and "tube E" in sent
moves.clear(); dt.tick_watch(); assert moves == []  # 한 번만
cmd("V"); assert not dt.st["alarm"] and "tube clear" in sent
# 대피 화살표는 2초 감시 뒤 Pi가 돌아오면 다시 뜸 (Pi는 guide를 한 번만 보냄)
cmd("L", "arrow", "left")
clock[0] = 14002; dt.tick_watch(); assert "tube E" in sent
cmd("V"); assert sent[:2] == ["tube clear", "tube <"]  # 지우고 끝내지 않고 화살표를 다시 그림
cmd("L", "clear")
# 측정값: 없는 채널(nope)도 자리를 지켜 -1, color는 이벤트로 따로
sonar_cm[0] = 30.0
cmd("T", 100, "bat,nope,sonar,color"); sent.clear()
one_loop(dt.stream); assert sent == ["CMD|D|0|8520|-1|30.0|$", "CMD|E|0|color|1|$"], sent
cmd("T", 0)
# upload.py가 main.py를 다시 실행하면(st가 새로 생김) 이전 스트림 스레드는 끝남
old = dt.st
def reload(ms):
    dt.st = dict(old)
time.sleep_ms = reload
dt.stream()  # 끝나지 않으면 여기서 멈춤
time.sleep_ms = lambda ms: None
dt.st = old
# 반복문에서 라이브러리 오류가 나도 반복문은 살아 있고 로봇은 멈춤
cmd("F", 50, 25, 5000)
sent.clear(); boom[0] = 1
one_loop(dt.main); assert dt.st["mode"] is None and moves[-1] == (0, 0) and sent[-1].startswith("CMD|!|loop|"), sent
print("sim_check ok")
