"""길 뚫기: 서버 순찰 명령 → Pi 5 → MechDog 선 따라 걷기, 대시보드에 상황 표시

준비 (README "준비"): 노트북에서 Mosquitto와 python -m server.api, Pi에서 python -m edge.agent,
MechDog은 검은 테이프 위에 세워 둔다. 미션맵이 없으면 테이프를 1~2m 붙인다.
Wi-Fi 끊김 단계용으로 Pi에 SSH 창을 하나 열어 WIFI_DROP 명령을 쳐 둔다 (Enter는 시험이 물을 때).
"""
SHOWN = {"A": "MD-1", "B": "MD-2", "C": "MD-3", "D": "MD-4"}  # 대시보드 화면의 로봇 이름
# Pi Wi-Fi를 15초 끊었다 켬. systemd-run으로 돌려 SSH가 끊겨도 다시 켜짐 (Pi OS NetworkManager 기준)
WIFI_DROP = "sudo systemd-run sh -c 'nmcli radio wifi off; sleep 15; nmcli radio wifi on'"


def run(t):
    me = "%s(%s)" % (SHOWN.get(t.robot, t.robot), t.robot)
    s = t.state

    # 1. 길이 이어져 있는지 (걷지 않음)
    t.do("대시보드 화면 (GET /)", lambda: "<html" in t.http("GET", "/").lower())
    t.expect("로봇 연결 (Pi → 서버 → 대시보드)", lambda: s().get("online"), timeout=10)
    t.expect("배터리 값 (MechDog → Pi → 서버)", lambda: (s().get("battery") or 0) > 0, timeout=5)
    t.do("시작 전 정지 명령", lambda: t.cmd("stop"))
    t.expect("대기 상태", lambda: s().get("status") == "idle", timeout=5)
    t.ask("로봇 준비", "%s이 검은 테이프 위에 서 있고 앞이 비어 있나요?" % me)

    # 2. 서버 명령 → Pi → MechDog 걷기
    t.do("순찰 명령 (서버 → Pi)", lambda: t.http("POST", "/robots/%s/patrol" % t.robot, role=t.role))
    t.expect("순찰 상태 (Pi가 MechDog F 응답을 받음)", lambda: s().get("status") == "patrol", timeout=5)
    t.ask("선 따라 걷기", "로봇이 테이프를 따라 걷고 있나요?")
    t.ask("대시보드 표시", "대시보드 로봇 현황에 %s이 연결 · 순찰 · 배터리로 보이나요?" % me)

    # 3. 정지 · 다시 걷기
    t.do("정지 명령", lambda: t.cmd("stop"))
    t.expect("대기 상태로 바뀜", lambda: s().get("status") == "idle", timeout=5)
    t.ask("로봇 멈춤", "로봇이 멈췄나요?")
    t.ask("다시 시작점", "Wi-Fi 끊김 동안 걸을 거리가 남도록 로봇을 테이프 시작점에 다시 두었나요?")
    t.do("재개 명령", lambda: t.cmd("resume"))
    t.expect("다시 순찰 상태", lambda: s().get("status") == "patrol", timeout=5)
    t.ask("다시 걷기", "로봇이 다시 테이프를 따라 걷나요?")

    # 4. 순찰 중 Pi Wi-Fi 끊김: 대시보드에 끊김, 순찰은 계속 (edge/README.md), 다시 붙으면 순찰 그대로
    t.ask("Wi-Fi 끊기", "y를 누르고 바로 Pi SSH 창에서 실행하세요: %s" % WIFI_DROP)
    t.expect("끊김 표시 (서버 → 대시보드)", lambda: s().get("online") is False, timeout=20)
    t.ask("끊긴 동안 순찰", "끊긴 동안에도 로봇이 테이프를 따라 계속 걷나요?")
    t.expect("다시 연결 (Pi → 서버)", lambda: s().get("online") is True, timeout=40)
    t.expect("다시 붙은 뒤 순찰 그대로", lambda: s().get("status") == "patrol", timeout=5)
    t.ask("대시보드 끊김 · 복구", "대시보드에서 %s이 끊김으로 바뀌었다가 연결로 돌아왔나요?" % me)
    t.do("끝 정지 명령", lambda: t.cmd("stop"))
    t.expect("끝 대기 상태", lambda: s().get("status") == "idle", timeout=5)
