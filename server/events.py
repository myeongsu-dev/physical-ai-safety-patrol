"""이벤트 기록(SQLite)과 과열 교차 확인
셀프 체크: python -m server.events

과열 교차 확인 (F-05)
1. 로봇 X가 마커 M에서 heat 보고 (Pi가 45℃ 넘는 걸 세 번 다시 재서 확인한 것)
2. 서버: X에게 wait(그 자리 대기), 다른 로봇 Y에게 go(zone=M)
3. Y가 M에서 heat 보고 → 두 대 모두 50℃ 초과이고 불꽃이 한 번이라도 보였으면 화재 확정 → evacuate
4. 확정이 아니거나 CHECK_TIMEOUT_S 안에 Y 보고가 없으면 해제 → 둘 다 resume
보낼 로봇이 없으면 X만 붙잡아 두고 기다리다가 시간이 지나면 해제한다.
"""
import json
import logging
import sqlite3
import threading
import time

from common import protocol
from server import map_config

log = logging.getLogger("server.events")

CONFIRM_C = 50.0        # 두 대 모두 이 온도를 넘어야 화재
CHECK_TIMEOUT_S = 90    # 출동 보낸 뒤 이 시간 안에 두 번째 보고가 없으면 해제
HELPER_STATUSES = ("patrol", "idle")   # 확인하러 보낼 수 있는 로봇 상태


class EventLog:
    """받은 이벤트에 번호(id)를 붙여 저장하고, 대시보드 체크로 해제한다."""

    def __init__(self, path=":memory:", clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()     # MQTT 스레드와 웹 서버가 같이 쓴다
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot TEXT NOT NULL, type TEXT NOT NULL, ts INTEGER NOT NULL,
            received REAL NOT NULL, body TEXT NOT NULL, checked REAL, false_alarm INTEGER)""")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        if "false_alarm" not in cols:                  # 예전 기록 파일이면 칸만 더함
            self.db.execute("ALTER TABLE events ADD COLUMN false_alarm INTEGER")
        self.db.commit()

    def add(self, robot, msg):
        """potent/{robot}/event 하나 저장. 대시보드로 보낼 dict(kind·id·robot 포함)를 돌려준다."""
        if msg.get("type") not in protocol.EVENT_TYPES:
            raise ValueError("모르는 이벤트: " + str(msg.get("type")))
        ts = int(msg.get("ts") or self.clock())
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO events (robot, type, ts, received, body) VALUES (?, ?, ?, ?, ?)",
                (robot, msg["type"], ts, self.clock(), json.dumps(msg, ensure_ascii=False)))
            self.db.commit()
            return self._row(cur.lastrowid, robot, json.dumps(msg, ensure_ascii=False), None, None)

    def check(self, event_id, false_alarm=False):
        """관리자가 경보를 닫음. false_alarm=True면 오탐(로봇이 잘못 봄), 아니면 실제로 일어나 조치함.
        이미 닫힌 경보는 그대로 둔다. 없는 번호면 None"""
        with self.lock:
            self.db.execute("UPDATE events SET checked = ?, false_alarm = ? WHERE id = ? AND checked IS NULL",
                            (self.clock(), int(bool(false_alarm)), event_id))
            self.db.commit()
            row = self.db.execute(f"SELECT {self.COLS} FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row(*row) if row else None

    def open_events(self, limit=100):
        """아직 해제 안 된 이벤트 (대시보드를 새로 열 때 채워 넣기용), 오래된 것부터"""
        with self.lock:
            rows = self.db.execute(
                f"SELECT {self.COLS} FROM events WHERE checked IS NULL ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._row(*r) for r in reversed(rows)]

    def history(self, limit=50, before=None, type=None):
        """지난 경보 목록 (닫힌 것 포함), 최근 것부터. before=id보다 앞의 것만 (다음 쪽 넘기기)"""
        sql, args = f"SELECT {self.COLS} FROM events WHERE 1=1", []
        if before is not None:
            sql, args = sql + " AND id < ?", args + [before]
        if type is not None:
            sql, args = sql + " AND type = ?", args + [type]
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        return [self._row(*r) for r in rows]

    COLS = "id, robot, body, checked, false_alarm"

    @staticmethod
    def _row(event_id, robot, body, checked, false_alarm):
        """checked: 닫혔는지, checked_at: 닫은 시각(초), false_alarm: 오탐이면 true (안 닫혔으면 null)"""
        return {"kind": "event", "id": event_id, "robot": robot, **json.loads(body),
                "checked": checked is not None, "checked_at": checked,
                "false_alarm": None if checked is None else bool(false_alarm)}


class HeatCheck:
    """과열 교차 확인. on_heat · tick이 할 일 목록을 돌려준다.
    할 일: ("cmd", 로봇, 명령 dict) · ("mode", "evacuate")"""

    def __init__(self, fleet, clock=time.time):
        self.fleet, self.clock = fleet, clock
        self.checks = {}      # 마커 → {"first", "helper", "reports": {로봇: (온도, 불꽃)}, "deadline"}
        self.confirmed = None  # 확정된 마커 (대피 중이면 새 확인은 안 함)

    def on_heat(self, robot, msg):
        marker, temp = msg.get("marker"), float(msg.get("temp_c") or 0)
        flame = bool(msg.get("flame"))
        if marker is None or self.confirmed is not None:
            return []
        c = self.checks.get(marker)
        if c is None:                                   # 처음 보고: 붙잡고 다른 로봇 보내기
            helper = self._pick_helper(exclude={robot})
            self.checks[marker] = {"first": robot, "helper": helper, "reports": {robot: (temp, flame)},
                                   "deadline": self.clock() + CHECK_TIMEOUT_S}
            log.info("과열 의심 마커 %s (%s %.1f℃) → 확인 출동 %s", marker, robot, temp, helper or "없음")
            out = [("cmd", robot, protocol.command("wait"))]
            if helper:
                out.append(("cmd", helper, protocol.command("go", zone=marker)))
            return out
        c["reports"][robot] = (temp, flame)             # 같은 로봇이 다시 보내면 최신 값으로
        if len(c["reports"]) < 2:
            return []
        temps = [t for t, _ in c["reports"].values()]
        if all(t > CONFIRM_C for t in temps) and any(f for _, f in c["reports"].values()):
            log.warning("화재 확정 마커 %s %s", marker, c["reports"])
            self.confirmed = marker
            self.checks.clear()
            return [("mode", "evacuate")]
        log.info("마커 %s 교차 확인 결과 화재 아님 %s", marker, c["reports"])
        return self._release(marker)

    def tick(self):
        now, out = self.clock(), []
        for marker in [m for m, c in self.checks.items() if now >= c["deadline"]]:
            log.info("마커 %s 확인 시간 끝, 해제", marker)
            out += self._release(marker)
        return out

    def reset(self):
        """대피가 끝나 normal로 돌아갈 때"""
        self.confirmed = None
        self.checks.clear()

    def busy(self):
        """확인에 묶여 있는 로봇 (다른 출동·교대에서 빼야 함)"""
        return {r for c in self.checks.values() for r in (c["first"], c["helper"]) if r}

    def _release(self, marker):
        c = self.checks.pop(marker)
        return [("cmd", r, protocol.command("resume")) for r in (c["first"], c["helper"]) if r]

    def _pick_helper(self, exclude):
        """연결돼 있고 순찰·대기 중이고 배터리가 남은 로봇 중 배터리 많은 것.
        TODO(dispatch): 가까운 로봇 고르기는 dispatch.py에서 경로 거리로"""
        busy = self.busy() | exclude
        cands = [n for n, s in self.fleet.robots.items()
                 if n not in busy and self.fleet.online(n) and s.get("status") in HELPER_STATUSES
                 and s.get("battery", 0) > map_config.BATTERY_RETURN]
        return max(cands, key=lambda n: (self.fleet.robots[n].get("battery", 0), n), default=None)


def _selfcheck_heat():
    from server.fleet import Fleet

    now = [1000.0]
    f = Fleet(clock=lambda: now[0])
    for n, st, bat in (("A", "patrol", 80), ("B", "patrol", 90), ("C", "dock", 100), ("D", "idle", 20)):
        f.update_state(n, {"status": st, "battery": bat})
    h = HeatCheck(f, clock=lambda: now[0])
    heat = lambda t, fl=False, m=7: protocol.event("heat", marker=m, temp_c=t, flame=fl)
    # 1) A가 의심 → A 대기, B 출동 (C는 충전 중, D는 배터리 부족이라 제외)
    assert h.on_heat("A", heat(52, True)) == [("cmd", "A", {"cmd": "wait"}), ("cmd", "B", {"cmd": "go", "zone": 7})]
    assert h.busy() == {"A", "B"}
    assert h.on_heat("A", heat(53, True)) == []         # 같은 로봇이 또 보내도 아직 한 대
    # 2) B도 50℃ 초과 → 불꽃은 A가 봤으니 확정
    assert h.on_heat("B", heat(51)) == [("mode", "evacuate")]
    assert h.on_heat("A", heat(60, True, m=3)) == []    # 대피 중엔 새 확인 안 함
    h.reset()
    # 3) 두 번째가 50℃ 이하 → 해제, 둘 다 resume
    h.on_heat("A", heat(48))
    assert h.on_heat("B", heat(46)) == [("cmd", "A", {"cmd": "resume"}), ("cmd", "B", {"cmd": "resume"})]
    # 4) 둘 다 50℃ 초과인데 불꽃 없음 → 해제
    h.on_heat("A", heat(55))
    assert h.on_heat("B", heat(56))[0] == ("cmd", "A", {"cmd": "resume"}) and h.confirmed is None
    # 5) 두 번째 보고가 시간 안에 안 오면 해제
    h.on_heat("A", heat(55, True))
    now[0] += CHECK_TIMEOUT_S - 1
    assert h.tick() == []
    now[0] += 1
    assert h.tick() == [("cmd", "A", {"cmd": "resume"}), ("cmd", "B", {"cmd": "resume"})]
    # 6) 보낼 로봇이 없으면 붙잡아만 두기
    f.update_state("B", {"status": "dock", "battery": 30})
    assert h.on_heat("A", heat(55)) == [("cmd", "A", {"cmd": "wait"})]


if __name__ == "__main__":
    _selfcheck_heat()
    now = [1000.0]
    log = EventLog(clock=lambda: now[0])
    e = log.add("B", protocol.event("obstacle", marker=14, sonar_cm=12, photo=None))
    assert e["kind"] == "event" and e["id"] == 1 and e["robot"] == "B" and e["type"] == "obstacle" and not e["checked"]
    log.add("C", protocol.event("helmet", marker=3, conf=0.8))
    assert [x["id"] for x in log.open_events()] == [1, 2]
    assert log.open_events()[0]["false_alarm"] is None
    c = log.check(1)
    assert c["checked"] is True and c["checked_at"] == 1000.0 and c["false_alarm"] is False
    assert [x["id"] for x in log.open_events()] == [2]
    assert log.check(99) is None
    assert log.check(2, false_alarm=True)["false_alarm"] is True     # 오탐으로 닫기
    assert log.check(2)["false_alarm"] is True                        # 이미 닫힌 건 그대로
    # 지난 경보: 닫힌 것까지 최근 것부터, before로 다음 쪽, type으로 거르기
    log.add("A", protocol.event("obstacle", marker=5, sonar_cm=9, photo=None))
    assert [x["id"] for x in log.history()] == [3, 2, 1]
    assert [x["id"] for x in log.history(limit=1, before=3)] == [2]
    assert [x["id"] for x in log.history(type="obstacle")] == [3, 1]
    try:
        log.add("A", {"type": "smoke"}); raise AssertionError("ValueError가 나야 함")
    except ValueError:
        pass
    # 예전 기록 파일(false_alarm 칸 없음)도 그대로 열림
    import os, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "old.sqlite3")
        old = sqlite3.connect(p)
        old.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, robot TEXT NOT NULL, type TEXT NOT NULL, "
                    "ts INTEGER NOT NULL, received REAL NOT NULL, body TEXT NOT NULL, checked REAL)")
        old.execute("INSERT INTO events (robot, type, ts, received, body, checked) VALUES "
                    "('A', 'obstacle', 1, 1, '{\"type\": \"obstacle\"}', 5)")
        old.commit(); old.close()
        moved = EventLog(p)
        assert moved.history()[0]["checked"] is True and moved.history()[0]["false_alarm"] is False
        moved.db.close()
    print("events ok")
