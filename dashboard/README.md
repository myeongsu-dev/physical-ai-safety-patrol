# 관제 대시보드 · 이예은

관리자가 보는 웹 화면입니다. 서버(`server/api.py`)가 이 폴더를 그대로 내어 줍니다.

| 화면 | 내용 |
|---|---|
| 구역 지도 | 미션맵 SVG 위에 로봇 4대 위치·상태, 막힌 통로, 화재 구역 |
| 이벤트 알림 | 사진 팝업 + 경보음, 관리자가 체크하면 해제 |
| 로봇 목록 | 배터리, 상태, 프로그램 버전(git 커밋) |

- 입력: WebSocket `/ws`의 `state` · `event` · `mode` ([docs/protocol.md](../docs/protocol.md) 3절)
- 출력: `POST /events/{id}/check`
- 지도: 03 미션맵 시뮬레이션 지도(`physical-ai-corp-project`의 `assets/sim/missionmap.js`)를 가져와 씁니다.
- 로봇 없이 개발: `python -m server.fake_robots`와 `python -m server.api`를 켜고 브라우저로 엽니다.
- 시간이 되면(Could): 모바일 알림 앱 시안
