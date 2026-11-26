"""미션맵 설정: 마커 위치, A·B 순찰 순서, 충전 복귀 규칙.

지도 그림(missionmap_route_AB.png)으로 정한 내용을 서버가 읽을 수 있게 옮긴 것.
맵·규칙이 바뀌면 이 파일만 고친다. fleet.py · dispatch.py · fake_robots.py가 여기서 가져다 쓴다.
셀프 체크: python -m server.map_config

방향은 지도 위쪽을 북(N)으로 본다. 마커는 모두 윗변이 북쪽을 보게 붙인다.
"""

# ---------- 마커 ----------
# 4x4 아루코(DICT_4X4_50) 1~15 사용, 16은 예비. 인쇄 크기: 마커 10mm + 흰 여백 3mm.
# xy: 지도 그림(missionmap_original.png, 1191x860) 위 픽셀 좌표. 대시보드 표시용.
MARKERS = {
    1: {"xy": (227, 118), "where": "왼쪽 위 모서리"},
    2: {"xy": (434, 97), "where": "CP1 · 윗길, 아래로 갈라지는 곳"},
    3: {"xy": (759, 97), "where": "윗길, A2·A3 사이 길이 합류하는 곳"},
    4: {"xy": (960, 117), "where": "오른쪽 위 모서리"},
    5: {"xy": (206, 299), "where": "왼쪽 길, 가운데 길 합류 아래"},
    6: {"xy": (508, 334), "where": "CP3 · 가운데 길"},
    7: {"xy": (691, 334), "where": "CP2 · 가운데 길, 위로 갈라지는 곳"},
    8: {"xy": (980, 255), "where": "오른쪽 길, CP4 위"},
    9: {"xy": (593, 412), "where": "가운데 갈림길 아래 줄기"},
    10: {"xy": (226, 507), "where": "왼쪽 아래 모서리"},
    11: {"xy": (518, 527), "where": "아래 길, 줄기 왼쪽"},
    12: {"xy": (684, 527), "where": "아래 길, 줄기 오른쪽"},
    13: {"xy": (960, 507), "where": "오른쪽 아래 모서리"},
    14: {"xy": (593, 650), "where": "도크 길 (맵 안)"},
    15: {"xy": (593, 755), "where": "도크 = 관리실"},
}
DOCK_MARKER = 15
DOCK_LANE = (14, 15)          # 한 번에 한 대만 지나가는 도크 길

# ---------- 순찰 순서 ----------
# entry: 도크에서 나가며 지나는 마커, loop: 끝없이 반복하는 고리, back: 충전 복귀 때 지나는 마커
ROUTES = {
    "A": {
        "entry": [15, 14],
        "loop": [11, 10, 5, 1, 2, 3, 4, 8, 13, 12],
        "back": [14, 15],     # 12에서 꺾어 도크로
    },
    "B": {
        "entry": [15, 14, 9],
        "loop": [6, 5, 1, 2, 6, 7, 3, 4, 8, 7],   # 6·7은 한 바퀴에 두 번 (방향이 다름)
        "back": [9, 14, 15],  # 7(서쪽)에서 갈림길 오른쪽 가지로 도크로
    },
}

# ---------- 충전 복귀 ----------
# 도크 = 관리실: 로봇이 스스로 오면 사람이 충전하고, 배터리가 BATTERY_START 이상 차면 서버가 자동으로 다시 내보낸다.
RETURN_POINT = {
    "A": {"marker": 12, "heading": "W"},
    "B": {"marker": 7, "heading": "W"},   # B는 7을 동쪽으로도 지나가므로 서쪽일 때만
}
BATTERY_RETURN = 25        # 복귀 지점에서 이 값 이하면 도크로
BATTERY_START = 60         # 도크에서 이 값 이상이면 서버가 자동 출발 (임시, 실제 배터리 보고 조정)
BATTERY_EMERGENCY = 15     # 어디서든 이 값 이하면 그 자리 정지 + 서버에 보고


# ---------- 만났을 때 우선순위: 보류 ----------
# TODO(보류, 팀 결정 전): A·B가 도크 길(DOCK_LANE)이나 갈림길에서 동시에 만날 때 누가 먼저 갈지.
# 지금 제안: 배터리 낮은 쪽 먼저, 같으면 A (12번이 도크 길에 더 가깝다). 진 쪽은 한 바퀴 더 순찰.
# 정해지면 여기에 규칙을 넣고 dispatch.py(합류점 우선권)에서 쓴다.


# ---------- 갈림길 동작 (마커, 진행 방향) → 동작 ----------
# TODO(edge와 합의): 진행 방향(N/E/S/W)을 state에 실을지, 판단을 서버/Pi 중 어디서 할지 정해지면 연결한다.
# 여기에 없는 마커는 선만 따라간다 (모서리·위치 확인용).
TURNS = {
    "A": {
        (14, "N"): "left",        # 커브로 아래 길(11 쪽) 합류
        (14, "S"): "straight",    # 도크로
        (2, "E"): "straight",
        (8, "S"): "straight",
        (12, "W"): "straight",    # 배터리 복귀면 left
    },
    "B": {
        (14, "N"): "straight",    # 9 쪽
        (14, "S"): "straight",    # 도크로
        (9, "N"): "left",         # 갈림길 왼쪽 → 6
        (9, "S"): "straight",     # 도크로
        (2, "E"): "right",        # 아래로
        (6, "W"): "straight",     # 5 쪽
        (6, "S"): "left",         # 2에서 내려와 7 쪽
        (6, "E"): "straight",     # 7 쪽
        (7, "E"): "left",         # 위로, 3 쪽
        (7, "W"): "straight",     # 배터리 복귀면 left
        (8, "S"): "right",        # 다음 갈림길(CP4)에서 가운데 길로
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
    assert set(ROUTES["A"]["loop"]) | set(ROUTES["B"]["loop"]) | {9, 14, 15} == set(MARKERS)
    assert turn("A", 12, "W", 30) == "straight" and turn("A", 12, "W", 25) == "left"
    assert turn("B", 7, "E", 10) == "left" and turn("B", 7, "W", 20) == "left" and turn("B", 7, "W", 80) == "straight"
    assert turn("B", 1, "N") == "follow"
    print("map_config OK: 마커", len(MARKERS), "개, A 고리", len(ROUTES["A"]["loop"]), "· B 고리", len(ROUTES["B"]["loop"]))
