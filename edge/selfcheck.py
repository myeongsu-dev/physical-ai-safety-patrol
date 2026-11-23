"""edge 셀프 체크. 장치·브로커 없이 PC에서 판단 흐름을 확인. 실행: python -m edge.selfcheck → "edge ok" """
import tempfile
from pathlib import Path

from common import protocol
from edge import config
from edge.agent import Agent
from edge.camera import SOI, EOI, Frame, split_jpegs
from edge.detect import (CLASS_CONF, CLASSES, Det, Voter, class_names, class_order_problems, filter_conf,
                         find_model, has_person, no_helmet)
from edge.dog_link import BoardState, FakeDog, battery_pct
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
    dog = FakeDog()
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
    for _ in range(int(secs / dt)):
        clock.t += dt
        ag.step()


def check_agent(spool):
    # 순찰 시작 → F, 측정값 → state
    ag, dog, th, clock, events, states = make(spool)
    assert dog.sent[0][0] == "T" and ("V",) in dog.sent
    ag.reporter.inbox.put(("cmd", protocol.command("patrol")))
    dog.inject("D", 0, 7400, 50.0)
    run(ag, clock, 1.2)
    assert ag.status == "patrol" and ("F", config.PATROL_STRIDE, config.PATROL_ANGLE, config.MOVE_MS) in dog.sent
    assert states[-1]["battery"] == 50 and states[-1]["sonar_cm"] == 50.0 and states[-1]["version"] == "test"
    assert set(states[-1]) == {"marker", "status", "battery", "sonar_cm", "temp_c", "version"}

    # 마커
    dog.inject("E", 1, "color", 3)
    run(ag, clock, 0.2)
    assert ag.marker == 3.0

    # 보드 막힘 정지 → obstacle + 사진 + 음성, 풀리면 다시 걸음
    dog.sent.clear()
    dog.inject("E", 2, "stop", 12.0)
    run(ag, clock, 0.2)
    assert ag.status == "blocked"
    assert events[-1][0] == "obstacle" and events[-1][1] and events[-1][2]["person"] is False
    assert ("A", config.VOICE_OBSTACLE, config.VOICE_VOLUME) in dog.sent
    dog.inject("E", 3, "resume", 30.0)
    run(ag, clock, 0.2)
    assert ag.status == "patrol" and dog.sent[-1][0] == "F"

    # Pi 보조 정지: 14cm 안 3번 연속
    n = len(events)
    for _ in range(3):
        dog.inject("D", 4, 7400, 10.0)
    run(ag, clock, 0.2)
    assert ag.status == "blocked" and ("S",) in dog.sent and len(events) == n + 1
    dog.inject("D", 5, 7400, 25.0)
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

    # 안전모: 맨머리 5장 중 3장 → helmet 보고 1번 (쿨다운)
    ag, dog, th, clock, events, _ = make(spool, model=Det1([Det("person", 0.9, ()), Det("head", 0.8, ())]))
    run(ag, clock, 2.0)
    assert [e[0] for e in events] == ["helmet"] and events[0][2]["conf"] == 0.8
    assert ("A", config.VOICE_HELMET, config.VOICE_VOLUME) in dog.sent

    # 막혔는데 사람 → 기다림, 10초 넘게 서 있으면 obstacle(person=True)
    ag.on_command(protocol.command("patrol"))
    ag.last_event["helmet"] = clock()  # 안전모 보고는 빼고 봄
    events.clear()
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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        check_parts()
        check_reporter(Path(d) / "r")
        check_agent(Path(d) / "a")
    print("edge ok")
