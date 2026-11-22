"""이벤트 기록(SQLite)과 판단 · 과열 교차 확인
셀프 체크: python -m server.events

TODO: 45℃ 의심 → 다른 로봇 출동 → 두 대 모두 50℃ 초과 + 불꽃이면 evacuate
"""
import json
import sqlite3
import threading
import time

from common import protocol


class EventLog:
    """받은 이벤트에 번호(id)를 붙여 저장하고, 대시보드 체크로 해제한다."""

    def __init__(self, path=":memory:", clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()     # MQTT 스레드와 웹 서버가 같이 쓴다
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot TEXT NOT NULL, type TEXT NOT NULL, ts INTEGER NOT NULL,
            received REAL NOT NULL, body TEXT NOT NULL, checked REAL)""")
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
            return self._row(cur.lastrowid, robot, json.dumps(msg, ensure_ascii=False), None)

    def check(self, event_id):
        """관리자가 조치를 확인함. 없는 번호면 None"""
        with self.lock:
            cur = self.db.execute("UPDATE events SET checked = ? WHERE id = ? AND checked IS NULL",
                                  (self.clock(), event_id))
            self.db.commit()
            row = self.db.execute("SELECT id, robot, body, checked FROM events WHERE id = ?",
                                  (event_id,)).fetchone()
        return self._row(*row) if row else None

    def open_events(self, limit=100):
        """아직 해제 안 된 이벤트 (대시보드를 새로 열 때 채워 넣기용), 오래된 것부터"""
        with self.lock:
            rows = self.db.execute(
                "SELECT id, robot, body, checked FROM events WHERE checked IS NULL ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._row(*r) for r in reversed(rows)]

    @staticmethod
    def _row(event_id, robot, body, checked):
        return {"kind": "event", "id": event_id, "robot": robot, **json.loads(body), "checked": checked is not None}


if __name__ == "__main__":
    now = [1000.0]
    log = EventLog(clock=lambda: now[0])
    e = log.add("B", protocol.event("obstacle", marker=14, sonar_cm=12, photo=None))
    assert e["kind"] == "event" and e["id"] == 1 and e["robot"] == "B" and e["type"] == "obstacle" and not e["checked"]
    log.add("C", protocol.event("helmet", marker=3, conf=0.8))
    assert [x["id"] for x in log.open_events()] == [1, 2]
    assert log.check(1)["checked"] is True
    assert [x["id"] for x in log.open_events()] == [2]
    assert log.check(99) is None
    try:
        log.add("A", {"type": "smoke"}); raise AssertionError("ValueError가 나야 함")
    except ValueError:
        pass
    print("events ok")
