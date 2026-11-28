"""지휘: 대피 안내 (충전 교대 · 출동 · 합류점 우선권은 아직)

대피 화살표: 불난 마커를 빼고, 마커마다 가장 가까운 출구(EXITS)까지 최단 경로를 구한다.
경로의 첫 걸음 방향(N·E·S·W)이 그 마커의 화살표다. 길이 끊긴 마커와 불난 마커는 x.
  - 대시보드: plan()을 그대로 받아 지도에 그린다 ({"marker", "dir", "exit", "path"})
  - 로봇: 지금 마커의 방향을 로봇이 보는 방향 기준으로 바꿔 guide(arrow=left·right·up·down·x)로 보낸다
길(연결) · 방향 · 거리는 route.py (map_config.ROUTES의 마커 순서와 MARKERS 좌표)에서 계산한다.
셀프 체크: python -m server.dispatch

TODO: 충전 교대(배터리 BATTERY_RETURN 이하면 대기 로봇에 구간 인계), 출동 로봇을 경로 거리로 고르기,
      우선순위 대피 안내 > 교차 확인 > 순찰, 합류점 우선권(map_config 보류 항목)
"""
import heapq
import math

from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
from server import map_config, route

COMPASS = ("N", "E", "S", "W")
# 로봇 기준 화살표: 로봇이 보는 방향과 같으면 up, 시계 방향 90도면 right ...
RELATIVE = {0: "up", 1: "right", 2: "down", 3: "left"}
ROBOT_ARROWS = ("left", "right", "up", "x")   # 지금 펌웨어(firmware/dog/main.py ARROWS)가 아는 것. down이 생기면 더함


# 길 · 방향 · 거리는 route.py와 같은 것을 쓴다 (서버 · Pi가 같은 계산)
edges, compass, _dist = route.edges, route.compass, route.dist


def plan(fire=None, exits=None):
    """마커마다 대피 방향. fire: 불난 마커(모르면 None), exits: 출구 마커들 (기본 map_config.EXITS)
    돌려주는 것: [{"marker": 1, "dir": "S", "exit": 5, "path": [1, 5]}, ...] 마커 번호 순
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
    """대피 시작 때 연결된 로봇마다 보낼 guide 명령 [(로봇, 명령 dict)]. 마커나 방향을 모르는 로봇은 뺀다.
    도크에서 쉬는 로봇도 뺀다 (현장에 없고, 대피가 끝나 resume을 받으면 쉬던 로봇이 걸어 나가므로)."""
    by_marker = {a["marker"]: a for a in arrows}
    out = []
    for robot in sorted(fleet.robots):
        s = fleet.robots[robot]
        a = by_marker.get(s.get("marker"))
        if not fleet.online(robot) or a is None:
            continue
        if s.get("status") in ("idle", "dock") and fleet.at_dock(robot):
            continue
        arrow = robot_arrow(a["dir"], fleet.heading.get(robot))
        if arrow:
            out.append((robot, protocol.command("guide", arrow=arrow)))
    return out


if __name__ == "__main__":
    from server.fleet import Fleet

    nb = edges()
    assert all(nb[m] for m in map_config.MARKERS), "어디와도 안 이어진 마커"
    grid = {(1, 2), (2, 3), (3, 4), (1, 5), (2, 6), (3, 8), (4, 9), (5, 6), (6, 7), (7, 8), (8, 9),
            (5, 10), (7, 12), (9, 14), (10, 11), (11, 12), (12, 13), (13, 14), (12, 15), (15, 16)}
    assert {(x, y) for x in nb for y in nb[x] if x < y} == grid, "ROUTES로 이은 길이 지도 격자와 다름"
    assert compass(1, 2) == "E" and compass(10, 5) == "N" and compass(15, 16) == "S"
    # 불 없음, 출구 16(도크): 모두 16으로. 15는 바로 아래(S), 16은 here
    p = {a["marker"]: a for a in plan(exits=[16])}
    assert p[16]["dir"] == "here" and p[15] == {"marker": 15, "dir": "S", "exit": 16, "path": [15, 16]}
    assert all(a["path"][-1] == 16 for a in p.values())
    # 7에서 불: 7을 지나던 경로는 돌아감, 7은 x
    p = {a["marker"]: a for a in plan(fire=7, exits=[16])}
    assert p[7]["dir"] == "x" and all(7 not in a["path"] for a in p.values())
    assert p[6]["path"][-1] == 16
    # 도크로 가는 유일한 길(15)에서 불: 모두 갈 수 없음(x)
    p = {a["marker"]: a for a in plan(fire=15, exits=[16])}
    assert all(a["dir"] == "x" for m, a in p.items() if m != 16)
    # 출구가 둘이면 가까운 쪽으로
    p = {a["marker"]: a for a in plan(exits=[16, 1])}
    assert p[2]["exit"] == 1 and p[2]["dir"] == "W" and p[13]["exit"] == 16
    # 팀이 정한 출구(5, 9): 모두 둘 중 가까운 쪽으로, 5에서 불나면 9로
    p = {a["marker"]: a for a in plan()}
    assert map_config.EXITS == [5, 9] and p[5]["dir"] == p[9]["dir"] == "here"
    assert p[1]["exit"] == 5 and p[4]["exit"] == 9 and p[16]["path"][0] == 16 and p[16]["exit"] in (5, 9)
    p = {a["marker"]: a for a in plan(fire=5)}
    assert all(a["exit"] == 9 for m, a in p.items() if m != 5)
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
    cmds = guide_commands(f, plan(exits=[10]))
    assert cmds == [("A", {"cmd": "guide", "arrow": "x"})], cmds          # 5에서 10으로는 남쪽(뒤)
    p = {a["marker"]: a for a in plan(exits=[1])}
    assert p[5]["dir"] == "N" and guide_commands(f, plan(exits=[1])) == [("A", {"cmd": "guide", "arrow": "up"})]
    # 도크에서 쉬는 로봇에는 guide 안 보냄
    f.update_state("C", {"marker": 15, "status": "patrol", "battery": 80})
    f.update_state("C", {"marker": 16, "status": "idle", "battery": 80})    # 15 → 16: 도크에 들어와 쉼
    assert "C" not in {r for r, _ in guide_commands(f, plan())}
    print("dispatch ok: 불 없음 →", {a["marker"]: a["dir"] for a in plan()})
