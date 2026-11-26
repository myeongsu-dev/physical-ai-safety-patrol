"""로봇 상태와 순찰 역할(A/B) 부여 (서버가 관리)

서버가 메카독마다 순찰 역할(A: 바깥 큰 고리, B: 안쪽 길 포함 고리)을 정해 주며 출발시킨다.
출발은 서버 자동: 빈 역할이 있으면 도크에서 쉬는 로봇 중 배터리가 가장 많은 로봇을 내보낸다 (auto_assign(auto=True)).
순찰 시작 · 충전 뒤 모두 같다. 길 중간에서 안전 정지한 로봇은 자동으로 다시 내보내지 않는다 (사람이 확인).
로봇 이름(A~D, ROBOT_ID)과 역할은 별개다. 역할별 마커 순서는 server/map_config.py.
셀프 체크: python -m server.fleet

TODO: 상태(대기·순찰·막힘 정지·과열 확인·출동·대피 안내·복귀) 바꾸기, 지난 마커 수로 구역 계산
"""
import time

from common import protocol  # 메시지 이름·형식은 여기서만 가져온다
from server import map_config

ONLINE_SEC = 5          # 이 시간 안에 state가 온 로봇만 출발 대상
ON_DUTY = ("patrol",)   # 이 상태면 이미 역할을 맡아 순찰 중
START_GRACE_S = 15      # patrol을 보낸 뒤 이 시간 동안은 순찰 중으로 친다 (보드 응답 기다리는 동안 또 보내지 않게)


class Fleet:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.robots = {}    # 로봇 이름 → 마지막 state + seen(받은 시각)
        self.roles = {}     # 역할 → 로봇 이름 (서버가 정한 것)
        self.started = {}   # 로봇 → patrol을 보낸 시각

    # ---------- 상태 받기 ----------
    def update_state(self, robot, msg):
        """potent/{robot}/state 를 받을 때마다 호출."""
        self.robots[robot] = {**msg, "seen": self.clock()}
        role = msg.get("role")        # 서버를 다시 켰을 때: 순찰 중인 로봇이 알려 주는 역할로 복구
        if role in protocol.ROLES and msg.get("status") in ON_DUTY and role not in self.roles:
            self.roles[role] = robot

    def online(self, robot):
        r = self.robots.get(robot)
        return bool(r) and self.clock() - r["seen"] <= ONLINE_SEC

    def on_duty(self, robot):
        """순찰 중이거나 방금 출발시킨 로봇"""
        if not self.online(robot):
            return False
        status = self.robots[robot].get("status")
        waiting = status == "idle" and self.clock() - self.started.get(robot, -1e9) < START_GRACE_S
        return status in ON_DUTY or waiting   # Pi는 보드가 F에 답하기 전까지 idle로 보낸다

    def role_of(self, robot):
        return next((role for role, name in self.roles.items() if name == robot), None)

    # ---------- 역할 부여 ----------
    def assign(self, robot, role):
        """역할을 정해 출발시킨다. 보낼 (토픽, 메시지) 목록을 돌려준다."""
        if role not in protocol.ROLES:
            raise ValueError("모르는 역할: " + str(role))
        if not self.online(robot):
            raise ValueError(f"{robot} 로봇이 연결돼 있지 않음")
        holder = self.roles.get(role)
        if holder and holder != robot and self.online(holder) \
                and self.robots[holder].get("status") in ON_DUTY:
            raise ValueError(f"역할 {role}은 {holder}가 순찰 중")
        old = self.role_of(robot)
        if old and old != role:
            del self.roles[old]
        self.roles[role] = robot
        self.started[robot] = self.clock()
        return [(protocol.topic(robot, "cmd"), protocol.command("patrol", role=role))]

    def auto_assign(self, auto=False, exclude=()):
        """비어 있는 역할을 쉬는 로봇에게 나눠 주고 출발시킨다. 배터리가 많은 로봇부터 A, B 순서로.
        auto=False (POST /fleet/start): 연결된 로봇 중 배터리가 BATTERY_RETURN 초과면 내보냄
        auto=True (서버가 1초마다): 도크(DOCK_MARKER)에서 idle이고 배터리 BATTERY_START 이상인 로봇만
        exclude: 내보내면 안 되는 로봇 (과열 확인 중, 관리자가 세운 로봇)"""
        busy = {name for name in self.roles.values() if self.on_duty(name)}
        free_roles = [r for r in protocol.ROLES if self.roles.get(r) not in busy]
        if not free_roles:
            return []

        def ready(n):
            s = self.robots[n]
            if not self.online(n) or n in busy or n in exclude:
                return False
            if auto:
                return (s.get("status") == "idle" and s.get("marker") == map_config.DOCK_MARKER
                        and s.get("battery", 0) >= map_config.BATTERY_START)
            return s.get("battery", 0) > map_config.BATTERY_RETURN
        ready = sorted(filter(ready, self.robots), key=lambda n: (-self.robots[n].get("battery", 0), n))
        out = []
        for role, robot in zip(free_roles, ready):
            out += self.assign(robot, role)
        return out


if __name__ == "__main__":
    now = [1000.0]
    f = Fleet(clock=lambda: now[0])
    f.update_state("A", {"status": "idle", "battery": 60, "marker": 15})
    f.update_state("B", {"status": "idle", "battery": 90, "marker": 15})
    f.update_state("C", {"status": "idle", "battery": 20, "marker": 15})   # 배터리 부족 → 제외
    sent = f.auto_assign()
    assert sent == [("potent/B/cmd", {"cmd": "patrol", "role": "A"}),
                    ("potent/A/cmd", {"cmd": "patrol", "role": "B"})], sent
    assert f.roles == {"A": "B", "B": "A"}
    # 둘 다 순찰 중이면 다시 불러도 아무것도 안 보냄
    f.update_state("B", {"status": "patrol", "battery": 88, "role": "A"})
    f.update_state("A", {"status": "patrol", "battery": 58, "role": "B"})
    assert f.auto_assign() == []
    # 순찰 중인 역할은 다른 로봇에게 직접 줄 수 없음
    f.update_state("C", {"status": "idle", "battery": 95})
    try:
        f.assign("C", "A"); raise AssertionError("ValueError가 나야 함")
    except ValueError:
        pass
    # A 역할 로봇(B)이 충전하러 들어가면(=순찰 아님) 대기 중인 C가 A를 이어받음
    f.update_state("B", {"status": "dock", "battery": 24, "role": "A"})
    assert f.auto_assign() == [("potent/C/cmd", {"cmd": "patrol", "role": "A"})]
    # 연결이 끊긴(5초 넘게 조용한) 로봇은 출발 대상 아님
    now[0] += 10
    f.update_state("D", {"status": "idle", "battery": 99})
    try:
        f.assign("A", "B"); raise AssertionError("ValueError가 나야 함")
    except ValueError:
        pass
    # 서버를 다시 켜도 순찰 중인 로봇의 state로 역할을 복구
    g = Fleet(clock=lambda: now[0])
    g.update_state("D", {"status": "patrol", "battery": 70, "role": "B"})
    assert g.roles == {"B": "D"}
    # 서버 자동 출발: 도크(15)에서 쉬고 배터리 BATTERY_START 이상인 로봇만, 배터리 많은 순
    h = Fleet(clock=lambda: now[0])
    h.update_state("A", {"status": "idle", "battery": 70, "marker": 15})
    h.update_state("B", {"status": "idle", "battery": 95, "marker": 15})
    h.update_state("C", {"status": "idle", "battery": 99, "marker": 6})    # 길 중간에서 멈춤 → 제외
    h.update_state("D", {"status": "idle", "battery": 40, "marker": 15})   # 충전 덜 됨 → 제외
    assert h.auto_assign(auto=True) == [("potent/B/cmd", {"cmd": "patrol", "role": "A"}),
                                        ("potent/A/cmd", {"cmd": "patrol", "role": "B"})]
    # 보드 응답을 기다리는 동안(아직 idle) 1초 뒤 다시 불러도 또 보내지 않음
    now[0] += 1
    assert h.auto_assign(auto=True) == []
    # START_GRACE_S가 지나도 순찰로 안 바뀌면 그 역할은 비었다고 보고 다시 고름
    now[0] += START_GRACE_S
    for n, b in (("A", 70), ("B", 95), ("C", 99), ("D", 40)):
        h.update_state(n, {"status": "idle", "battery": b, "marker": 15 if n != "C" else 6})
    assert len(h.auto_assign(auto=True)) == 2
    # 내보내면 안 되는 로봇은 빼고
    now[0] += START_GRACE_S
    h.update_state("A", {"status": "idle", "battery": 70, "marker": 15})
    h.update_state("B", {"status": "patrol", "battery": 95, "marker": 3, "role": "A"})
    h.update_state("D", {"status": "idle", "battery": 80, "marker": 15})
    assert h.auto_assign(auto=True, exclude={"D"}) == [("potent/A/cmd", {"cmd": "patrol", "role": "B"})]
    print("fleet ok: 역할", f.roles)
