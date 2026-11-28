# 관제 서버 · 채웅

노트북에서 돌립니다. 여러 대를 엮는 판단을 맡습니다.

| 파일 | 하는 일 |
|---|---|
| `fleet.py` | 로봇 상태(대기·순찰·막힘 정지·과열 확인·출동·대피 안내·복귀) · 마커로 구역 위치 계산 |
| `events.py` | 이벤트 판단 · 과열 교차 확인 (두 대 모두 50℃ 초과 + 불꽃 → 화재 확정) |
| `dispatch.py` | 대피 안내: 불난 마커를 피해 마커마다 출구 방향(화살표) 계산. 충전 교대 · 출동 · 합류점 우선권은 아직 |
| `route.py` | 동선 계산 · 다음 마커, 갈림길 방향 `turn_at(역할, 지난 마커, 지금 마커, going)` → left · right · straight · follow. 서버(교통 정리)와 Pi(보드에 방향 명령)가 같이 씀 |
| `traffic.py` | 교통 정리 · 다음 마커 예약(먼저 찍은 로봇 우선, 같은 마커면 wait), 도크 길 한 대씩, 배터리 확인 지점(A 13, B 8)에서 서버가 dock |
| `fake_robots.py` | 가짜 로봇. MQTT로 상태·이벤트를 흉내 내 로봇 없이 서버·대시보드 개발 |
| `api.py` | FastAPI: WebSocket `/ws`, 사진 `POST·GET /photos`, 경보 닫기(정탐·오탐), 지난 경보 `GET /events`, 관리자 잠금 (실행 진입점) |

- 입력: `potent/{로봇}/state` · `event`, 사진 업로드, 대시보드 체크
- 출력: `potent/{로봇}/cmd` · `potent/mode`, 대시보드 WebSocket ([docs/protocol.md](../docs/protocol.md) 2·3절)
- 기록: 이벤트·사진을 SQLite에 (`server/photos/`, `*.sqlite3`은 커밋 안 함)
- MQTT 브로커: Mosquitto (노트북에 설치, 포트 1883)
- 출발: 서버 자동. 빈 역할(A·B)이 있으면 도크(16)에서 쉬고 배터리가 `BATTERY_START`(60%, 임시) 이상인 로봇 중 가장 많은 로봇을 내보냄. Pi가 state에 hold(정지 이유)를 보내면 안 내보냄 (hold 칸이 없는 예전 Pi도 안 내보냄), 대피 중에도 안 내보냄. 켜고 마커를 아직 못 읽었으면 도크로 봄
- 대피 안내: evacuate가 되면 대시보드에 `{"kind": "evac", "fire", "exits", "arrows": [{"marker", "dir", "exit", "path"}]}`, 로봇에 `guide(arrow)` (도크에서 쉬는 로봇 제외). 대피를 해제하면 guide로 세운 로봇에 `resume`을 보내 순찰로 돌려보냄 (관리자 stop · 안전 정지된 로봇은 그대로). 출구는 `map_config.EXITS` (5 비상구 1 옆, 9 비상구 2 옆)
- 관리자 잠금: 저장소 맨 위 `.env`에 `ADMIN_PASSWORD=...`를 적으면 경보 닫기 · 출발 · 명령 · 모드 변경은 헤더 `X-Admin-Password`가 맞아야 받음. 비워 두면 잠그지 않음 (개발용)

```bash
pip install -r server/requirements.txt
python -m server.fake_robots     # 로봇 대신
python -m server.api             # http://localhost:8000
```

## 교통 정리 (우선순위)

A·B가 같은 마커나 도크 길에서 부딪히지 않게 서버가 `wait`으로 세우고 풀어 줍니다. 값은 모두 `map_config.py`에 있습니다.

1. 다음 마커 예약: 로봇이 마커를 찍으면 동선상 다음 마커를 예약합니다. 다른 로봇이 있거나 예약했으면 그 자리에서 `wait`, 비면 풀어 줍니다 (순찰은 `resume`, 복귀 · 출동은 `dock` · `go`를 다시 보냄)
2. 도크 길(`SINGLE_LANES`, 12-15-16)은 한 번에 한 대만. 출발은 길이 비었을 때 한 대씩, 복귀는 배터리 확인 지점(`BATTERY_CHECK`: A 13번, B 8번에서 서쪽으로 갈 때)에서 길이 비었을 때만 서버가 `dock`, 로봇은 다음 마커(12 · 7)에서 도크 쪽으로 꺾음 (안 비었으면 한 바퀴 더)
3. 거의 같이(`TIE_WINDOW_S` 0.5초 안) 원하면 `TIE_BREAK`: `PRIORITY`(복귀 > 출동 > 순찰) → 배터리 낮은 쪽 → 이름 순

| 바꾸고 싶은 것 | 값 |
|---|---|
| 우선순위 순서 | `PRIORITY` |
| 동시로 보는 시간 | `TIE_WINDOW_S` |
| 동점일 때 순서 | `TIE_BREAK` |
| 한 대만 다니는 길 | `SINGLE_LANES` |
| 복귀를 서버가 정할지 | `SERVER_DOCK` |
| 배터리 확인 지점 | `BATTERY_CHECK` |

대피 중에는 쓰지 않습니다 (guide가 로봇을 세움).

## 갈림길 방향 (Pi가 쓰는 법)

```python
from server import route
route.turn_at("A", prev=13, cur=12)                 # "straight" 순찰: 12를 지나 11로
route.turn_at("A", prev=13, cur=12, going="dock")   # "left"     복귀: 도크 쪽
route.turn_at("B", prev=15, cur=12)                 # "straight" 출발: 줄기 따라 7로
```

- 마커를 읽을 때마다 (역할, 지난 마커, 지금 마커, 지금 하는 일)로 부르고, `left` · `right` · `straight`면 보드에 그 방향을 보냅니다. `follow`는 갈림길이 아님(선만 따라감), `None`은 모름
- `going`: 순찰 `"patrol"`, `dock` 명령 뒤 `"dock"`, `go` 명령 뒤 `"go"`(+ `zone`)
- 동선은 `map_config.py`만 보고 계산하므로 동선이 바뀌어도 Pi 코드는 그대로입니다. 갈림길 표 `TURNS`와 계산이 같은지 셀프 체크가 확인합니다

