"""미션맵 설정: 마커 위치, A·B 순찰 순서, 충전 복귀 규칙.

11/27 새 맵(마커 1~16, 격자 길)에 예전 A·B 동선(missionmap_route_AB.png)을 옮긴 것.
맵·규칙이 바뀌면 이 파일만 고친다. fleet.py · dispatch.py · fake_robots.py가 여기서 가져다 쓴다.
셀프 체크: python -m server.map_config

방향은 지도 위쪽을 북(N)으로 본다. 마커는 모두 윗변이 북쪽을 보게 붙인다.
"""

# ---------- 마커 ----------
# 4x4 아루코(DICT_4X4_50) 1~16 사용. 인쇄 크기: 마커 10mm + 흰 여백 3mm.
# xy: 대시보드 지도(dashboard/js/map.js, 2000x1444) 위 좌표. 방향·거리 계산과 대시보드 표시에 같이 쓴다.
# 길은 격자: 위 줄 1-2-3-4, 가운데 줄 5-6-7-8-9, 아래 줄 10-11-12-13-14, 세로 1-5-10 · 2-6 · 3-8 · 4-9-14 · 7-12-15-16
MARKERS = {
    1: {"xy": (340, 160), "where": "왼쪽 위 모서리"},
    2: {"xy": (773, 160), "where": "CP1 · 윗길, 아래(6)로 갈라지는 곳"},
    3: {"xy": (1220, 160), "where": "윗길, 아래(8)로 갈라지는 곳"},
    4: {"xy": (1650, 160), "where": "오른쪽 위 모서리"},
    5: {"xy": (340, 553), "where": "왼쪽 길, 가운데 길 합류 · 비상구 1 옆"},
    6: {"xy": (773, 553), "where": "CP3 · 가운데 길, 위(2)로 갈라지는 곳"},
    7: {"xy": (1000, 553), "where": "가운데 길, 아래 줄기(12)로 갈라지는 곳"},
    8: {"xy": (1220, 553), "where": "CP2 · 가운데 길, 위(3)로 갈라지는 곳"},
    9: {"xy": (1650, 553), "where": "CP4 · 오른쪽 길, 가운데 길 합류 · 비상구 2 옆"},
    10: {"xy": (340, 890), "where": "왼쪽 아래 모서리"},
    11: {"xy": (670, 890), "where": "아래 길, 줄기 왼쪽"},
    12: {"xy": (1000, 890), "where": "아래 길, 줄기(7 · 15)가 만나는 곳"},
    13: {"xy": (1325, 890), "where": "아래 길, 줄기 오른쪽"},
    14: {"xy": (1650, 890), "where": "오른쪽 아래 모서리"},
    15: {"xy": (1000, 1090), "where": "도크 길 (맵 안)"},
    16: {"xy": (1000, 1268), "where": "충전 스테이션 (맵 밖)"},
}
DOCK_MARKER = 16
DOCK_LANE = (15, 16)          # 한 번에 한 대만 지나가는 도크 길
EXITS = [5, 9]                # 대피 출구 마커: 5 = 비상구 1 옆, 9 = 비상구 2 옆 (16 충전 스테이션은 출구 아님)

# ---------- 순찰 순서 ----------
# entry: 도크에서 나가며 지나는 마커, loop: 끝없이 반복하는 고리, back: 충전 복귀 때 지나는 마커
ROUTES = {
    "A": {
        "entry": [16, 15],
        "loop": [12, 11, 10, 5, 1, 2, 3, 4, 9, 14, 13],   # 바깥 큰 고리
        "back": [15, 16],     # 12에서 꺾어 도크로
    },
    "B": {
        "entry": [16, 15, 12],
        "loop": [7, 6, 5, 1, 2, 6, 7, 8, 3, 4, 9, 8],     # 6·7·8은 한 바퀴에 두 번 (방향이 다름)
        "back": [12, 15, 16],  # 7(서쪽)에서 줄기로 도크로
    },
}

# ---------- 충전 복귀 ----------
# 도크 = 관리실: 로봇이 스스로 오면 사람이 충전하고, 배터리가 BATTERY_START 이상 차면 서버가 자동으로 다시 내보낸다.
RETURN_POINT = {
    "A": {"marker": 12, "heading": "W"},
    "B": {"marker": 7, "heading": "W"},   # B는 7을 동쪽·북쪽으로도 지나가므로 서쪽일 때만
}
BATTERY_RETURN = 25        # 복귀 지점에서 이 값 이하면 도크로 (대시보드 BATTERY_RETURN과 같게)
BATTERY_START = 60         # 도크에서 이 값 이상이면 서버가 자동 출발 (임시, 실제 배터리 보고 조정)
BATTERY_EMERGENCY = 15     # 어디서든 이 값 이하면 그 자리 정지 + 서버에 보고


# ---------- 만났을 때 우선순위 (server/traffic.py가 씀) ----------
# 규칙 1: 다음 마커 예약. 로봇이 마커를 찍으면 동선상 다음 마커를 예약하고, 다른 로봇이 이미 있거나 예약했으면 그 자리 wait.
# 규칙 2: SINGLE_LANES(한 줄 길)는 한 번에 한 대만. 들어오는 로봇 먼저, 다 들어온 뒤 출발.
# 대피 중에는 쓰지 않는다 (guide가 로봇을 세움). 팀 결정이 바뀌면 아래 값만 고친다.
PRIORITY = ("dock", "go", "patrol")   # 앞일수록 먼저. dock = 충전 복귀, go = 과열 확인 출동, patrol = 순찰
TIE_WINDOW_S = 0.5                    # 이 시간 안에 같은 마커를 원하면 동시로 보고 TIE_BREAK 순서로 정함
TIE_BREAK = ("priority", "battery", "name")   # priority: PRIORITY 순, battery: 배터리 낮은 로봇 먼저, name: 이름 순
SINGLE_LANES = [(12, 15, 16)]         # 한 줄 길 (도크 길). 12는 길 입구. 이어진 두 마커가 모두 들어 있으면 그 구간
SERVER_DOCK = True                    # True: 복귀 지점에서 배터리가 BATTERY_RETURN 이하면 서버가 dock 명령
                                      #       (도크 길이 비었을 때만. 안 비었으면 한 바퀴 더 돌고 다음 복귀 지점에서 다시 봄)


# ---------- 갈림길 동작 (마커, 진행 방향) → 동작 ----------
# TODO(edge와 합의): 진행 방향(N/E/S/W)을 state에 실을지, 판단을 서버/Pi 중 어디서 할지 정해지면 연결한다.
# 여기에 없는 마커는 선만 따라간다 (모서리·위치 확인용).
TURNS = {
    "A": {
        (12, "N"): "left",        # 도크에서 올라와 아래 길 서쪽(11)으로
        (5, "N"): "straight",
        (2, "E"): "straight",
        (3, "E"): "straight",
        (9, "S"): "straight",
        (12, "W"): "straight",    # 배터리 복귀면 left
    },
    "B": {
        (12, "N"): "straight",    # 줄기로 7까지
        (7, "N"): "left",         # 가운데 길 서쪽(6)으로
        (6, "W"): "straight",     # 5 쪽
        (5, "W"): "right",        # 위로, 1 쪽
        (2, "E"): "right",        # 아래로, 6 쪽
        (6, "S"): "left",         # 2에서 내려와 7 쪽
        (7, "E"): "straight",     # 8 쪽
        (8, "E"): "left",         # 위로, 3 쪽
        (3, "N"): "right",        # 4 쪽
        (9, "S"): "right",        # 가운데 길 서쪽(8)으로
        (8, "W"): "straight",     # 7 쪽
        (7, "W"): "straight",     # 6 쪽, 배터리 복귀면 left
    },
}


def turn(robot, marker, heading, battery=100):
    """이 마커에서 할 동작. 복귀 지점에서 배터리가 BATTERY_RETURN 이하면 도크 쪽(left)."""
    rp = RETURN_POINT[robot]
    if marker == rp["marker"] and heading == rp["heading"] and battery <= BATTERY_RETURN:
        return "left"
    return TURNS[robot].get((marker, heading), "follow")


if __name__ == "__main__":
    for r, route in ROUTES.items():
        for part in route.values():
            assert all(m in MARKERS for m in part), (r, part)
    assert set(ROUTES["A"]["loop"]) | set(ROUTES["B"]["loop"]) | {15, 16} == set(MARKERS)
    assert turn("A", 12, "W", 26) == "straight" and turn("A", 12, "W", 25) == "left" and turn("A", 12, "N") == "left"
    assert turn("B", 7, "E", 10) == "straight" and turn("B", 7, "W", 20) == "left" and turn("B", 7, "W", 80) == "straight"
    assert turn("B", 1, "N") == "follow" and turn("B", 7, "N") == "left"
    assert all(e in MARKERS for e in EXITS) and DOCK_MARKER not in EXITS
    assert set(PRIORITY) <= {"dock", "go", "patrol"} and set(TIE_BREAK) <= {"priority", "battery", "name"}
    assert all(m in MARKERS for lane in SINGLE_LANES for m in lane) and any(DOCK_MARKER in lane for lane in SINGLE_LANES)
    print("map_config OK: 마커", len(MARKERS), "개, A 고리", len(ROUTES["A"]["loop"]), "· B 고리", len(ROUTES["B"]["loop"]))
