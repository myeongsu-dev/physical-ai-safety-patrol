"""셀프 체크: python firmware/dog/sim_check.py
가짜 Hiwonder 모듈로 main.py를 PC에서 불러 명령 처리·자동 정지·초음파 정지·2초 감시를 확인한다 (실제 로봇 없이)."""
import importlib.util
import subprocess  # noqa: F401  아래 가짜 select를 끼우기 전에 불러 둠 (Linux subprocess는 select.select를 씀, upload.py가 import)
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
    BLACK = 5
    def __init__(self, *a): pass
    line = [bytes(7), bytes([5, 80, 60, 70, 50, 90, 70]), bytes(7)]  # 위 · 가운데 · 아래 구역 (patrol_cam 0xB0)
    class iic:  # 레지스터 번호를 쓰고 따로 읽음
        reg = None
        @staticmethod
        def writeto(addr, buf): Cam.iic.reg = (addr, bytes(buf))
        @staticmethod
        def readfrom(addr, n):
            assert Cam.iic.reg == (0x53, bytes([0xB0])) and n == 21, Cam.iic.reg
            return Cam.line[0] + Cam.line[1] + Cam.line[2]
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
mod("Hiwonder_IIC", IIC=lambda n: n, I2CSonar=Sonar, MP3=MP3, MPU=lambda: types.SimpleNamespace(read_accel_data=lambda: [0.0, 0.0, 9.8], read_gyro_data=lambda: [65.8, 6.2, 32.1]), ESP32S3Cam=Cam)
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
assert dt.line_color == Cam.BLACK  # 미션맵 선은 검은 테이프
dt.sys = types.SimpleNamespace(stdin=None, stdout=types.SimpleNamespace(write=lambda s: sent.append(s.rstrip("\n"))))


def cmd(*parts):
    """Pi가 보내는 것과 같은 줄을 넣고 보드의 마지막 답을 돌려줌"""
    sent.clear()
    dt.on_line("CMD|" + "|".join(str(p) for p in parts) + "|$\n")
    return sent[-1] if sent else ""


# 정보: 명령·숫자 채널·이벤트 채널
assert cmd("V") == "CMD|V|%s|M,F,U,G,S,P,L,A,C,T,V,6|bat,sonar,roll,pitch,yaw,line,far,near,steer|color|$" % dt.VERSION, sent
# upload.py: 올릴 때 VERSION 뒤에 +해시, 줄바꿈이 달라도 같은 해시
spec = importlib.util.spec_from_file_location("upload", Path(__file__).with_name("upload.py"))
upload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upload)
src = Path(__file__).with_name("main.py").read_bytes()
h = upload.code_hash(src)
assert ('VERSION = "%s+%s"' % (dt.VERSION, h)).encode() in upload.stamp(src)
assert upload.code_hash(src.replace(b"\n", b"\r\n")) == h and upload.stamp(upload.stamp(src)).count(b"+") == upload.stamp(src).count(b"+")
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
# 마지막 안전장치: M · F로 앞으로 걷는 중 앞 10 cm 안이 3번(0.1초 간격) 연속이면 멈추고 E|stop
sonar_cm[0] = 8.0
clock[0] = 5000  # 위 시험이 시계를 되돌려 놓아서
cmd("M", 50, 10, 5000)
for _ in range(2):
    clock[0] += 100; dt.tick_motion()
assert moves[-1] == (50, 10)
clock[0] += 100; sent.clear(); dt.tick_motion()
assert moves[-1] == (0, 0) and dt.st["mode"] is None and any("|stop|8.0|" in x for x in sent), sent
cmd("M", -50, 0, 5000)                                # 뒤로 걷기 · 제자리는 앞을 막아도 안 멈춤
for _ in range(4):
    clock[0] += 100; dt.tick_motion()
assert moves[-1] == (-50, 0)
cmd("S"); sonar_cm[0] = 25.0
# 선 따라가기: 선 x=80(가운데)이면 직진
cmd("F", 50, 25, 10000); dt.tick_motion(); assert moves[-1] == (50, 0)


def follow(far, mid):
    Cam.line = [bytes([5, far, 20, 0, 0, 0, 0]) if far >= 0 else bytes(7), bytes([5, mid, 60, 0, 0, 0, 0]) if mid >= 0 else bytes(7), bytes(7)]
    clock[0] += 20
    dt.tick_motion()
    return moves[-1]
def fresh():  # 새로 출발 (기준 방향 · 쏠림 보정 0)
    cmd("S"); cmd("F", 50, 25, 10000); dt.gyro["yaw"] = 0.0
def run(far, mid, n=10):
    for _ in range(n):
        m = follow(far, mid)
    return m[1]
fresh(); assert run(80, 80) == 0                      # 가운데: 직진
fresh(); assert run(100, 100) < 0                     # 선이 오른쪽: 오른쪽으로
fresh(); assert run(60, 60) > 0                       # 왼쪽: 왼쪽으로
fresh(); assert run(-1, 100) < 0 and run(100, -1) < 0  # 한 구역만 보여도
fresh(); assert run(82, 95) < 0                       # T14 끝: 먼 쪽은 거의 가운데, 가까운 쪽이 오른쪽 → 오른쪽으로
fresh(); a1 = run(85, 85, 5); a2 = run(85, 85, 60); assert a1 < 0 and a2 < a1  # 비켜난 채 머물면 쌓여서 더 꺾음
fresh(); assert run(100, 100, 200) == -25             # 오래 비켜나 있으면 쌓여서 최대 각도에서 자름
fresh(); run(80, 80, 40); m = follow(150, 150); assert m[0] == 50, m  # 한 장만 크게 비켜난 건 한 걸음 평균이라 바로 안 멈춤
for _ in range(30):                                   # 0.6초쯤 계속 크게 비켜나면
    m = follow(150, 150)
assert m == (0, -dt.SEARCH_ANGLE), m                  # 크게(1.5 cm 넘게) 비켜나면 제자리에서 선 쪽으로
for _ in range(40):
    m = follow(82, 82)
assert m[0] == 50, m                                  # 가운데로 돌아오면 바로 다시 걸음
assert follow(-1, -1) == (50, -25)                    # 놓치면 잠깐(0.6초)은 걸으면서 마지막으로 본 쪽(오른쪽)으로 크게
fresh(); run(60, 60); assert follow(-1, -1) == (50, 25)  # 왼쪽에서 놓치면 왼쪽
for _ in range(40):                                   # 0.6초 넘게 안 보이면 제자리에서 그쪽으로 돌며 찾음
    m = follow(-1, -1)
assert m == (0, dt.SEARCH_ANGLE), m
assert follow(70, 70)[0] == 50                        # 다시 보이면 바로 걷기
fresh(); run(60, 60)
dt.gyro["yaw"] = 0.0
for _ in range(40):
    follow(-1, -1)
dt.gyro["yaw"] = 95.0; sent.clear(); follow(-1, -1)  # 90° 넘게 돌아도 못 찾으면 멈추고 E lost
assert dt.st["mode"] is None and moves[-1] == (0, 0) and any("|lost|" in x for x in sent), sent
fresh(); assert run(85, 15) > 0                       # 곡선: 먼 점은 가운데여도 가까운 점이 왼쪽 끝이면 왼쪽으로 크게
# 아래 구역만 보이면: 발 바로 앞이라 같은 칸이어도 cm로는 덜 비켜남 → 덜 꺾음
fresh(); a_mid = run(-1, 60, 3)
fresh()
for _ in range(3):
    Cam.line = [bytes(7), bytes(7), bytes([5, 60, 100, 0, 0, 0, 0])]
    clock[0] += 20
    dt.tick_motion()
assert 0 < moves[-1][1] < a_mid and moves[-1][0] == 50, (moves[-1], a_mid)  # 선이 보이니 걷기는 계속
assert dt.ch_line() == 60
fresh(); run(80, 80, 20)
for _ in range(10):                                   # 가운데여도 왼쪽으로 돌고 있으면(yaw가 늘면) 오른쪽으로 눌러 줌(자이로)
    dt.gyro["yaw"] += 0.4; m = follow(80, 80)
assert m[1] < 0, m
fresh()                                               # 한 걸음마다 좌우로 흔들리는 선(머리 흔들림)은 평균 0이라 크게 안 꺾음
for k in range(70):
    m = follow(80 + (20 if (k // 9) % 2 else -20), 80 + (15 if (k // 9) % 2 else -15))
assert abs(m[1]) <= 8, m
Cam.line = [bytes(7), bytes([5, 80, 60, 70, 50, 90, 70]), bytes(7)]
cmd("S")
# 선 위치: 가운데 구역 먼저, 없으면 위 구역, 둘 다 없으면 -1
assert dt.ch_line() == 80
Cam.line = [bytes([5, 30, 20, 20, 10, 40, 30]), bytes(7), bytes(7)]; assert dt.ch_line() == 30
Cam.line = [bytes(7), bytes([5, 80, 60, 0, 0, 0, 0]), bytes([5, 40, 100, 0, 0, 0, 0])]; assert dt.ch_line() == 40  # 아래 먼저
Cam.line = [bytes(7), bytes(7), bytes(7)]; assert dt.ch_line() == -1
Cam.line = [bytes(7), bytes([5, 80, 60, 70, 50, 90, 70]), bytes(7)]
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
# IMU: 가만히 있을 때의 자이로 기준값을 빼면 방향이 돌지 않고, 기울기는 가속도로 0
assert all(abs(a - b) < 1e-9 for a, b in zip(dt.gyro["bias"], [65.8, 6.2, 32.1])), dt.gyro
assert dt.read("roll") == 0 and dt.read("pitch") == 0
for _ in range(50):
    clock[0] += 20
    dt.tick_yaw()
assert abs(dt.gyro["yaw"]) < 1e-6, dt.gyro
# 걷는 중에 F가 다시 오면(Pi가 시간을 늘림) 쌓인 보정을 지우지 않음
fresh(); run(100, 100, 20); i0 = dt.st["i"]; cmd("F", 50, 25, 10000); assert dt.st["i"] == i0 != 0
cmd("S")
print("sim_check ok")
