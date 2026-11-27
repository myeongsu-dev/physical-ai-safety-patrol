"""로봇 상태와 순찰 역할(A/B) 부여 (서버가 관리)

서버가 메카독마다 순찰 역할(A: 바깥 큰 고리, B: 안쪽 길 포함 고리)을 정해 주며 출발시킨다.
출발은 서버 자동: 빈 역할이 있으면 도크에서 쉬는 로봇 중 배터리가 가장 많은 로봇을 내보낸다 (auto_assign(auto=True)).
순찰 시작 · 충전 뒤 모두 같다. 서버는 짐작하지 않고 Pi가 state에 실어 보내는 hold(정지 이유)를 본다.
hold가 있으면(관리자 stop, 선 놓침, 보드 끊김 ...) 사람이 patrol · resume을 직접 보내기 전까지 내보내지 않는다.
state에 hold 칸이 아예 없는 Pi(예전 버전)도 정지 이유를 알 수 없으니 내보내지 않는다.
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
        self.heading = {}   # 로봇 → 보는 방향(N·E·S·W). 마커가 바뀔 때 지난 마커 → 새 마커 방향으로 추정
        self.saw_marker = set()   # 마커를 한 번이라도 보고한 로봇

    # ---------- 상태 받기 ----------
    def update_state(self, robot, msg):
        """potent/{robot}/state 를 받을 때마다 호출."""
        prev = self.robots.get(robot, {}).get("marker")
        new = msg.get("marker")
        if prev in map_config.MARKERS and new in map_config.MARKERS and prev != new:
            from server.dispatch import compass
            self.heading[robot] = compass(prev, new)
        if new in map_config.MARKERS:
            self.saw_marker.add(robot)
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
        held = status == "idle" and self.robots[robot].get("hold") == "wait"   # 서버가 잠깐 세운 것 (교통 정리 · 교차 확인)
        return status in ON_DUTY or waiting or held   # Pi는 보드가 F에 답하기 전까지 idle로 보낸다

    def at_dock(self, robot):
        """도크에 있다고 볼 수 있나. 켜고 아직 마커를 못 읽었으면(None, 한 번도 본 적 없음) 도크로 본다
        (서버와 Pi를 같이 켤 때는 로봇을 도크에 둔다). 순찰 중 Pi만 다시 켜져 None이면 도크가 아님."""
        marker = self.robots[robot].get("marker")
        if marker is None:
            return robot not in self.saw_marker
        return marker == map_config.DOCK_MARKER

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

    def auto_assign(self, auto=False, exclude=(), limit=None):
        """비어 있는 역할을 쉬는 로봇에게 나눠 주고 출발시킨다. 배터리가 많은 로봇부터 A, B 순서로.
        둘 다 hold(정지 이유)가 없는 로봇만. hold는 patrol · resume을 직접 보내야 풀린다 (POST /robots/…)
        auto=False (POST /fleet/start): 연결된 로봇 중 배터리가 BATTERY_RETURN 초과면 내보냄
        auto=True (서버가 1초마다): 도크에서 쉬고(idle · dock) 배터리 BATTERY_START 이상인 로봇만
        exclude: 내보내면 안 되는 로봇 (과열 확인 중), limit: 한 번에 내보낼 최대 대수 (도크 길은 한 대씩이라 1)"""
        busy = {name for name in self.roles.values() if self.on_duty(name)}
        free_roles = [r for r in protocol.ROLES if self.roles.get(r) not in busy]
        if not free_roles:
            return []

        def ready(n):
            s = self.robots[n]
            if not self.online(n) or n in busy or n in exclude:
                return False
            if "hold" not in s or s["hold"] is not None:   # 정지 이유가 있거나 알 수 없음
                return False
            if auto:
                return (s.get("status") in ("idle", "dock") and self.at_dock(n)
                        and s.get("battery", 0) >= map_config.BATTERY_START)
            return s.get("battery", 0) > map_config.BATTERY_RETURN
        ready = sorted(filter(ready, self.robots), key=lambda n: (-self.robots[n].get("battery", 0), n))
        out = []
        for role, robot in list(zip(free_roles, ready))[:limit]:
            out += self.assign(robot, role)
        return out


if __name__ == "__main__":
    now = [1000.0]
    f = Fleet(clock=lambda: now[0])
    f.update_state("A", {"status": "idle", "battery": 60, "marker": 16, "hold": None})
    f.update_state("B", {"status": "idle", "battery": 90, "marker": 16, "hold": None})
    f.update_state("C", {"status": "idle", "battery": 20, "marker": 16})   # 배터리 부족 → 제외
    sent = f.auto_assign()
    assert sent == [("potent/B/cmd", {"cmd": "patrol", "role": "A"}),
                    ("potent/A/cmd", {"cmd": "patrol", "role": "B"})], sent
    assert f.roles == {"A": "B", "B": "A"}
    # 둘 다 순찰 중이면 다시 불러도 아무것도 안 보냄
    f.update_state("B", {"status": "patrol", "battery": 88, "role": "A"})
    f.update_state("A", {"status": "patrol", "battery": 58, "role": "B"})
    assert f.auto_assign() == []
    # 순찰 중인 역할은 다른 로봇에게 직접 줄 수 없음
    f.update_state("C", {"status": "idle", "battery": 95, "hold": None})
    try:
        f.assign("C", "A"); raise AssertionError("ValueError가 나야 함")
    except ValueError:
        pass
    # A 역할 로봇(B)이 충전하러 들어가면(=순찰 아님) 대기 중인 C가 A를 이어받음
    f.update_state("B", {"status": "dock", "battery": 24, "role": "A"})
    assert f.auto_assign() == [("potent/C/cmd", {"cmd": "patrol", "role": "A"})]
    # 연결이 끊긴(5초 넘게 조용한) 로봇은 출발 대상 아님
    now[0] += 10
    f.update_state("D", {"status": "idle", "battery": 99, "hold": None})
    try:
        f.assign("A", "B"); raise AssertionError("ValueError가 나야 함")
    except ValueError:
        pass
    # 서버를 다시 켜도 순찰 중인 로봇의 state로 역할을 복구
    g = Fleet(clock=lambda: now[0])
    g.update_state("D", {"status": "patrol", "battery": 70, "role": "B"})
    assert g.roles == {"B": "D"}
    # 서버 자동 출발: 도크(16)에서 쉬고 배터리 BATTERY_START 이상인 로봇만, 배터리 많은 순
    h = Fleet(clock=lambda: now[0])
    h.update_state("A", {"status": "idle", "battery": 70, "marker": 16, "hold": None})
    h.update_state("B", {"status": "idle", "battery": 95, "marker": 16, "hold": None})
    h.update_state("C", {"status": "idle", "battery": 99, "marker": 6, "hold": None})    # 길 중간에서 멈춤 → 제외
    h.update_state("D", {"status": "idle", "battery": 40, "marker": 16, "hold": None})   # 충전 덜 됨 → 제외
    assert h.auto_assign(auto=True) == [("potent/B/cmd", {"cmd": "patrol", "role": "A"}),
                                        ("potent/A/cmd", {"cmd": "patrol", "role": "B"})]
    # 보드 응답을 기다리는 동안(아직 idle) 1초 뒤 다시 불러도 또 보내지 않음
    now[0] += 1
    assert h.auto_assign(auto=True) == []
    # START_GRACE_S가 지나도 순찰로 안 바뀌면 그 역할은 비었다고 보고 다시 고름
    now[0] += START_GRACE_S
    for n, b in (("A", 70), ("B", 95), ("C", 99), ("D", 40)):
        h.update_state(n, {"status": "idle", "battery": b, "marker": 16 if n != "C" else 6, "hold": None})
    assert len(h.auto_assign(auto=True)) == 2
    # 내보내면 안 되는 로봇은 빼고
    now[0] += START_GRACE_S
    h.update_state("A", {"status": "idle", "battery": 70, "marker": 16, "hold": None})
    h.update_state("B", {"status": "patrol", "battery": 95, "marker": 3, "role": "A"})
    h.update_state("D", {"status": "idle", "battery": 80, "marker": 16, "hold": None})
    assert h.auto_assign(auto=True, exclude={"D"}) == [("potent/A/cmd", {"cmd": "patrol", "role": "B"})]
    # 정지 이유(hold)가 있거나 hold 칸이 없는 로봇(예전 Pi)은 안 내보냄
    k = Fleet(clock=lambda: now[0])
    k.update_state("A", {"status": "idle", "battery": 99, "marker": 16, "hold": "line"})   # 도크 나가자마자 선 놓침
    k.update_state("B", {"status": "idle", "battery": 98, "marker": 16})                  # hold 칸 없음
    k.update_state("C", {"status": "idle", "battery": 97, "marker": 16, "hold": "no_ack"}) # 보드가 답 안 함
    assert k.auto_assign(auto=True) == [] and k.auto_assign() == []
    k.update_state("A", {"status": "idle", "battery": 99, "marker": 16, "hold": None})    # 사람이 확인하고 풂
    assert k.auto_assign(auto=True)[0] == ("potent/A/cmd", {"cmd": "patrol", "role": "A"})
    # 충전 끝난 가짜 로봇은 status dock으로 도크에 있음
    k.update_state("D", {"status": "dock", "battery": 100, "marker": 16, "hold": None})
    assert k.auto_assign(auto=True) == [("potent/D/cmd", {"cmd": "patrol", "role": "B"})]
    # 처음 켜서 마커를 아직 못 읽음(None) → 도크로 봄. 마커를 본 적 있는 로봇이 None이면(Pi만 재부팅) 아님
    j = Fleet(clock=lambda: now[0])
    j.update_state("A", {"status": "idle", "battery": 90, "marker": None, "hold": None})
    assert j.at_dock("A") and len(j.auto_assign(auto=True)) == 1
    j.update_state("B", {"status": "patrol", "battery": 90, "marker": 6, "hold": None})
    j.update_state("B", {"status": "idle", "battery": 90, "marker": None, "hold": None})
    assert not j.at_dock("B") and j.auto_assign(auto=True) == []
    print("fleet ok: 역할", f.roles)
