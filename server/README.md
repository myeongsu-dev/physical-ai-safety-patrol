# 관제 서버 · 채웅

노트북에서 돌립니다. 여러 대를 엮는 판단을 맡습니다.

| 파일 | 하는 일 |
|---|---|
| `fleet.py` | 로봇 상태(대기·순찰·막힘 정지·과열 확인·출동·대피 안내·복귀) · 마커로 구역 위치 계산 |
| `events.py` | 이벤트 판단 · 과열 교차 확인 (두 대 모두 50℃ 초과 + 불꽃 → 화재 확정) |
| `dispatch.py` | 충전 교대 · 출동 · 대피 배정 · 합류점 우선권 |
| `fake_robots.py` | 가짜 로봇. MQTT로 상태·이벤트를 흉내 내 로봇 없이 서버·대시보드 개발 |
| `api.py` | FastAPI: WebSocket `/ws`, 사진 `POST·GET /photos`, 경보 닫기(정탐·오탐), 지난 경보 `GET /events`, 관리자 잠금 (실행 진입점) |

- 입력: `potent/{로봇}/state` · `event`, 사진 업로드, 대시보드 체크
- 출력: `potent/{로봇}/cmd` · `potent/mode`, 대시보드 WebSocket ([docs/protocol.md](../docs/protocol.md) 2·3절)
- 기록: 이벤트·사진을 SQLite에 (`server/photos/`, `*.sqlite3`은 커밋 안 함)
- MQTT 브로커: Mosquitto (노트북에 설치, 포트 1883)
- 출발: 서버 자동. 빈 역할(A·B)이 있으면 도크(15)에서 쉬고 배터리가 `BATTERY_START`(60%, 임시) 이상인 로봇 중 가장 많은 로봇을 내보냄. 길 중간에서 멈춘 로봇, 관리자가 stop한 로봇, 대피 중에는 안 내보냄
- 관리자 잠금: 저장소 맨 위 `.env`에 `ADMIN_PASSWORD=...`를 적으면 경보 닫기 · 출발 · 명령 · 모드 변경은 헤더 `X-Admin-Password`가 맞아야 받음. 비워 두면 잠그지 않음 (개발용)

```bash
pip install -r server/requirements.txt
python -m server.fake_robots     # 로봇 대신
python -m server.api             # http://localhost:8000
```
