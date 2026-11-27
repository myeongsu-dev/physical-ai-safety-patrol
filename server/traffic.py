"""교통 정리: A·B가 같은 마커나 도크 길에서 부딪히지 않게 서버가 wait로 세우고 풀어 준다.
셀프 체크: python -m server.traffic

규칙 (값은 server/map_config.py의 PRIORITY · TIE_WINDOW_S · TIE_BREAK · SINGLE_LANES · SERVER_DOCK)
1. 다음 마커 예약: 로봇이 마커를 찍으면 동선상 다음 마커를 그 로봇 몫으로 예약한다.
   다른 로봇이 그 마커에 있거나 이미 예약했으면 그 자리에서 wait. 비면 풀어 준다
   (순찰은 resume, 복귀 · 출동은 dock · go를 다시 보냄. Pi는 wait 뒤 resume을 받으면 순찰로 돌아가기 때문).
   충전 스테이션(DOCK_MARKER)에는 여러 대가 있어도 된다.
2. 한 줄 길(SINGLE_LANES, 도크 길)은 한 번에 한 대만. 출발은 길이 비었을 때 한 대씩,
   복귀(SERVER_DOCK)는 복귀 지점에서 길이 비었을 때만 dock을 보낸다. 안 비었으면 한 바퀴 더 돈다.
3. 같은 마커를 TIE_WINDOW_S 안에 두 대가 원하면 TIE_BREAK 순서(PRIORITY → 배터리 낮은 쪽 → 이름)로 정한다.
   서버는 메시지를 하나씩 처리하므로 완전히 동시인 경우는 없다. 이 규칙은 거의 같이 온 것끼리의 순서다.
대피 중(evacuate)에는 쓰지 않는다. guide가 로봇을 세우고, normal로 돌아오면 처음부터 다시 잡는다.

Server(api.py)가 부르는 것: on_state(로봇) · tick() · note_command(로봇, 명령) · can_depart() · departed(로봇) · reset()
모두 보낼 명령 목록 [(로봇, 명령 dict)]을 돌려준다 (can_depart는 True/False).
"""
import logging

from common import protocol
from server import map_config, route

log = logging.getLogger("server.traffic")

MOVES = ("patrol", "go", "dock")   # 서버가 걷게 하는 명령


shortest_path = route.shortest_path   # 예전 이름 (fake_robots 등)


def lane_of(a, b):
    """마커 a → b가 한 줄 길 안의 구간이면 그 길 번호, 아니면 None"""
    for i, lane in enumerate(map_config.SINGLE_LANES):
        if a in lane and b in lane:
            return i
    return None


DOCK_LANE = next(i for i, lane in enumerate(map_config.SINGLE_LANES) if map_config.DOCK_MARKER in lane)


class Traffic:
    def __init__(self, fleet, clock=None):
        self.fleet = fleet
        self.clock = clock or fleet.clock
        self.cur = {}       # 로봇 → 지금(마지막으로 찍은) 마커
        self.prev = {}      # 로봇 → 그 전 마커
        self.intent = {}    # 로봇 → 서버가 마지막으로 보낸 걷기 명령 (patrol · go · dock)
        self.reserved = {}  # 마커 → {"robot", "at"(예약 시각), "from"(예약할 때 있던 마커)}
        self.waiting = {}   # 로봇 → 기다리는 마커 (교통 정리로 wait을 보낸 로봇)
        self.lanes = {}     # 한 줄 길 번호 → 쓰는 로봇

    # ---------- 상태 ----------
    def kind(self, robot):
        return self.intent.get(robot, {}).get("cmd", "patrol")

    def rank(self, robot):
        """작을수록 먼저 (TIE_BREAK 순서)"""
        s = self.fleet.robots.get(robot, {})
        keys = {"priority": map_config.PRIORITY.index(self.kind(robot)) if self.kind(robot) in map_config.PRIORITY
                else len(map_config.PRIORITY),
                "battery": s.get("battery", 100), "name": robot}
        return tuple(keys[k] for k in map_config.TIE_BREAK)

    def next_marker(self, robot):
        """동선상 다음 마커 (모르면 None). 계산은 route.next_marker (Pi가 갈림길 방향을 정할 때와 같은 것)"""
        role = self.fleet.role_of(robot) or self.fleet.robots.get(robot, {}).get("role")
        return route.next_marker(role, self.prev.get(robot), self.cur.get(robot), self.kind(robot),
                                 self.intent.get(robot, {}).get("zone"))

    def blocked_by(self, robot, marker):
        """robot이 marker로 가면 안 되는 이유가 되는 로봇 (없으면 None)"""
        if marker != map_config.DOCK_MARKER:
            for other, m in self.cur.items():
                if other != robot and m == marker:
                    return other
            r = self.reserved.get(marker)
            if r and r["robot"] != robot:
                return r["robot"]
        lane = lane_of(self.cur.get(robot), marker)
        owner = self.lanes.get(lane) if lane is not None else None
        if owner not in (None, robot):
            return owner
        return None

    def _take(self, robot, marker):
        self.reserved[marker] = {"robot": robot, "at": self.clock(), "from": self.cur.get(robot)}
        lane = lane_of(self.cur.get(robot), marker)
        if lane is not None:
            self.lanes[lane] = robot

    def _drop(self, robot):
        for m in [m for m, r in self.reserved.items() if r["robot"] == robot]:
            del self.reserved[m]

    def _release_cmd(self, robot):
        k = self.kind(robot)
        if k == "patrol":
            return protocol.command("resume")
        return protocol.command(k, **{f: v for f, v in self.intent[robot].items() if f != "cmd"})

    # ---------- 다음 마커 잡기 ----------
    def _plan(self, robot):
        nxt = self.next_marker(robot)
        if nxt is None:
            return []
        who = self.blocked_by(robot, nxt)
        r = self.reserved.get(nxt)
        if who is not None and r and r["robot"] == who and self.cur.get(who) == r["from"] \
                and self.clock() - r["at"] <= map_config.TIE_WINDOW_S and self.rank(robot) < self.rank(who) \
                and lane_of(self.cur.get(robot), nxt) is None:
            # 거의 같이 원했는데 내가 먼저인 경우: 상대는 아직 출발 전이니 상대를 세우고 내가 감
            log.info("%s · %s 거의 같이 마커 %s → %s 먼저 (%s)", robot, who, nxt, robot, map_config.TIE_BREAK)
            del self.reserved[nxt]
            self.waiting[who] = nxt
            self._take(robot, nxt)
            return [(who, protocol.command("wait"))]
        if who is not None:
            log.info("%s: 마커 %s를 %s가 쓰는 중 → 기다림", robot, nxt, who)
            self.waiting[robot] = nxt
            return [(robot, protocol.command("wait"))]
        self._take(robot, nxt)
        return []

    def _maybe_dock(self, robot, marker):
        """복귀 지점에서 배터리가 낮으면 dock (도크 길이 비었을 때만)"""
        if not map_config.SERVER_DOCK or self.kind(robot) != "patrol":
            return []
        role = self.fleet.role_of(robot)
        rp = map_config.RETURN_POINT.get(role)
        s = self.fleet.robots.get(robot, {})
        if not rp or marker != rp["marker"] or self.fleet.heading.get(robot) != rp["heading"] \
                or s.get("battery", 100) > map_config.BATTERY_RETURN:
            return []
        if self.lanes.get(DOCK_LANE) not in (None, robot):
            log.info("%s 복귀할 때인데 도크 길을 %s가 쓰는 중 → 한 바퀴 더", robot, self.lanes[DOCK_LANE])
            return []
        log.info("%s 배터리 %s%% → 도크로", robot, s.get("battery"))
        self.lanes[DOCK_LANE] = robot
        self.intent[robot] = protocol.command("dock")
        return [(robot, protocol.command("dock"))]

    # ---------- Server가 부르는 것 ----------
    def on_state(self, robot, active=True):
        """state를 받을 때마다. 마커가 바뀌었을 때만 판단한다. active=False(대피 중)면 위치만 기억."""
        s = self.fleet.robots.get(robot, {})
        marker = s.get("marker")
        if marker not in map_config.MARKERS or marker == self.cur.get(robot):
            return []
        self.prev[robot], self.cur[robot] = self.cur.get(robot), marker
        if not active:
            return []
        self._drop(robot)
        for lane, owner in list(self.lanes.items()):
            if owner == robot and marker not in map_config.SINGLE_LANES[lane]:
                del self.lanes[lane]          # 한 줄 길을 빠져나감
        k = self.kind(robot)
        if k == "dock" and marker == map_config.DOCK_MARKER or \
                k == "go" and marker == self.intent[robot].get("zone"):
            self.intent.pop(robot, None)      # 도착
            if marker == map_config.DOCK_MARKER:
                self.lanes = {l: o for l, o in self.lanes.items() if o != robot}
            return []
        if robot in self.waiting:             # 기다리라고 했는데 마커가 바뀜 (wait이 늦게 닿음): 다시 판단
            del self.waiting[robot]
        return self._maybe_dock(robot, marker) + self._plan(robot)

    def tick(self, exclude=()):
        """1초마다: 기다리는 로봇 중 길이 빈 로봇을 풀어 준다 (먼저인 로봇부터). exclude: 과열 확인 중인 로봇"""
        out = []
        for robot in sorted(self.waiting, key=self.rank):
            if robot in exclude or not self.fleet.online(robot):
                continue
            nxt = self.waiting[robot]
            if self.blocked_by(robot, nxt) is None:
                del self.waiting[robot]
                self._take(robot, nxt)
                log.info("%s: 마커 %s 비었음 → 다시 출발", robot, nxt)
                out.append((robot, self._release_cmd(robot)))
        return out

    def note_command(self, robot, msg, own=False):
        """서버가 로봇에 명령을 보낼 때마다 (own: 교통 정리가 보낸 것).
        다른 곳(과열 확인, 관리자)이 보낸 명령이면 그쪽이 로봇을 맡은 것이니 기다림을 지우고 다시 판단한다."""
        cmd = msg.get("cmd")
        if cmd in MOVES:
            self.intent[robot] = dict(msg)
        if own:
            return []
        self.waiting.pop(robot, None)
        self._drop(robot)
        if cmd in MOVES and robot in self.cur:
            return self._plan(robot)
        return []

    def can_depart(self):
        """도크 길이 비어 출발시켜도 되나"""
        return self.lanes.get(DOCK_LANE) is None

    def departed(self, robot):
        """서버가 도크의 로봇을 출발시킴: 도크 길을 잡고 첫 마커를 예약"""
        self.lanes[DOCK_LANE] = robot
        self.cur[robot], self.prev[robot] = map_config.DOCK_MARKER, None
        return self._plan(robot)

    def reset(self):
        """대피 시작 · 끝: 예약 · 기다림 · 한 줄 길을 모두 비움 (지금 마커는 기억)"""
        self.reserved, self.waiting, self.lanes = {}, {}, {}
        self.intent = {r: m for r, m in self.intent.items() if m.get("cmd") == "dock"}


if __name__ == "__main__":
    from server.fleet import Fleet

    now = [1000.0]
    f = Fleet(clock=lambda: now[0])

    def state(t, robot, marker, battery=80, status="patrol", role=None):
        f.update_state(robot, {"marker": marker, "status": status, "battery": battery, "hold": None, "role": role})
        return t.on_state(robot)

    def fresh():
        f.robots.clear(); f.roles.clear(); f.heading.clear()
        return Traffic(f)

    assert shortest_path(7, 16) == [7, 12, 15, 16] and shortest_path(16, 16) == [16]
    assert lane_of(12, 15) == DOCK_LANE and lane_of(13, 12) is None

    # 다음 마커: A는 13 → 12, B는 8 → 7 → 6 (같은 7이라도 지난 마커로 구분), 출발은 16 → 15
    t = fresh(); f.roles.update({"A": "A", "B": "B"})
    state(t, "B", 8, role="B"); state(t, "B", 7, role="B")
    assert t.next_marker("B") == 6
    t.prev["B"] = 6
    assert t.next_marker("B") == 8

    # 1) 같은 마커(12)를 두 대가 원함: 먼저 찍은 A가 가고 B는 wait, A가 12를 지나가면 B를 resume
    t = fresh(); f.roles.update({"A": "A", "B": "B"})
    state(t, "A", 14, role="A"); assert state(t, "A", 13, role="A") == [] and t.reserved[12]["robot"] == "A"
    now[0] += 5
    state(t, "B", 16, role="B")
    assert state(t, "B", 15, role="B") == [("B", {"cmd": "wait"})] and t.waiting == {"B": 12}
    assert state(t, "A", 12, role="A") == [] and t.tick() == []          # A가 12에 있음 → 아직
    state(t, "A", 11, role="A")
    assert t.tick() == [("B", {"cmd": "resume"})] and t.reserved[12]["robot"] == "B"

    # 2) 거의 같이(0.5초 안) 원함: 복귀(dock) 중인 B가 순찰 A보다 먼저. 늦게 처리됐어도 A를 세움
    t = fresh(); f.roles.update({"A": "A", "B": "B"})
    state(t, "A", 14, role="A"); state(t, "A", 13, role="A")
    t.note_command("B", protocol.command("dock"), own=True)
    t.lanes[DOCK_LANE] = "B"
    state(t, "B", 8, role="B")
    now[0] += 0.3
    assert state(t, "B", 7, role="B") == [("A", {"cmd": "wait"})] and t.reserved[12]["robot"] == "B"
    # 0.5초가 지나서 원하면 먼저 찍은 쪽이 그대로
    t = fresh(); f.roles.update({"A": "A", "B": "B"})
    state(t, "A", 14, role="A"); state(t, "A", 13, role="A")
    t.note_command("B", protocol.command("dock"), own=True); t.lanes[DOCK_LANE] = "B"
    state(t, "B", 8, role="B")
    now[0] += 2
    assert state(t, "B", 7, role="B") == [("B", {"cmd": "wait"})]
    # 기다리던 복귀 로봇은 resume이 아니라 dock을 다시 받음 (Pi는 wait 뒤 resume이면 순찰로 돌아감)
    state(t, "A", 12, role="A"); state(t, "A", 11, role="A")
    assert t.tick() == [("B", {"cmd": "dock"})]

    # 3) 복귀: 복귀 지점(A 12, 서쪽)에서 배터리 BATTERY_RETURN 이하면 dock + 도크 길 잡음, 그동안 출발 안 함
    t = fresh(); f.roles.update({"A": "A"})
    state(t, "A", 13, role="A", battery=map_config.BATTERY_RETURN)
    out = state(t, "A", 12, role="A", battery=map_config.BATTERY_RETURN)
    assert out == [("A", {"cmd": "dock"})] and t.lanes[DOCK_LANE] == "A" and not t.can_depart()
    assert t.reserved[15]["robot"] == "A"
    state(t, "A", 15, role="A", status="dock"); assert not t.can_depart()
    state(t, "A", 16, role="A", status="dock"); assert t.can_depart() and "A" not in t.intent
    # 도크 길을 다른 로봇이 쓰는 중이면 복귀하지 않고 한 바퀴 더
    t = fresh(); f.roles.update({"A": "A", "B": "C"})
    t.lanes[DOCK_LANE] = "C"
    state(t, "A", 13, role="A", battery=10)
    assert state(t, "A", 12, role="A", battery=10) == [] and t.kind("A") == "patrol" and t.next_marker("A") == 11

    # 4) 출발: 도크 길을 잡고 15를 예약. 출발한 로봇이 12를 지나 고리로 나가면 길이 풀림
    t = fresh(); f.roles.update({"A": "A"})
    assert t.departed("A") == [] and not t.can_depart() and t.reserved[15]["robot"] == "A"
    t.note_command("A", protocol.command("patrol", role="A"), own=True)
    state(t, "A", 15, role="A"); state(t, "A", 12, role="A"); assert not t.can_depart()
    state(t, "A", 11, role="A"); assert t.can_depart()

    # 5) 다른 곳(과열 확인 · 관리자)이 명령을 보내면 기다림을 지움
    t = fresh(); f.roles.update({"A": "A", "B": "B"})
    state(t, "A", 14, role="A"); state(t, "A", 13, role="A")
    state(t, "B", 16, role="B"); state(t, "B", 15, role="B")
    assert "B" in t.waiting
    t.note_command("B", protocol.command("stop"))
    assert "B" not in t.waiting and t.tick() == []

    # 6) 설정을 바꾸면 그대로 따름: 배터리 먼저로 바꾸면 순찰 중이어도 배터리 낮은 쪽이 먼저
    saved = map_config.TIE_BREAK
    map_config.TIE_BREAK = ("battery", "name")
    t = fresh()
    f.update_state("A", {"battery": 30}); f.update_state("B", {"battery": 90})
    assert t.rank("A") < t.rank("B")
    map_config.TIE_BREAK = saved
    print("traffic ok: 우선순위", map_config.PRIORITY, "· 동시", map_config.TIE_WINDOW_S, "초 · 한 줄 길", map_config.SINGLE_LANES)
