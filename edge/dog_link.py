"""보드와 CMD|…|$ 주고받기 (USB 시리얼). docs/protocol.md 1절

- 포트는 DTR·RTS를 끈 채 연다 (보드가 다시 켜지지 않게)
- 받은 줄은 protocol.parse_dog로 나눠 inbox(queue)에 ["E", t, "stop", "13.0"] 처럼 쌓는다
- 마지막으로 보낸 지 HEARTBEAT_S가 지나면 HEARTBEAT_CMD를 보내 보드의 2초 감시를 막는다
- 케이블이 빠지면 2초마다 다시 연다
"""
import logging
import queue
import sys
import threading
import time

from common import protocol
from edge import config

log = logging.getLogger("edge.dog")


def battery_pct(v):
    """보드 배터리 값을 %로. 100보다 크면 mV로 보고 2S 리튬(6.4~8.4V) 기준으로 바꿈 (확인 필요)"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    if v > 100:
        v = (v - 6400) / (8400 - 6400) * 100
    return int(max(0, min(100, round(v))))


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return s


class BoardState:
    """보드가 보낸 줄로 알 수 있는 것: 측정값, 버전, 마지막 성공·실패"""

    def __init__(self, chans=config.STREAM_CHANS, event_chans=("color",)):
        self.event_chans = set(event_chans)
        self.set_stream(chans)
        self.values = {}         # {"bat": 7600.0, "sonar": 32.1, "line": 80.0}
        self.version = None
        self.commands = None     # V에 온 지원 명령 ["F", "S", …] (V를 받기 전에는 None)
        self.board_chans = None  # V에 온 숫자 채널 (보드에 비전 모듈이 없으면 line이 빠짐)
        self.last_error = None   # ("F", "비전 모듈 없음")

    def set_stream(self, chans):
        names = [c for c in chans.split(",") if c] if isinstance(chans, str) else list(chans)
        self.chans = [c for c in names if c not in self.event_chans]  # D에 오는 순서

    def update(self, parts):
        """한 줄 반영. 이벤트면 (채널, 값)을 돌려줌 → agent가 판단"""
        kind = parts[0]
        if kind == "D":
            for name, v in zip(self.chans, parts[2:]):
                self.values[name] = _num(v)
        elif kind == "E" and len(parts) >= 4:
            return parts[2], _num(parts[3])
        elif kind == "V" and len(parts) >= 2:
            self.version = parts[1]
            if len(parts) >= 3:
                self.commands = [c for c in parts[2].split(",") if c]
            if len(parts) >= 4:
                self.board_chans = [c for c in parts[3].split(",") if c]
            if len(parts) >= 5:  # V|버전|명령들|숫자 채널|이벤트 채널
                self.event_chans = set(c for c in parts[4].split(",") if c)
        elif kind == "!":
            self.last_error = tuple(parts[1:3])
            log.warning("보드가 명령을 못 함: %s", "|".join(parts[1:]))
        return None

    @property
    def battery(self):
        return battery_pct(self.values.get("bat"))

    @property
    def sonar_cm(self):
        v = self.values.get("sonar")
        return v if isinstance(v, float) and v >= 0 else None

    @property
    def line(self):
        """선 중심 x (0~159, 가운데 80). 못 찾았으면 -1, line 채널을 안 받으면 None"""
        v = self.values.get("line")
        return v if isinstance(v, float) else None


def line_side(x, lo=60, hi=100):
    """보드 F와 같은 기준으로 꺾는 쪽 (firmware/dog/main.py c_follow): lost · left · right · straight"""
    if x is None or x < 0:
        return "lost"
    return "left" if x < lo else "right" if x > hi else "straight"


class DogLink:
    def __init__(self, port=config.DOG_PORT, baud=config.DOG_BAUD):
        self.port, self.baud = port, baud
        self.inbox = queue.Queue()
        self.ser = None
        self.opens = 0          # 포트를 연 횟수. 늘면 다시 붙은 것 (그사이 보드는 2초 감시로 멈췄을 수 있음)
        self.quiet_breaks = 0   # BOARD_QUIET_S 넘게 못 보낸 횟수 (보드가 감시로 멈췄을 수 있음)
        self._tx_lock = threading.Lock()
        self._last_tx = 0.0
        self._closing = threading.Event()

    @property
    def connected(self):
        return self.ser is not None

    def open(self):
        self._try_open()
        threading.Thread(target=self._rx_loop, name="dog-rx", daemon=True).start()
        threading.Thread(target=self._hb_loop, name="dog-hb", daemon=True).start()

    def _try_open(self):
        import serial

        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = self.port, self.baud, 0.2
            # 보드가 리셋되지 않게: Windows는 열기 전에 DTR·RTS를 끄고, Linux는 커널이 켠 그대로 둔다(하나씩 끄면 리셋)
            if sys.platform == "win32":
                s.dtr = s.rts = False
            s.open()
            self.ser = s
            self.opens += 1
            log.info("보드 연결 %s @%d", self.port, self.baud)
        except Exception as e:
            self.ser = None
            log.warning("보드 포트 열기 실패 %s: %s", self.port, e)

    def close(self):
        self._closing.set()
        if self.ser:
            try:
                self.send("S")
                self.ser.close()
            finally:
                self.ser = None

    def send(self, name, *args):
        line = protocol.dog_cmd(name, *args)
        with self._tx_lock:
            if not self.ser:
                return False
            try:
                self.ser.write(line)
                now = time.monotonic()
                if self._last_tx and now - self._last_tx > config.BOARD_QUIET_S:
                    self.quiet_breaks += 1
                    log.warning("보드에 %.1f초 못 보냄 → 보드가 감시로 멈췄을 수 있음", now - self._last_tx)
                self._last_tx = now
                return True
            except Exception as e:
                log.warning("보드로 보내기 실패: %s", e)
                self._drop()
                return False

    def _drop(self):
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def _rx_loop(self):
        while not self._closing.is_set():
            if not self.ser:
                time.sleep(2)
                self._try_open()
                continue
            try:
                raw = self.ser.readline()
            except Exception as e:
                log.warning("보드 읽기 실패: %s", e)
                self._drop()
                continue
            if raw:
                parts = protocol.parse_dog(raw)
                if parts:
                    self.inbox.put(parts)

    def _hb_loop(self):
        while not self._closing.is_set():
            if self.ser and time.monotonic() - self._last_tx > config.HEARTBEAT_S:
                self.send(config.HEARTBEAT_CMD)
            time.sleep(0.2)


class FakeDog:
    """보드 없이 개발용. 명령에 ok로 답하고, T를 받으면 측정값을 흉내 냄.
    fake.sonar·fake.line 값을 바꾸면 그 값이 D로 나가고, inject()로 아무 줄이나 넣을 수 있다.
    고장 흉내: fail={"F": "비전 모듈 없음"} → CMD|!|F|…|$ · silent={"F"} → 답 없음 ·
    disconnect()/reconnect() → 케이블 빠짐·다시 꽂음 · mute=True → 측정값 끊김.
    auto_stream=False면 스레드 없이 pump()를 부를 때마다 D 한 줄 (시험에서 시계를 직접 돌릴 때)"""

    def __init__(self, auto_stream=True):
        self.inbox = queue.Queue()
        self.sent = []          # 시험에서 확인용 [("F", 50, 25, 10000), …]. 연결이 끊긴 동안 보낸 것은 안 들어감
        self.sonar = 60.0
        self.line = 80.0        # 선 중심 x, -1이면 선을 못 찾음
        self.bat = 7800
        self.period = 0
        self.chans = []
        self.connected = True
        self.opens = 1
        self.quiet_breaks = 0
        self.moving = False     # 보드가 걷고 있다고 볼 수 있는지 (F ok 뒤 True, S 뒤 False)
        self.fail = {}
        self.silent = set()
        self.mute = False
        self.auto_stream = auto_stream
        self._t0 = time.monotonic()
        self._closing = threading.Event()

    def open(self, stream=True):
        if stream and self.auto_stream:
            threading.Thread(target=self._stream, name="fakedog", daemon=True).start()

    def close(self):
        self.send("S")
        self._closing.set()

    def inject(self, *parts):
        self.inbox.put([str(p) for p in parts])

    def disconnect(self):
        """케이블 빠짐: 보내기 실패, 측정값 없음. 진짜 보드는 2초 감시로 멈춤"""
        self.connected = False
        self.moving = False

    def reconnect(self):
        self.connected = True
        self.opens += 1

    def send(self, name, *args):
        if not self.connected:
            return False
        self.sent.append((name, *args))
        if name in self.silent:
            return True
        if name in self.fail:
            self.inject("!", name, self.fail[name])
            return True
        if name == "V":
            self.inject("V", "fake-1", "F,U,S,P,L,A,T,V", "bat,sonar,line", "color")
            return True
        if name == "T":
            self.period = int(args[0])
            self.chans = [c for c in str(args[1]).split(",") if c and c != "color"] if len(args) > 1 else []
        elif name == "F":
            self.moving = True
        elif name in ("S", "P"):
            self.moving = False
        self.inject(name, "ok")
        return True

    def pump(self):
        """측정값 D 한 줄 (T를 받았고, 연결돼 있고, mute가 아닐 때)"""
        if self.period and self.connected and not self.mute:
            t = int((time.monotonic() - self._t0) * 1000)
            vals = {"bat": self.bat, "sonar": self.sonar, "line": self.line}
            self.inject("D", t, *[vals.get(c, -1) for c in self.chans])

    def _stream(self):
        while not self._closing.is_set():
            if self.period:
                self.pump()
                time.sleep(self.period / 1000)
            else:
                time.sleep(0.05)
