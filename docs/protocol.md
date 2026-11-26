# 파트 사이 약속

코드로는 `common/protocol.py`에 있습니다. 둘 중 하나를 바꾸면 다른 하나도 같은 커밋에서 고칩니다.

## 1. 보드 ↔ Pi 5 (USB 시리얼)

보드 USB와 Pi USB-A를 케이블 하나로 잇고, 동작 시험 도구(`tools/mechdog-test`)와 같은 `CMD|…|$` 형식을 씁니다. 한 줄에 메시지 하나, 줄 끝은 `\n`입니다. Pi는 포트를 열 때 보드가 다시 켜지지 않게 DTR·RTS를 끈 채 엽니다.

| 방향 | 메시지 | 뜻 |
|---|---|---|
| Pi → 보드 | `F\|보폭\|각도\|ms` | 선 따라가기 |
| Pi → 보드 | `U\|보폭\|정지cm\|재개cm\|ms` | 초음파 정지 걷기 (정지 거리 안 3번 연속이면 멈춤) |
| Pi → 보드 | `S` · `P` | 정지 · 기본 자세 |
| Pi → 보드 | `L\|모드\|값` | LED 표시 (화살표·✕·숫자) |
| Pi → 보드 | `A\|번호\|볼륨` | 음성 재생 (MP3 트랙 번호, 볼륨 0~30) |
| Pi → 보드 | `T\|간격ms\|채널,…` | 측정값 보내기 시작 (`bat` · `sonar` · `line` · `color`, 시험용 `far` 위 구역 선 x · `near` 아래 구역(발 바로 앞) 선 x · `steer` 조향 각도 · `roll` · `pitch` IMU 기울기 · `yaw` 출발 뒤 돈 각도), 간격 0이면 끔 |
| Pi → 보드 | `V` | 버전·지원 명령 묻기 |
| 보드 → Pi | `CMD\|D\|t\|값…\|$` | 측정값 (T에서 고른 순서대로) |
| 보드 → Pi | `CMD\|E\|t\|채널\|값\|$` | 이벤트: `stop` 막힘 정지(`U`, 그리고 `M`·`F`로 앞으로 걷는 중 10 cm 안 3번 연속이면 보드가 스스로) · `resume` 재개(`U`만) · `color` 마커 색 · `done` 이동 끝 · `lost` 선을 놓쳐 제자리 돌며 찾다가 못 찾고 멈춤(값: 돈 각도) |
| 보드 → Pi | `CMD\|명령\|ok\|$` · `CMD\|!\|명령\|이유\|$` | 명령 성공 · 실패 |

보드는 Pi가 2초 넘게 조용하면 멈추고 LED에 오류를 띄웁니다. 이동 명령은 한 번에 최대 15초입니다.

## 2. Pi 5 ↔ 서버 (Wi-Fi · MQTT)

로봇 이름은 `.env`의 `ROBOT_ID`(`A` · `B` · `C` · `D`)입니다. 내용은 모두 UTF-8 JSON입니다.

| 토픽 | 방향 · 주기 | 내용 |
|---|---|---|
| `potent/{로봇}/state` | Pi → 서버, 1초마다 | `marker`(지난 마커), `status`, `role`(받은 순찰 역할, 없으면 `null`), `battery`(%), `sonar_cm`, `temp_c`, `version`(git 커밋) |
| `potent/{로봇}/event` | Pi → 서버, 생길 때 | `type`, `ts`, 값, `conf`(신뢰도), `photo`(사진 주소) |
| `potent/{로봇}/cmd` | 서버 → Pi | `cmd` + 필요한 값 |
| `potent/mode` | 서버 → 전체 | `{"mode": "normal"}` · `{"mode": "evacuate"}` |
| 사진 | Pi → 서버, HTTP | `POST /photos`로 올리고 받은 주소를 이벤트의 `photo`에 넣음 |

이벤트 `type`: `obstacle`(통로 막힘) · `floor_object`(바닥 물건) · `helmet`(안전모 미착용) · `heat`(과열 의심) · `flame`(불꽃)

명령 `cmd`: `patrol`(`role`: `A`·`B`) · `stop` · `resume` · `go`(`zone`) · `wait` · `dock` · `guide`(`arrow`: `left`·`right`·`up`·`x`)

```json
// potent/B/event
{ "type": "obstacle", "ts": 1763343312, "marker": 14, "sonar_cm": 12,
  "person": false, "photo": "/photos/B_1117_103512.jpg" }

// potent/A/cmd
{ "cmd": "go", "zone": 7 }

// potent/C/cmd  (로봇 C에게 A 동선 순찰을 맡기며 출발)
{ "cmd": "patrol", "role": "A" }
```

**순찰 역할(`role`)**: 서버가 출발시킬 때 `patrol` 명령에 `role`로 동선을 정해 줍니다. `A`는 바깥 큰 고리, `B`는 안쪽 길을 포함한 고리입니다(마커 순서는 `server/map_config.py`). 로봇 이름(`ROBOT_ID`)과 역할은 별개라서, 어느 로봇이든 A나 B를 맡을 수 있습니다. Pi는 받은 역할을 기억해 `state`의 `role`로 알리고, 역할에 맞는 갈림길 동작을 합니다. 역할 없이 온 `patrol`은 지난 역할을 이어 갑니다.

서버가 끊긴 동안 Pi는 이벤트를 쌓아 두었다가 다시 붙으면 보냅니다.

## 3. 서버 ↔ 대시보드

| 방향 | 방식 | 내용 |
|---|---|---|
| 서버 → 화면 | WebSocket `/ws` | `{"kind": "state", "robot": "A", …}` · `{"kind": "event", "id": 12, "robot": "B", …}` · `{"kind": "mode", "mode": "evacuate"}` |
| 화면 → 서버 | HTTP `POST /events/{id}/check` | 관리자가 조치를 확인하고 해제 |
| 화면 | HTTP `GET /photos/…` | 사진 |

이벤트는 2초 안에 화면에 뜨는 것이 목표입니다.
