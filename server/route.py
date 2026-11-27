"""동선 계산: 다음 마커와 갈림길 방향. 서버(교통 정리)와 Pi(갈림길에서 보드에 방향 명령)가 같이 쓴다.
셀프 체크: python -m server.route

map_config.py(MARKERS · ROUTES · DOCK_MARKER)만 보고 계산하므로 동선이 바뀌면 map_config만 고치면 된다.
서버 다른 모듈을 가져오지 않아 Pi에서도 그대로 import할 수 있다: from server import route

    route.turn_at("A", prev=13, cur=12)                  → "straight"  (순찰: 12를 지나 11로)
    route.turn_at("A", prev=13, cur=12, going="dock")    → "left"      (복귀: 12에서 도크 쪽)
    route.turn_at("B", prev=8, cur=7)                    → "straight"
    route.turn_at("B", prev=12, cur=7)                   → "left"      (출발: 줄기에서 올라와 6 쪽)

turn_at이 돌려주는 것
- "left" · "right" · "straight": 갈림길(길이 셋 이상 만나는 마커)에서 고를 방향. 진행 방향(지난 마커 → 지금 마커) 기준
- "follow": 갈림길이 아님 (모서리 · 곧은 길). 보드가 선을 그냥 따라가면 됨
- None: 다음 마커를 모름 (지난 마커를 모르는데 갈 곳이 여럿, 동선에 없는 마커 등). 멈추지 말고 선 따라가기로 둘지는 Pi가 정함

going: "patrol"(역할 동선대로) · "dock"(충전 스테이션까지 최단 길) · "go"(zone까지 최단 길, 과열 확인 출동)
"""
import heapq
import math

from server import map_config

COMPASS = ("N", "E", "S", "W")
RELATIVE = {0: "straight", 1: "right", 3: "left"}   # 진행 방향에서 시계 방향으로 몇 칸 돌았나 (2 = 뒤로, 없음)


def compass(a, b):
    """마커 a에서 b로 가는 방향 (지도 위쪽이 N, 좌표 y는 아래로 커짐)"""
    (x1, y1), (x2, y2) = map_config.MARKERS[a]["xy"], map_config.MARKERS[b]["xy"]
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) >= abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def dist(a, b):
    (x1, y1), (x2, y2) = map_config.MARKERS[a]["xy"], map_config.MARKERS[b]["xy"]
    return math.hypot(x2 - x1, y2 - y1)


def edges():
    """마커 → 이웃 마커 집합. ROUTES의 entry · loop(한 바퀴) · back을 이은 것 (양방향)"""
    nb = {m: set() for m in map_config.MARKERS}

    def link(seq):
        for a, b in zip(seq, seq[1:]):
            if a != b:
                nb[a].add(b)
                nb[b].add(a)
    for role, r in map_config.ROUTES.items():
        loop = r["loop"]
        link(r["entry"] + loop[:1])                                   # 도크에서 나와 고리 첫 마커로
        link(loop + loop[:1])                                         # 고리 한 바퀴
        link([map_config.RETURN_POINT[role]["marker"]] + r["back"])   # 복귀 지점에서 도크로
    return nb


def junction(marker):
    """길이 셋 이상 만나는 마커 (방향을 골라야 하는 곳)"""
    return len(edges().get(marker, ())) >= 3


def transitions(role):
    """{(지난 마커, 지금 마커): 다음 마커}. B는 같은 마커를 두 번 지나서 지난 마커로 구분한다. 출발은 (None, 도크)"""
    r = map_config.ROUTES[role]
    loop = r["loop"]
    seq = [None] + r["entry"] + loop + loop[:2]
    t = {}
    for a, b, c in zip(seq, seq[1:], seq[2:]):
        t.setdefault((a, b), c)
    return t


def shortest_path(start, goal, avoid=()):
    """마커 start → goal 최단 경로 [start, ..., goal]. 길이 없으면 []"""
    if start not in map_config.MARKERS or goal not in map_config.MARKERS:
        return []
    nb = edges()
    best, prev, heap = {start: 0.0}, {}, [(0.0, start)]
    while heap:
        d, m = heapq.heappop(heap)
        if m == goal:
            break
        if d > best.get(m, 1e18):
            continue
        for n in sorted(nb[m]):
            if n in avoid:
                continue
            nd = d + dist(m, n)
            if nd < best.get(n, 1e18):
                best[n], prev[n] = nd, m
                heapq.heappush(heap, (nd, n))
    if goal not in best:
        return []
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]


def next_marker(role, prev, cur, going="patrol", zone=None):
    """동선상 다음 마커 (모르면 None)"""
    if cur not in map_config.MARKERS:
        return None
    if going in ("dock", "go"):
        goal = map_config.DOCK_MARKER if going == "dock" else zone
        path = shortest_path(cur, goal)
        return path[1] if len(path) > 1 else None
    if role not in map_config.ROUTES:
        return None
    t = transitions(role)
    nxt = t.get((prev, cur))
    if nxt is None:   # 지난 마커를 모를 때: 이 마커에서 갈 수 있는 곳이 하나뿐이면 그것
        options = {c for (a, b), c in t.items() if b == cur}
        nxt = options.pop() if len(options) == 1 else None
    return nxt


def relative(heading_in, heading_out):
    """진행 방향 기준 left · right · straight (뒤로는 None)"""
    if heading_in not in COMPASS or heading_out not in COMPASS:
        return None
    return RELATIVE.get((COMPASS.index(heading_out) - COMPASS.index(heading_in)) % 4)


def turn_at(role, prev, cur, going="patrol", zone=None):
    """마커 cur에서 고를 방향: "left" · "right" · "straight" · "follow"(갈림길 아님) · None(모름)"""
    nxt = next_marker(role, prev, cur, going, zone)
    if nxt is None:
        return None
    if not junction(cur):
        return "follow"
    if prev not in map_config.MARKERS:
        return None   # 진행 방향을 모름
    return relative(compass(prev, cur), compass(cur, nxt))


if __name__ == "__main__":
    nb = edges()
    grid = {(1, 2), (2, 3), (3, 4), (1, 5), (2, 6), (3, 8), (4, 9), (5, 6), (6, 7), (7, 8), (8, 9),
            (5, 10), (7, 12), (9, 14), (10, 11), (11, 12), (12, 13), (13, 14), (12, 15), (15, 16)}
    assert {(a, b) for a in nb for b in nb[a] if a < b} == grid, "동선으로 이은 길이 지도 격자와 다름"
    assert {m for m in map_config.MARKERS if junction(m)} == {2, 3, 5, 6, 7, 8, 9, 12}
    assert shortest_path(7, 16) == [7, 12, 15, 16] and shortest_path(16, 16) == [16]

    # 1) 손으로 쓴 갈림길 표(map_config.TURNS)와 계산이 같은지: 표에 있는 (마커, 진행 방향)마다
    #    그 방향으로 이 마커에 들어오는 지난 마커를 동선에서 찾아 비교
    for role, table in map_config.TURNS.items():
        t = transitions(role)
        for (marker, heading), want in table.items():
            prevs = [a for (a, b) in t if b == marker and a in map_config.MARKERS and compass(a, b) == heading]
            assert prevs, (role, marker, heading, "이 방향으로 들어오는 동선 없음")
            for p in prevs:
                got = turn_at(role, p, marker)
                assert got == want, (role, p, marker, heading, got, want)

    # 2) 동선 한 바퀴: 모든 갈림길에서 left · right · straight 중 하나, 모서리는 follow, 계산한 다음 마커가 실제 이웃
    for role in map_config.ROUTES:
        r = map_config.ROUTES[role]
        seq = r["entry"] + r["loop"] * 2
        prev = None
        for i, cur in enumerate(seq[:-1]):
            got = turn_at(role, prev, cur)
            nxt = next_marker(role, prev, cur)
            assert nxt == seq[i + 1] and nxt in nb[cur], (role, prev, cur, nxt)
            if junction(cur) and prev is not None:
                assert got in ("left", "right", "straight"), (role, prev, cur, got)
            elif not junction(cur):
                assert got == "follow", (role, prev, cur, got)
            prev = cur

    # 3) 복귀(dock): 복귀 지점에서 도크 쪽으로, 그 뒤 도크까지
    assert turn_at("A", 13, 12) == "straight" and turn_at("A", 13, 12, going="dock") == "left"
    assert turn_at("B", 8, 7) == "straight" and turn_at("B", 8, 7, going="dock") == "left"
    assert turn_at("B", 7, 12, going="dock") == "straight"            # 줄기 따라 내려가 15로
    assert turn_at("B", 12, 15, going="dock") == "follow" and next_marker("B", 15, 16, going="dock") is None
    # 4) 출발: 도크 → 15 → 12에서 A는 왼쪽(11), B는 직진(7), B는 7에서 왼쪽(6)
    assert turn_at("A", 15, 12) == "left" and turn_at("B", 15, 12) == "straight" and turn_at("B", 12, 7) == "left"
    assert next_marker("A", None, 16) == 15
    # 5) 과열 확인 출동(go): 최단 길로. 9에서 출발해 3으로: 9 → 4 → 3 (위로 = 왼쪽, 서쪽에서 온 로봇 기준)
    assert next_marker("A", 14, 9, going="go", zone=3) in (4, 8)
    # 6) 모르는 경우
    assert turn_at("A", None, 12) is None and turn_at("Z", 1, 2) is None and turn_at("A", 1, 99) is None
    print("route ok: 갈림길", sorted(m for m in map_config.MARKERS if junction(m)),
          "· A 출발 12:", turn_at("A", 15, 12), "· B 출발 7:", turn_at("B", 12, 7))
