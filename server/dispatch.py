"""지휘: 대피 안내 (충전 교대 · 출동 · 합류점 우선권은 아직)

대피 화살표: 불난 마커를 빼고, 마커마다 가장 가까운 출구(EXITS)까지 최단 경로를 구한다.
경로의 첫 걸음 방향(N·E·S·W)이 그 마커의 화살표다. 길이 끊긴 마커와 불난 마커는 x.
  - 대시보드: plan()을 그대로 받아 지도에 그린다 ({"marker", "dir", "exit", "path"})
  - 로봇: 지금 마커의 방향을 로봇이 보는 방향 기준으로 바꿔 guide(arrow=left·right·up·down·x)로 보낸다
길(연결)은 map_config.ROUTES의 마커 순서에서, 방향과 거리는 MARKERS의 지도 좌표에서 계산한다.
셀프 체크: python -m server.dispatch

TODO: 충전 교대(배터리 BATTERY_RETURN 이하면 대기 로봇에 구간 인계), 출동 로봇을 경로 거리로 고르기,
      우선순위 대피 안내 > 교차 확인 > 순찰, 합류점 우선권(map_config 보류 항목)
"""
import heapq
import math

from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
from server import map_config

COMPASS = ("N", "E", "S", "W")
# 로봇 기준 화살표: 로봇이 보는 방향과 같으면 up, 시계 방향 90도면 right ...
RELATIVE = {0: "up", 1: "right", 2: "down", 3: "left"}
ROBOT_ARROWS = ("left", "right", "up", "x")   # 지금 펌웨어(firmware/dog/main.py ARROWS)가 아는 것. down이 생기면 더함


def edges():
    """마커 → 이웃 마커 집합. ROUTES의 entry · loop(한 바퀴) · back을 이은 것 (양방향)"""
    nb = {m: set() for m in map_config.MARKERS}

    def link(seq):
        for a, b in zip(seq, seq[1:]):
            if a != b:
                nb[a].add(b)
                nb[b].add(a)
    for r in map_config.ROUTES.values():
        loop = r["loop"]
        link(r["entry"] + loop[:1])              # 도크에서 나와 고리 첫 마커로
        link(loop + loop[:1])                    # 고리 한 바퀴
        ret = map_config.RETURN_POINT[next(k for k, v in map_config.ROUTES.items() if v is r)]["marker"]
        link([ret] + r["back"])                  # 복귀 지점에서 도크로
    return nb


def compass(a, b):
    """마커 a에서 b로 가는 방향 (지도 위쪽이 N, 픽셀 y는 아래로 커짐)"""
    (x1, y1), (x2, y2) = map_config.MARKERS[a]["xy"], map_config.MARKERS[b]["xy"]
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) >= abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def _dist(a, b):
    (x1, y1), (x2, y2) = map_config.MARKERS[a]["xy"], map_config.MARKERS[b]["xy"]
    return math.hypot(x2 - x1, y2 - y1)


def plan(fire=None, exits=None):
    """마커마다 대피 방향. fire: 불난 마커(모르면 None), exits: 출구 마커들 (기본 map_config.EXITS)
    돌려주는 것: [{"marker": 1, "dir": "S", "exit": 15, "path": [1, 5, 10, 11, 14, 15]}, ...] 마커 번호 순
    출구에 있으면 dir "here", 갈 수 없거나 불난 마커면 dir "x" · exit None · path []"""
    exits = [e for e in (exits or map_config.EXITS) if e != fire]
    nb = edges()
    # 출구들에서 거꾸로 한 번에 최단 거리 (다익스트라), nxt = 출구 쪽으로 가는 다음 마커
    dist, nxt, goal = {}, {}, {}
    q = [(0.0, e, e) for e in exits]
    for e in exits:
        dist[e], nxt[e], goal[e] = 0.0, None, e
    while q:
        d, m, g = heapq.heappop(q)
        if d > dist.get(m, math.inf):
            continue
        for n in nb[m]:
            if n == fire:
                continue
            nd = d + _dist(m, n)
            if nd < dist.get(n, math.inf):
                dist[n], nxt[n], goal[n] = nd, m, g
                heapq.heappush(q, (nd, n, g))
    out = []
    for m in sorted(map_config.MARKERS):
        if m == fire or m not in dist:
            out.append({"marker": m, "dir": "x", "exit": None, "path": []})
            continue
        path = [m]
        while nxt[path[-1]] is not None:
            path.append(nxt[path[-1]])
        out.append({"marker": m, "dir": "here" if len(path) == 1 else compass(m, path[1]),
                    "exit": goal[m], "path": path})
    return out


def robot_arrow(direction, heading):
    """지도 방향(N·E·S·W)을 로봇 화면 화살표로. 로봇이 보는 방향(heading)을 모르면 None (보내지 않음)"""
    if direction in ("x", "here"):
        return "x"
    if heading not in COMPASS or direction not in COMPASS:
        return None
    rel = RELATIVE[(COMPASS.index(direction) - COMPASS.index(heading)) % 4]
    return rel if rel in ROBOT_ARROWS else "x"   # 펌웨어에 down이 없으면 일단 x (그 자리 대기)


def guide_commands(fleet, arrows):
    """대피 시작 때 연결된 로봇마다 보낼 guide 명령 [(로봇, 명령 dict)]. 마커나 방향을 모르는 로봇은 뺀다."""
    by_marker = {a["marker"]: a for a in arrows}
    out = []
    for robot in sorted(fleet.robots):
        a = by_marker.get(fleet.robots[robot].get("marker"))
        if not fleet.online(robot) or a is None:
            continue
        arrow = robot_arrow(a["dir"], fleet.heading.get(robot))
        if arrow:
            out.append((robot, protocol.command("guide", arrow=arrow)))
    return out


if __name__ == "__main__":
    from server.fleet import Fleet

    nb = edges()
    assert all(nb[m] for m in map_config.MARKERS), "어디와도 안 이어진 마커"
    assert nb[14] == {15, 11, 9, 12} and nb[2] == {1, 3, 6}, (nb[14], nb[2])
    assert compass(1, 2) == "E" and compass(10, 5) == "N" and compass(14, 15) == "S"
    # 불 없음, 출구 15(도크): 모두 15로. 14는 바로 아래(S), 15는 here
    p = {a["marker"]: a for a in plan(exits=[15])}
    assert p[15]["dir"] == "here" and p[14] == {"marker": 14, "dir": "S", "exit": 15, "path": [14, 15]}
    assert all(a["path"][-1] == 15 for a in p.values())
    # 9에서 불: 9를 지나던 경로는 돌아감, 9는 x
    p = {a["marker"]: a for a in plan(fire=9, exits=[15])}
    assert p[9]["dir"] == "x" and all(9 not in a["path"] for a in p.values())
    assert p[6]["path"][-1] == 15
    # 출구로 가는 유일한 길(14)에서 불: 모두 갈 수 없음(x)
    p = {a["marker"]: a for a in plan(fire=14, exits=[15])}
    assert all(a["dir"] == "x" for m, a in p.items() if m != 15)
    # 출구가 둘이면 가까운 쪽으로
    p = {a["marker"]: a for a in plan(exits=[15, 1])}
    assert p[2]["exit"] == 1 and p[2]["dir"] == "W" and p[12]["exit"] == 15
    # 로봇 화살표: 북쪽을 보는 로봇에게 동쪽이면 right, 서쪽이면 left, 남쪽(뒤)은 펌웨어에 down이 없어 x
    assert robot_arrow("E", "N") == "right" and robot_arrow("W", "N") == "left" and robot_arrow("N", "N") == "up"
    assert robot_arrow("S", "N") == "x" and robot_arrow("E", None) is None and robot_arrow("here", None) == "x"
    assert robot_arrow("N", "E") == "left"
    # 로봇별 guide: 마커가 바뀌는 걸 보고 방향을 알아낸 로봇에만
    now = [1000.0]
    f = Fleet(clock=lambda: now[0])
    f.update_state("A", {"marker": 10, "status": "patrol", "battery": 80})
    f.update_state("A", {"marker": 5, "status": "patrol", "battery": 80})    # 10 → 5: 북쪽으로 가는 중
    f.update_state("B", {"marker": 3, "status": "patrol", "battery": 80})    # 방향 모름
    assert f.heading == {"A": "N"}
    cmds = guide_commands(f, plan(exits=[15]))
    assert cmds == [("A", {"cmd": "guide", "arrow": "x"})], cmds          # 5에서 15로는 남쪽(뒤)
    p = {a["marker"]: a for a in plan(exits=[1])}
    assert p[5]["dir"] == "N" and guide_commands(f, plan(exits=[1])) == [("A", {"cmd": "guide", "arrow": "up"})]
    print("dispatch ok: 불 없음 →", {a["marker"]: a["dir"] for a in plan()})
