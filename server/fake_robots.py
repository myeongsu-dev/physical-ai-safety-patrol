"""가짜 로봇 4대. 실제 로봇(edge/agent.py)과 같은 MQTT 메시지로 상태·이벤트를 흉내 낸다.
로봇 없이 서버·대시보드를 개발할 때 쓴다. 실행: python -m server.fake_robots
셀프 체크(브로커 없이): python -m server.fake_robots --selfcheck

- 1초마다 potent/{로봇}/state (marker, status, role, battery, sonar_cm, temp_c, hold, version)
  hold: 다시 걷지 않는 이유 (Pi와 같게 stop · wait · guide, patrol · resume으로 풀림), 없으면 null
- 가끔 obstacle · helmet · heat 이벤트 (사진은 없음, photo는 null)
- potent/{로봇}/cmd 에 반응: patrol(role) · stop · resume · go(zone) · wait · dock · guide(arrow)
- potent/mode 에 반응: evacuate면 순찰을 멈추고 guide 대기, normal이면 idle
"""
import argparse
import logging
import os
import random
import time

from common import protocol
from server import map_config

log = logging.getLogger("fake")

STATUSES = ("idle", "patrol", "blocked", "heat_check", "go", "guide", "dock")
MOVING = ("patrol", "go", "dock")
MARKER_SEC = 3          # 마커 하나 지나는 데 걸리는 시간(초)
DRAIN_PER_MARKER = 0.4  # 마커 하나 지날 때 줄어드는 배터리(%)
BLOCK_SEC = 6           # 막힘이 풀리기까지(초)
HEAT_SEC = 8            # 과열 확인에 머무는 시간(초)


class FakeRobot:
    def __init__(self, name, battery=100.0, rng=None, clock=time.time):
        self.name, self.rng, self.clock = name, rng or random.Random(), clock
        self.battery = battery
        self.status = "idle"
        self.role = None
        self.marker = map_config.DOCK_MARKER
        self.path = []          # 앞으로 지날 마커
        self.lap = []           # 순찰 고리 (다 돌면 다시 채움)
        self.target = None
        self.resume_to = None
        self.until = 0.0        # blocked · heat_check 끝나는 시각
        self.next_marker = 0.0
        self.mode = "normal"
        self.sonar_cm = 120.0
        self.temp_c = 24.0
        self.hold = None        # 다시 걷지 않는 이유 (Pi edge/agent.py의 hold와 같은 뜻)

    # ---------- 보낼 것 ----------
    def state(self):
        return {"marker": self.marker, "status": self.status, "role": self.role,
                "battery": round(self.battery), "sonar_cm": round(self.sonar_cm, 1),
                "temp_c": round(self.temp_c, 1), "hold": self.hold, "version": "fake"}

    def _event(self, type, **fields):
        return protocol.event(type, marker=self.marker, photo=None, **fields)

    # ---------- 받은 것 ----------
    def on_cmd(self, msg):
        cmd = msg.get("cmd")
        if cmd not in protocol.COMMANDS:
            log.warning("%s: 모르는 명령 버림 %s", self.name, msg)
            return
        now = self.clock()
        if cmd == "patrol":
            role = msg.get("role", self.role)
            if role not in protocol.ROLES:
                log.warning("%s: 역할 없이 patrol, 버림", self.name)
                return
            if role != self.role or not self.lap:
                self.role = role
                route = map_config.ROUTES[role]
                start = route["entry"] if self.marker == map_config.DOCK_MARKER else []
                self.path = [m for m in start if m != self.marker]
                self.lap = []
            self.status, self.target, self.hold = "patrol", None, None
        elif cmd == "stop":
            self.hold = "stop"
            if self.status in MOVING:
                self.resume_to = self.status
            self.status = "idle"
        elif cmd == "resume":
            self.status = self.resume_to or "patrol"
            self.resume_to, self.hold = None, None
        elif cmd in ("go", "dock"):
            self.target = map_config.DOCK_MARKER if cmd == "dock" else msg.get("zone")
            if self.target not in map_config.MARKERS:
                log.warning("%s: 모르는 구역 %s", self.name, self.target)
                return
            self.path = [self.target]       # 지도 경로 계산은 dispatch에서. 여기선 바로 그 마커로
            self.status = cmd
        elif cmd == "wait":
            self.status, self.hold = "idle", "wait"
        elif cmd == "guide":
            self.status, self.hold = "guide", "guide"
        self.next_marker = max(self.next_marker, now + MARKER_SEC)

    def on_mode(self, msg):
        mode = msg.get("mode")
        if mode not in protocol.MODES:
            return
        self.mode = mode
        if mode == "evacuate" and self.status in MOVING:
            self.status = "idle"          # 서버가 guide 명령으로 화살표를 정해 줄 때까지 대기
        elif mode == "normal" and self.status == "guide":
            self.status = "idle"

    # ---------- 1초마다 ----------
    def tick(self, chance=1.0):
        """1초 진행. 보낼 이벤트 목록을 돌려준다. chance는 이벤트 확률 배수(시험에서 0으로)."""
        now, out = self.clock(), []
        self.sonar_cm = 80 + self.rng.random() * 60
        self.temp_c += (24 - self.temp_c) * 0.2 + self.rng.uniform(-0.3, 0.3)

        if self.status in ("blocked", "heat_check"):
            if self.status == "blocked":
                self.sonar_cm = 12.0
            else:
                self.temp_c = max(self.temp_c, 46.0)
            if now >= self.until:
                self.status = self.resume_to or "patrol"
                self.resume_to = None
            return out

        if self.status in MOVING and now >= self.next_marker:
            self._step()
            self.next_marker = now + MARKER_SEC
            if self.status == "patrol":
                r = self.rng.random() / max(chance, 1e-9)
                if r < 0.04:
                    out.append(self._block(now))
                elif r < 0.07:
                    out.append(self._event("helmet", conf=round(self.rng.uniform(0.6, 0.95), 2)))
                elif r < 0.08:
                    out.append(self._heat(now))
        return out

    def _step(self):
        if not self.path and self.status == "patrol":
            if not self.lap:
                self.lap = list(map_config.ROUTES[self.role]["loop"])
            self.path = [self.lap.pop(0)]
        if not self.path:
            return
        self.marker = self.path.pop(0)
        self.battery = max(0.0, self.battery - DRAIN_PER_MARKER)
        if self.status in ("go", "dock") and self.marker == self.target:
            self.status, self.target = ("idle" if self.status == "go" else "dock"), None
            if self.marker == map_config.DOCK_MARKER:
                self.battery = 100.0       # 관리실에서 사람이 충전했다고 침

    def _block(self, now):
        self.resume_to, self.status, self.until = self.status, "blocked", now + BLOCK_SEC
        self.sonar_cm = 12.0
        return self._event("obstacle", sonar_cm=12, person=self.rng.random() < 0.3)

    def _heat(self, now):
        self.resume_to, self.status, self.until = self.status, "heat_check", now + HEAT_SEC
        self.temp_c = round(self.rng.uniform(45.5, 49.0), 1)
        return self._event("heat", temp_c=self.temp_c, flame=False, conf=0.0)


# ---------- MQTT로 돌리기 ----------
def run(host, port, names, seed=None):
    import paho.mqtt.client as mqtt

    rng = random.Random(seed)
    bots = {n: FakeRobot(n, battery=rng.uniform(60, 100), rng=random.Random(rng.random())) for n in names}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="potent-fake-robots")

    def on_connect(c, userdata, flags, rc, props=None):
        log.info("브로커 연결 %s:%s (%s)", host, port, rc)
        for n in bots:
            c.subscribe(protocol.topic(n, "cmd"), qos=1)
        c.subscribe(protocol.MODE_TOPIC, qos=1)

    def on_message(c, userdata, m):
        try:
            msg = protocol.unpack(m.payload)
        except ValueError as e:
            log.warning("깨진 메시지 버림 %s: %s", m.topic, e)
            return
        if m.topic == protocol.MODE_TOPIC:
            for b in bots.values():
                b.on_mode(msg)
            return
        t = protocol.parse_topic(m.topic)
        if t and t[1] == "cmd" and t[0] in bots:
            log.info("%s <- %s", t[0], msg)
            bots[t[0]].on_cmd(msg)

    client.on_connect, client.on_message = on_connect, on_message
    client.connect(host, port)
    client.loop_start()
    log.info("가짜 로봇 %s 시작. 서버가 patrol 명령을 보내면 움직인다", ",".join(names))
    try:
        while True:
            for n, b in bots.items():
                for e in b.tick():
                    log.info("%s 이벤트 %s", n, e["type"])
                    client.publish(protocol.topic(n, "event"), protocol.pack(e), qos=1)
                client.publish(protocol.topic(n, "state"), protocol.pack(b.state()), qos=0)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


def selfcheck():
    now = [0.0]
    b = FakeRobot("A", battery=50, rng=random.Random(1), clock=lambda: now[0])
    assert b.state()["status"] == "idle" and b.state()["marker"] == map_config.DOCK_MARKER
    b.on_cmd(protocol.command("patrol", role="A"))
    seen = []
    for _ in range(200):
        now[0] += 1
        assert b.tick(chance=0) == []
        seen.append(b.marker)
    route = map_config.ROUTES["A"]
    assert seen[MARKER_SEC] == route["entry"][1], seen[:10]      # 도크 → 14 → 고리
    assert set(route["loop"]) <= set(seen) and 9 not in seen      # A 고리만 돈다
    assert b.state()["role"] == "A" and b.battery < 50
    b.on_cmd({"cmd": "stop"}); assert b.status == "idle" and b.state()["hold"] == "stop"
    b.on_cmd({"cmd": "resume"}); assert b.status == "patrol" and b.state()["hold"] is None
    b.on_cmd(protocol.command("dock"))
    for _ in range(10):
        now[0] += 1; b.tick(chance=0)
    assert b.status == "dock" and b.marker == 15 and b.battery == 100
    b.on_mode({"mode": "evacuate"}); b.on_cmd(protocol.command("guide", arrow="left"))
    assert b.status == "guide"
    b.on_mode({"mode": "normal"}); assert b.status == "idle"
    # 이벤트가 나면 형식이 protocol과 맞는지
    c = FakeRobot("C", rng=random.Random(2), clock=lambda: now[0])
    c.on_cmd(protocol.command("patrol", role="B"))
    kinds = set()
    for _ in range(3000):
        now[0] += 1
        for e in c.tick(chance=5):
            assert e["type"] in protocol.EVENT_TYPES and "ts" in e and "marker" in e
            protocol.unpack(protocol.pack(e))
            kinds.add(e["type"])
    assert kinds == {"obstacle", "helmet", "heat"}, kinds
    assert all(s in STATUSES for s in (b.status, c.status))
    print("fake_robots ok: 이벤트", sorted(kinds))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="가짜 로봇 (MQTT)")
    p.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    p.add_argument("--robots", default="A,B,C,D")
    p.add_argument("--seed", type=int)
    p.add_argument("--selfcheck", action="store_true")
    a = p.parse_args()
    if a.selfcheck:
        selfcheck()
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
        run(a.host, a.port, a.robots.split(","), a.seed)
