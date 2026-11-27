"""edge 셀프 체크. 장치·브로커 없이 PC에서 판단 흐름을 확인. 실행: python -m edge.selfcheck → "edge ok" """
import tempfile
from pathlib import Path

from common import protocol
from edge import config
from edge.agent import Agent
from edge.camera import SOI, EOI, Frame, split_jpegs
from edge.detect import (CLASS_CONF, CLASSES, Det, Voter, class_names, class_order_problems, filter_conf,
                         find_model, has_person, no_helmet)
from edge.dog_link import BoardState, FakeDog, battery_pct, line_side
from edge.reporter import Reporter, photo_name, photo_url_from
from edge.thermo import FakeThermo, raw_to_c


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Cam:
    def __init__(self, clock):
        self.clock = clock

    def open(self):
        pass

    def close(self):
        pass

    def latest(self, max_age=1.0):
        return Frame(self.clock(), jpeg=SOI + b"photo" + EOI, image="그림")  # cv2 없이 (CI)


class Det1:
    """정해 둔 결과를 돌려주는 가짜 모델"""

    def __init__(self, dets):
        self.dets, self.ready, self.version = dets, True, "test"

    def __call__(self, image):
        return self.dets


def check_parts():
    a, b = SOI + b"1" + EOI, SOI + b"22" + EOI
    frames, rest = split_jpegs(b"junk" + a + b"xx" + b + SOI + b"3")
    assert frames == [a, b] and rest == SOI + b"3"
    assert raw_to_c(15091) == 28.67
    assert battery_pct(8400) == 100 and battery_pct(6400) == 0 and battery_pct(55) == 55 and battery_pct(-1) is None

    st = BoardState("bat,sonar,color")
    st.update(["D", "100", "7400", "31.5"])
    assert st.values == {"bat": 7400.0, "sonar": 31.5} and st.battery == 50 and st.sonar_cm == 31.5
    assert st.update(["E", "120", "stop", "12.0"]) == ("stop", 12.0)
    assert st.update(["E", "130", "color", "3"]) == ("color", 3.0)

    v = Voter(5, 3)
    assert [v.push(x) for x in (1, 0, 1, 0, 1)] == [False, False, False, False, True]
    assert v.push(1) is False  # 확정되면 다시 셈
    assert no_helmet([Det("helmet", 0.9, ()), Det("head", 0.7, ())]) == (True, 0.7)

    with tempfile.TemporaryDirectory() as d:
        for ver in ("v1", "v2", "v10"):
            (Path(d) / "safety" / ver / "best_ncnn_model").mkdir(parents=True)
        assert find_model("safety", d).parent.name == "v10"
        assert find_model("helmet", d) is None

    # 통합 모델: 클래스별 기준 (head 는 HELMET_CONF, fire 는 FLAME_CONF), 모르는 클래스는 버림
    cc = {"person": 0.5, "head": 0.5, "helmet": 0.5, "fire": 0.4}
    kept = filter_conf([Det("head", 0.45, ()), Det("fire", 0.45, ()), Det("vest", 0.99, ())], cc)
    assert [d.cls for d in kept] == ["fire"], kept
    assert set(CLASS_CONF) == {"person", "head", "helmet", "fire"}

    # 클래스 번호: 0 fire · 1 head · 2 helmet · 3 person. 이름 없는 모델은 번호로 이름을 붙임
    assert CLASSES == ("fire", "head", "helmet", "person")
    assert class_names({0: "class0", 1: "class1", 2: "class2", 3: "class3"}) == dict(enumerate(CLASSES))
    assert class_names(None) == dict(enumerate(CLASSES))
    good = {0: "fire", 1: "head", 2: "helmet", 3: "person"}
    assert class_names(good) == good and class_order_problems(good) == []
    old = {0: "person", 1: "head", 2: "helmet", 3: "fire"}   # 예전 순서 → 0번·3번 어긋남 알림
    assert [i for i, _, _ in class_order_problems(class_names(old))] == [0, 3]
    assert has_person([Det("helmet", 0.9, ())]) and not has_person([Det("fire", 0.9, ())])

    assert photo_name("B", 0, ()).startswith("B_") and photo_name("B", 0, ()).endswith(".jpg")
    n = photo_name("B", 0, ())
    assert photo_name("B", 0, {n}) == n[:-4] + "_2.jpg"
    assert photo_url_from('{"url": "/photos/x.jpg"}', "x.jpg") == "/photos/x.jpg"
    assert photo_url_from("oops", "x.jpg") == "/photos/x.jpg"


def check_reporter(spool):
    r = Reporter("B", spool_dir=spool)
    r.report_event("obstacle", jpeg=SOI + b"p" + EOI, marker=14, sonar_cm=12, person=False)
    assert Reporter("B", spool_dir=spool).outbox, "다시 켜도 쌓인 이벤트가 남아야 함"
    sent = []
    assert r.flush_one(publish=lambda t, p: True, upload=lambda n: None) is False  # 사진 실패 → 다음에 다시
    assert r.flush_one(publish=lambda t, p: False, upload=lambda n: "/photos/" + n) is False  # 끊김
    assert r.outbox, "못 보냈으면 남아 있어야 함"
    while r.outbox:
        r.flush_one(publish=lambda t, p: sent.append((t, protocol.unpack(p))) or True, upload=lambda n: "/photos/" + n)
    t, msg = sent[-1]
    assert t == "potent/B/event" and msg["type"] == "obstacle" and msg["photo"].startswith("/photos/B_")
    assert not any(k.startswith("_") for k in msg)
    assert not Reporter("B", spool_dir=spool).outbox


def make(spool, model=None, temp=25.0):
    clock = Clock()
    dog = FakeDog(auto_stream=False)   # 측정값은 run()이 한 걸음마다 한 줄씩 (시계를 직접 돌림)
    rep = Reporter("B", spool_dir=spool)
    events = []
    rep.report_event = lambda type, jpeg=None, **f: events.append((type, bool(jpeg), f))
    states = []
    rep.publish_state = states.append
    rep.start = lambda: None
    rep.stop = lambda: None
    th = FakeThermo(temp, clock=clock)
    ag = Agent(dog, Cam(clock), th, rep, model=model, clock=clock, version="test")
    ag.start()
    return ag, dog, th, clock, events, states


def run(ag, clock, secs, dt=0.1):
    for _ in range(int(round(secs / dt))):
        clock.t += dt
        ag.dog.pump()
        ag.step()


def check_agent(spool):
    # 순찰 시작 → F, 측정값 → state
    ag, dog, th, clock, events, states = make(spool)
    assert dog.sent[0][0] == "T" and ("V",) in dog.sent
    dog.bat, dog.sonar = 7400, 50.0
    ag.reporter.inbox.put(("cmd", protocol.command("patrol")))
    run(ag, clock, 1.2)
    assert ag.status == "patrol" and ("F", config.PATROL_STRIDE, config.PATROL_ANGLE, config.MOVE_MS) in dog.sent
    assert states[-1]["battery"] == 50 and states[-1]["sonar_cm"] == 50.0 and states[-1]["version"] == "test"
    assert set(states[-1]) == {"marker", "status", "role", "battery", "sonar_cm", "temp_c", "version", "hold"}

    # 마커: 새 마커를 읽으면 1초를 기다리지 않고 state를 바로 보냄. 같은 마커가 다시 와도 더 보내지 않음
    run(ag, clock, 0.5)
    n = len(states)
    dog.inject("E", 1, "color", 3)
    run(ag, clock, 0.1)
    assert ag.marker == 3.0 and len(states) == n + 1 and states[-1]["marker"] == 3.0
    dog.inject("E", 1, "color", 3)
    run(ag, clock, 0.1)
    assert len(states) == n + 1

    # 보드 막힘 정지 → obstacle + 사진 + 음성, 풀리면 다시 걸음
    dog.sent.clear()
    dog.sonar = 12.0   # 보드가 멈출 만큼 가까움
    dog.inject("E", 2, "stop", 12.0)
    run(ag, clock, 0.2)
    assert ag.status == "blocked"
    assert events[-1][0] == "obstacle" and events[-1][1] and events[-1][2]["person"] is False
    assert ("A", config.VOICE_OBSTACLE, config.VOICE_VOLUME) in dog.sent
    dog.sonar = 30.0
    dog.inject("E", 3, "resume", 30.0)
    run(ag, clock, 0.2)
    assert ag.status == "patrol" and dog.sent[-1][0] == "F"

    # Pi 보조 정지: 14cm 안 3번 연속
    n = len(events)
    dog.sonar = 10.0
    run(ag, clock, 0.4)
    assert ag.status == "blocked" and ("S",) in dog.sent and len(events) == n + 1
    dog.sonar = 25.0
    run(ag, clock, 0.2)
    assert ag.status == "patrol"

    # 과열: 45℃ 초과 → 멈춤 → 2초마다 3번 → heat 보고 → 서버 명령 없으면 돌아감
    th.value = 55.0
    run(ag, clock, 0.6)
    assert ag.status == "heat_check" and dog.sent[-1] == ("S",)
    run(ag, clock, config.HEAT_RECHECK_S * config.HEAT_CHECKS + 0.5)
    assert events[-1][0] == "heat" and events[-1][2]["temp_c"] == 55.0 and events[-1][2]["flame"] is False
    th.value = 25.0
    run(ag, clock, config.HEAT_HOLD_S + 1)
    assert ag.status == "patrol" and ag.heat is None

    # 서버 명령: guide → 정지 + LED 화살표, normal 모드 → 끔
    ag.reporter.inbox.put(("cmd", protocol.command("guide", arrow="left")))
    run(ag, clock, 0.2)
    assert ag.status == "guide" and dog.sent[-1] == ("L", "arrow", "left")
    ag.reporter.inbox.put(("mode", {"mode": "normal"}))
    run(ag, clock, 0.2)
    assert ag.status == "idle" and dog.sent[-1] == ("L", "clear")

    # go: 목표 마커에 닿으면 멈춤
    ag.reporter.inbox.put(("cmd", protocol.command("go", zone=7)))
    run(ag, clock, 0.2)
    assert ag.status == "go" and dog.sent[-1][0] == "F"
    dog.inject("E", 6, "color", 7)
    run(ag, clock, 0.2)
    assert ag.status == "idle" and ag.target is None

    # dock: 서버는 zone 없이 보냄 → 충전 스테이션(DOCK_MARKER)까지 가서 멈춤. 가는 길의 다른 마커에서는 안 멈춤
    ag.reporter.inbox.put(("cmd", protocol.command("dock")))
    run(ag, clock, 0.2)
    assert ag.status == "dock" and ag.target == config.DOCK_MARKER and dog.sent[-1][0] == "F"
    for m in (12, 15):
        dog.inject("E", 7, "color", m)
        run(ag, clock, 0.2)
        assert ag.status == "dock"
    dog.inject("E", 8, "color", config.DOCK_MARKER)
    run(ag, clock, 0.2)
    assert ag.status == "idle" and ag.target is None and dog.sent[-1] == ("S",)
    assert states[-1]["marker"] == config.DOCK_MARKER and states[-1]["status"] == "idle"
    # zone을 주면 그 마커
    ag.on_command(protocol.command("dock", zone=15))
    assert ag.target == 15

    # 안전모: 맨머리 5장 중 3장 → helmet 보고 1번 (쿨다운)
    ag, dog, th, clock, events, _ = make(spool, model=Det1([Det("person", 0.9, ()), Det("head", 0.8, ())]))
    run(ag, clock, 2.0)
    assert [e[0] for e in events] == ["helmet"] and events[0][2]["conf"] == 0.8
    assert ("A", config.VOICE_HELMET, config.VOICE_VOLUME) in dog.sent

    # 막혔는데 사람 → 기다림, 10초 넘게 서 있으면 obstacle(person=True)
    ag.on_command(protocol.command("patrol"))
    ag.last_event["helmet"] = clock()  # 안전모 보고는 빼고 봄
    events.clear()
    dog.sonar = 12.0
    dog.inject("E", 1, "stop", 12.0)
    run(ag, clock, 1.0)
    assert ag.status == "blocked" and [e[0] for e in events if e[0] == "obstacle"] == []
    run(ag, clock, 10.0)
    obs = [e for e in events if e[0] == "obstacle"]
    assert len(obs) == 1 and obs[0][2]["person"] is True

    # 과열 + 통합 모델이 3장 이상 fire → heat(flame=True) + flame. 과열 확인 중에는 안전모 판정 안 함
    ag, dog, th, clock, events, _ = make(spool, model=Det1([Det("fire", 0.77, ()), Det("head", 0.9, ())]), temp=60.0)
    run(ag, clock, config.HEAT_RECHECK_S * config.HEAT_CHECKS + 1.5)
    kinds = [e[0] for e in events]
    # 과열 보고 전까지는 helmet 이 끼지 않음 (보고 뒤에는 기존처럼 안전모 판정 다시 함)
    assert kinds[:2] == ["heat", "flame"] and events[0][2]["flame"] is True and events[1][2]["conf"] == 0.77

    # 다시 재니 내려감 → 보고 없음
    ag, dog, th, clock, events, _ = make(spool, temp=50.0)
    run(ag, clock, 0.6)
    th.value = 30.0
    run(ag, clock, config.HEAT_RECHECK_S * config.HEAT_CHECKS + 1)
    assert events == [] and ag.heat is None


def F():
    return ("F", config.PATROL_STRIDE, config.PATROL_ANGLE, config.MOVE_MS)


def check_messages(spool):
    """MQTT로 받은 것: 내 cmd만, 보관(retained) 명령·깨진 JSON은 버림, mode는 받음"""
    from types import SimpleNamespace as M

    r = Reporter("B", spool_dir=spool)
    on = lambda t, p, retain=False: r._on_message(None, None, M(topic=t, payload=p, retain=retain))  # noqa: E731
    on("potent/B/cmd", b'{"cmd":"patrol","role":"A"}')
    on("potent/A/cmd", b'{"cmd":"patrol","role":"A"}')     # 다른 로봇
    on("potent/B/cmd", b'{"cmd":"patrol"', )                # 깨진 JSON
    on("potent/B/cmd", b'[1, 2]')                           # 객체가 아님
    on("potent/B/cmd", b'{"cmd":"patrol"}', retain=True)    # 브로커에 남은 옛 명령
    on("potent/mode", b'{"mode":"normal"}')
    got = []
    while not r.inbox.empty():
        got.append(r.inbox.get())
    assert got == [("cmd", {"cmd": "patrol", "role": "A"}), ("mode", {"mode": "normal"})], got

    assert line_side(80) == "straight" and line_side(59) == "left" and line_side(101) == "right"
    assert line_side(-1) == "lost" and line_side(None) == "lost" and line_side(60) == line_side(100) == "straight"
    st = BoardState("bat,sonar,line,color")
    st.update(["V", "dog-1", "M,F,S,T,V", "bat,sonar,line", "color"])
    assert st.commands == ["M", "F", "S", "T", "V"] and st.board_chans == ["bat", "sonar", "line"]
    st.update(["D", "1", "7400", "-1", "-1"])
    assert st.sonar_cm is None and st.line == -1.0


def check_patrol(spool):
    """오늘 목표: 서버 patrol·stop·resume → F·S·F, 보드 답 확인, 정지 뒤 다시 걷지 않음, 안전 정지"""
    ag, dog, th, clock, events, states = make(spool)
    run(ag, clock, 0.5)
    status = lambda: ag.state()["status"]  # noqa: E731  서버에 보낼 상태 (states[-1]은 1초마다 실제로 보낸 것)
    hold = lambda: ag.state()["hold"]  # noqa: E731  서버가 자동 출발을 판단하는 정지 이유
    assert "hold" in ag.state() and hold() is None and states[-1]["hold"] is None

    # patrol(role A) → F → 보드 ok → state patrol, role A (ROBOT_ID B와 별개)
    dog.sent.clear()
    ag.reporter.inbox.put(("cmd", {"cmd": "patrol", "role": "A"}))
    run(ag, clock, 1.1)
    assert dog.sent.count(F()) == 1 and ag.acked and status() == "patrol" and states[-1]["role"] == "A"

    # 잘못된 명령: 모르는 역할·모르는 명령·cmd 없음 → 아무것도 안 바뀜
    dog.sent.clear()
    for bad in ({"cmd": "patrol", "role": "C"}, {"cmd": "fly"}, {"role": "A"}, {"cmd": 3}):
        ag.on_command(bad)
    run(ag, clock, 0.3)
    assert dog.sent == [] and ag.status == "patrol" and ag.role == "A"

    # 이동 유지: MOVE_MS - 2초마다 F를 다시 보냄
    run(ag, clock, config.MOVE_MS / 1000)
    assert dog.sent.count(F()) == 1 and status() == "patrol"

    # stop → S → idle. 그 뒤로는 MOVE_MS가 몇 번 지나도, 보드 done·늦은 ok가 와도 F 없음
    dog.sent.clear()
    ag.reporter.inbox.put(("cmd", {"cmd": "stop"}))
    run(ag, clock, 1.1)
    assert dog.sent[0] == ("S",) and status() == "idle" and not dog.moving and ag.hold == "stop"
    assert hold() == "stop" and states[-1]["hold"] == "stop"
    dog.inject("E", 9, "done", 1)
    dog.inject("F", "ok")
    run(ag, clock, config.MOVE_MS / 1000 * 3)
    assert F() not in dog.sent and status() == "idle" and not ag.acked

    # 정지 중 보드가 끊겼다 다시 붙어도 그대로 멈춰 있음
    dog.disconnect()
    run(ag, clock, 1.0)
    dog.reconnect()
    run(ag, clock, config.MOVE_MS / 1000)
    assert F() not in dog.sent and ag.status == "idle"

    # resume → 같은 역할로 다시 F
    ag.reporter.inbox.put(("cmd", {"cmd": "resume"}))
    run(ag, clock, 1.1)
    assert dog.sent.count(F()) == 1 and status() == "patrol" and states[-1]["role"] == "A"
    assert hold() is None and states[-1]["hold"] is None

    # 역할 없이 온 patrol은 지난 역할을 이어 감
    ag.on_command({"cmd": "patrol"})
    run(ag, clock, 0.3)
    assert ag.role == "A" and ag.status == "patrol"

    # 순찰 중 보드 연결이 끊김 → 안전 정지(idle). 다시 붙어도 저절로 걷지 않음 → resume으로만
    dog.sent.clear()
    dog.disconnect()
    run(ag, clock, 0.3)
    assert ag.status == "idle" and ag.hold == "link" and status() == "idle"
    dog.reconnect()
    run(ag, clock, config.MOVE_MS / 1000 * 2)
    assert F() not in dog.sent and ag.status == "idle"
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol" and dog.sent[-1] == F()

    # 다시 붙은 것만 알아도(포트를 다시 엶, 보드에 2초 넘게 못 보냄) 안전 정지
    for poke in (lambda: setattr(dog, "opens", dog.opens + 1), lambda: setattr(dog, "quiet_breaks", dog.quiet_breaks + 1)):
        poke()
        run(ag, clock, 0.2)
        assert ag.status == "idle" and ag.hold == "link"
        ag.on_command({"cmd": "resume"})
        run(ag, clock, 0.3)
        assert ag.status == "patrol"

    # 측정값(D)이 끊김(보드 재시작 등) → SENSOR_STALE_S 뒤 안전 정지
    dog.mute = True
    run(ag, clock, config.SENSOR_STALE_S + 0.3)
    assert ag.status == "idle" and ag.hold == "stream"
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "idle", "측정값이 없으면 resume도 안 함"
    dog.mute = False
    run(ag, clock, 0.2)
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol"

    # 선 유실: 잠깐(-1 한두 번)은 괜찮고, LINE_LOST_S 넘게 안 보이면 안전 정지. 선이 없으면 resume 안 됨
    dog.line = -1
    run(ag, clock, 0.3)
    dog.line = 75.0
    run(ag, clock, 0.3)
    assert ag.status == "patrol"
    dog.sent.clear()
    dog.line = -1
    run(ag, clock, config.LINE_LOST_S + 0.3)  # 그사이 F를 다시 보낼 수 있음(MOVE_MS) → 마지막이 S
    assert ag.status == "idle" and ag.hold == "line" and dog.sent[-1] == ("S",), dog.sent
    dog.sent.clear()
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "idle" and F() not in dog.sent
    dog.line = 82.0
    run(ag, clock, 0.2)
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol" and dog.sent[-1] == F()
    # 보드가 놓친 선을 제자리 돌며 찾다가 못 찾음(E lost) → Pi도 바로 멈춤
    dog.inject("E", 9, "lost", 91.0)
    run(ag, clock, 0.2)
    assert ag.status == "idle" and ag.hold == "line", ag.status
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol"

    # 막혀 서 있는 중에 보드가 다시 붙음 → 물건이 치워져도 저절로 출발하지 않음
    dog.sonar = 10.0
    run(ag, clock, 0.4)
    assert ag.status == "blocked"
    dog.opens += 1
    dog.sonar = 40.0
    run(ag, clock, 3.0)
    assert ag.status == "idle" and ag.hold == "link"
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol"

    # 보드가 F를 거부(CMD|!|F|…|$) → patrol로 알리지 않고 멈춤
    ag, dog, th, clock, events, states = make(spool)
    run(ag, clock, 0.5)
    dog.fail["F"] = "비전 모듈 없음"
    ag.on_command({"cmd": "patrol", "role": "B"})
    run(ag, clock, 2.0)
    assert ag.status == "idle" and ag.hold == "board" and all(s["status"] == "idle" for s in states)
    assert states[-1]["role"] == "B"

    # 보드가 F에 답이 없음 → ACK_TIMEOUT_S 뒤 S, idle
    del dog.fail["F"]
    dog.silent.add("F")
    dog.sent.clear()
    ag.on_command({"cmd": "patrol"})
    run(ag, clock, config.ACK_TIMEOUT_S + 0.5)
    assert ag.status == "idle" and ag.hold == "no_ack" and ("S",) in dog.sent
    assert ag.state()["hold"] == "no_ack" and states[-1]["hold"] == "no_ack"
    assert all(s["status"] == "idle" for s in states)
    dog.silent.clear()

    # 보드 동작 반복 오류(CMD|!|loop|…) → 보드가 멈췄으니 Pi도 idle
    ag.on_command({"cmd": "patrol"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol"
    dog.inject("!", "loop", "I2C 오류")
    run(ag, clock, 0.2)
    assert ag.status == "idle" and ag.hold == "board"

    # 보드가 없으면 patrol 안 함 (F를 보내지도, patrol로 알리지도 않음)
    dog.disconnect()
    dog.sent.clear()
    ag.on_command({"cmd": "patrol"})
    run(ag, clock, 1.1)
    assert dog.sent == [] and ag.status == "idle" and states[-1]["status"] == "idle"
    dog.reconnect()

    # 선 위에 없으면 patrol 안 함
    dog.line = -1
    run(ag, clock, 0.3)
    ag.on_command({"cmd": "patrol"})
    run(ag, clock, 0.3)
    assert ag.status == "idle" and F() not in dog.sent

    # 막힘 중 stop → 물건이 치워져도 다시 걷지 않음
    dog.line = 80.0
    run(ag, clock, 0.2)
    ag.on_command({"cmd": "patrol"})
    dog.sonar = 10.0
    run(ag, clock, 0.5)
    assert ag.status == "blocked"
    ag.on_command({"cmd": "stop"})
    dog.sonar = 40.0
    dog.sent.clear()
    run(ag, clock, config.MOVE_MS / 1000)
    assert ag.status == "idle" and F() not in dog.sent

    # 과열 확인으로 서 있는 중 보드가 다시 붙음 → 과열 확인이 끝나도 저절로 걷지 않음
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol"
    th.value = 50.0
    run(ag, clock, 0.6)
    assert ag.status == "heat_check"
    dog.opens += 1
    th.value = 25.0
    dog.sent.clear()
    run(ag, clock, config.HEAT_RECHECK_S * config.HEAT_CHECKS + 1)
    assert ag.heat is None and ag.status == "idle" and F() not in dog.sent and ag.resume_to == "patrol"

    # 프로그램 끝 → 보드 S, 서버에 idle
    ag.on_command({"cmd": "resume"})
    run(ag, clock, 0.3)
    assert ag.status == "patrol"
    dog.sent.clear()
    ag.stop()
    assert dog.sent[0] == ("S",) and states[-1]["status"] == "idle"


def check_web(spool):
    """디버그 웹 화면: 상태 JSON·기록·사진이 나오고 마지막 인식 결과가 담기는지 (빈 포트로 실제로 띄움)"""
    import json
    import logging
    import urllib.request

    from edge.webview import WebView, collect, SysStats

    ag, dog, th, clock, events, states = make(spool, model=Det1([Det("head", 0.8, (1, 2, 30, 40))]))
    run(ag, clock, 0.3)
    assert ag.last_dets["dets"] == [{"cls": "head", "conf": 0.8, "box": [1, 2, 30, 40]}]
    s = collect(ag, SysStats(), None)
    json.dumps(s, default=str)
    assert s["model"]["ready"] and s["model"]["dets"][0]["cls"] == "head" and s["agent"]["status"] == "idle"

    web = WebView(ag, port=0, host="127.0.0.1")
    assert web.start()
    try:
        base = "http://127.0.0.1:%d" % web.port
        get = lambda path: urllib.request.urlopen(base + path, timeout=3).read()
        assert json.loads(get("/api/status"))["robot"] == config.ROBOT_ID
        assert get("/snapshot.jpg").startswith(SOI)
        assert b"<title>" in get("/")
        logging.disable(logging.NOTSET)   # 이 한 줄은 기록이 나와야 함 (웹 기록 통으로만 감)
        try:
            logging.getLogger("edge.test").warning("웹 기록 시험")
        finally:
            logging.disable(logging.WARNING)
        assert any("웹 기록 시험" in l["msg"] for l in json.loads(get("/api/log?after=0")))
    finally:
        web.stop()


if __name__ == "__main__":
    import logging

    logging.disable(logging.WARNING)  # 일부러 낸 오류 기록은 숨김 (결과만 "edge ok")
    with tempfile.TemporaryDirectory() as d:
        check_parts()
        check_reporter(Path(d) / "r")
        check_messages(Path(d) / "m")
        check_agent(Path(d) / "a")
        check_patrol(Path(d) / "p")
        check_web(Path(d) / "w")
    print("edge ok")
