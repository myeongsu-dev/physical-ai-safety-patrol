# 순찰 통신 · 선 따라가기 시험 (Pi 5)

서버가 Pi를 거쳐 MechDog를 검은 선 위로 걷게 하고, 멈추고, 다시 걷게 하는 흐름을 확인하는 순서입니다.
AI 모델 · 머리 카메라 · 온도 센서 없이 합니다.

```text
관제 서버 ──MQTT potent/{ROBOT_ID}/cmd──▶ Pi 5 (edge.agent) ──USB 시리얼 CMD|F|…|$──▶ MechDog ESP32 ──I2C 0x53──▶ 바닥 비전 모듈 (patrol_cam)
관제 서버 ◀─MQTT potent/{ROBOT_ID}/state── Pi 5 ◀── CMD|F|ok|$ · CMD|!|F|이유|$ · CMD|D|t|bat|sonar|line|$ ── MechDog
```

선 찾기와 꺾기는 보드와 비전 모듈이 합니다 (Pi는 선 따라가기 알고리즘을 갖지 않음). Pi는 명령을 전하고, 보드 답을 확인하고, 위험하면 멈춥니다.

## 명령이 하는 일

| 서버 → Pi | Pi가 확인 | Pi → 보드 | 서버가 보는 state |
|---|---|---|---|
| `{"cmd":"patrol","role":"A"}` | 역할이 `A`·`B`인지, 보드 연결, 측정값(D)이 1초 안에 왔는지, 보드가 `F`를 지원하는지, 선이 보이는지(`line` ≥ 0) | `F\|50\|25\|10000` → 보드 `F\|ok`를 기다림 (1.5초) | ok가 온 뒤 `patrol`, `role: "A"` |
| `{"cmd":"stop"}` | | `S` → 이동 갱신 중단 | 바로 `idle` |
| `{"cmd":"resume"}` | patrol과 같은 확인 | `F\|50\|25\|10000` | ok 뒤 `patrol`, 역할 유지 |

- `role`은 순찰 동선이고 로봇 이름(`ROBOT_ID`)과 별개입니다. 역할 없이 온 `patrol`은 지난 역할을 이어 갑니다. `C` 같은 모르는 역할은 버립니다.
- `F`의 50은 보폭, 25는 꺾는 각도, 10000은 ms입니다. 보드는 한 번에 최대 15초라 Pi가 8초마다(`MOVE_MS - 2초`) `F`를 다시 보냅니다. `stop` 뒤에는 절대 다시 보내지 않습니다.
- 다른 로봇의 토픽, 깨진 JSON, 모르는 명령, 브로커에 보관된(retained) 옛 명령은 버립니다.
- Pi는 0.8초마다 `V`를 보내 보드의 2초 감시를 막습니다 (그대로 둠).

## Pi가 스스로 멈추는 경우 (안전 정지)

멈춘 뒤에는 **서버가 `resume`이나 `patrol`을 보낼 때까지 다시 걷지 않습니다.** state는 `idle`이고, state의 `hold`에 아래 이유가 실립니다 (서버 `stop`·`wait`·`guide` 뒤에는 그 명령 이름, 정지 이유가 없으면 `null`).

| 이유 (기록의 `안전 정지 (…)`) | 언제 |
|---|---|
| `line` | 선이 `LINE_LOST_S`(1초) 넘게 안 보임 (`line=-1`). 보드 `F`는 선을 놓치면 직진하기 때문 |
| `link` | 보드 포트가 끊김, 다시 열림, 또는 Pi가 2초 넘게 못 보냄 (그사이 보드가 감시로 멈췄을 수 있음) |
| `stream` | 걷는 중 측정값(D)이 `SENSOR_STALE_S`(1초) 넘게 안 옴 (보드 재시작 등) |
| `board` | 보드가 `F`를 거부(`CMD\|!\|F\|이유\|$`)하거나 동작 오류(`CMD\|!\|loop\|…`) |
| `no_ack` | `F`에 1.5초 동안 답이 없음 |
| `send` | 보드로 쓰기 실패 |

- 초음파 14cm 안 3번 → `blocked`(정지), 18cm 밖 → 다시 걸음은 그대로입니다 (`PI_GUARD`). 막힌 동안 보드가 끊기면 안전 정지로 바뀌어 물건이 치워져도 저절로 출발하지 않습니다.
- **서버 신호가 끊기면 다음 마커까지 가서 멈춥니다 (`server`).** 서버가 1초마다 보내는 `potent/server/alive`가 `ALIVE_TIMEOUT_S`(5초) 안 오면 걷던 중이면 다음 마커를 읽는 순간 `S`, `SERVER_STOP_MAX_S`(20초) 안에 마커가 없으면 그 자리에서 `S`. 막힘·과열 확인 중이었으면 끝나도 걷지 않습니다. 다시 붙어도 `resume`·`patrol`을 기다립니다. 서버 신호를 한 번도 못 받았으면(예전 서버) 이 감시는 꺼져 있습니다. MQTT가 다시 붙으면 구독을 다시 합니다 (clean session이라 끊긴 동안의 명령이 늦게 오지 않음).
- **배터리가 `BATTERY_STOP_PCT`(15%) 이하로 3초 계속되면 그 자리에서 멈춥니다 (`battery`).** 걸을 때 전압이 잠깐 떨어지는 것은 무시합니다.
- 프로그램이 끝나면(Ctrl-C, `systemctl stop`) `S`를 보내고 서버에 `idle`을 한 번 보냅니다. 강제 종료(`kill -9`, 전원)면 보드가 2초 감시로 멈춥니다.
- 초음파 `-1`(못 잼)은 앞이 비어 있을 때도 나오므로 고장으로 보지 않습니다. 측정값이 아예 안 오면 `stream`으로 멈춥니다.

## 설정 (`.env`, 커밋하지 않음)

| 이름 | 예 | 뜻 |
|---|---|---|
| `ROBOT_ID` | `A` | 로봇 이름. 토픽 `potent/A/cmd` |
| `DOG_PORT` | `/dev/serial/by-path/platform-…-usb-0:1:1.0-port0` | MechDog 보드. `hwcheck ports`로 찾음 |
| `MQTT_HOST` · `MQTT_PORT` | 관제 PC 주소 · `1883` | 브로커 |
| `STREAM_CHANS` | `bat,sonar,line,color` (기본) | `line`이 있어야 선 유실 정지가 동작 |
| `LINE_GUARD` · `LINE_LOST_S` | `1` · `1.0` | 선 유실 정지 켜기 · 시간 |
| `SENSOR_STALE_S` · `ACK_TIMEOUT_S` | `1.0` · `1.5` | 측정값 끊김 · F 답 기다림 |
| `PATROL_STRIDE` · `PATROL_ANGLE` · `MOVE_MS` | `50` · `25` · `10000` | 순찰 F 값. 처음 실기에서는 보폭을 낮춰 시작 (예 `30`) |

## Pi 5 준비 (처음 한 번)

**Windows PowerShell (개발 PC)**

```powershell
ssh <계정>@<Pi 주소>                          # 주소·계정은 각자 ~/.ssh/config에서 확인 (저장소에 적지 않음)
```

**Pi (Linux, SSH 안에서)**

```bash
cat /etc/os-release | head -3 ; python3 --version       # Raspberry Pi OS bookworm, Python 3.11
git clone https://github.com/myeongsu-dev/physical-ai-safety-patrol.git   # 이미 있으면 cd 후 git fetch
cd physical-ai-safety-patrol
git fetch origin && git switch edge/patrol-communication  # PR이 합쳐진 뒤에는 dev
python3 -m venv ~/venv
~/venv/bin/pip install pyserial "paho-mqtt>=2" python-dotenv requests   # 통신·주행만 (AI까지는 -r edge/requirements.txt)
sudo usermod -aG dialout $USER     # 시리얼 권한 → 로그아웃 후 다시 SSH
cp .env.example .env && nano .env  # ROBOT_ID, DOG_PORT, MQTT_HOST
```

포트 찾기: MechDog 보드만 꽂고 `~/venv/bin/python -m edge.hwcheck ports` → 바닥 비전 모듈 USB도 꽂았다면 하나씩 뽑아 보며 구분합니다 (둘 다 CH340). XIAO 머리 카메라는 `ttyACM`이라 헷갈리지 않습니다. `DOG_PORT`에는 꽂은 USB 자리로 정해지는 `/dev/serial/by-path/…` 이름을 적으면 다시 꽂거나 재부팅해도 바뀌지 않습니다 (같은 자리에 꽂을 것).

## 관제 PC에 브로커 (서버 담당 개발이 끝나기 전 대신)

`server/mosquitto.conf` 첫 줄 안내대로 Mosquitto를 설치·실행합니다 (같은 Wi-Fi의 Pi가 붙게 모든 주소에서 받음).
Pi가 못 붙으면 Windows 방화벽에서 1883을 엽니다 (관리자 PowerShell):

```powershell
New-NetFirewallRule -DisplayName "Mosquitto 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow
ipconfig                                       # 관제 PC Wi-Fi IPv4 → Pi .env의 MQTT_HOST
```

## 시험 순서

결과는 PASS · FAIL · 안 함(NOT TESTED)으로 적고, 실기 없이 한 것은 실기 PASS로 적지 않습니다.

| # | 어디서 | 명령 | PASS 기준 |
|---|---|---|---|
| 1 | 아무 곳 | `python -m common.protocol` | `protocol ok` |
| 2 | 아무 곳 | `python -m edge.selfcheck` | `edge ok` (patrol·stop·resume, 잘못된 명령, 보드 오류·무응답·끊김, 선 유실, 재연결 뒤 안 걸음 포함) |
| 3 | Pi | `python -m edge.hwcheck mqtt --secs 20` 를 켜 두고 관제 PC에서 아래 "서버 명령"을 보냄 | `연결됨`, `받음: ('cmd', {...})`. 다른 로봇 토픽은 안 받음 |
| 4 | Pi 또는 PC | `python -m edge.hwcheck flow` | 모두 PASS, `flow ok` (시험용 로봇 이름 `flowtest`라 진짜 로봇은 안 움직임) |
| 5 | Pi + 보드 | `python -m edge.hwcheck ports` → `python -m edge.hwcheck dog` | V 응답, `F`·`S` 지원, `line` 채널, T ok, 측정값 D, S ok, 오류 답 읽기 모두 PASS. 포트를 열 때 로봇이 주저앉지 않음 |
| 5b | Pi + 보드 | `python -m edge.hwcheck line` | 테이프를 옮기면 `left`·`straight`·`right`가 맞게 나옴, 테이프를 치우면 `lost` |
| 6 | Pi + 보드 | `python -m edge.hwcheck drive --stride 30 --secs 3` | F ok, 선을 따라 꺾음, 선을 치우면 `선 유실`로 멈춤, 손을 14cm 안에 넣으면 `초음파`로 멈춤, S ok |
| 7 | 전체 | Pi: `python -m edge.agent --drive` · 관제 PC: 서버 명령 | patrol → 걷기 + state `patrol`/`A`, stop → 멈춤 + `idle` (8초 넘게 지켜봐도 다시 안 걸음), resume → 다시 걷기 |

Pi 명령은 저장소 폴더에서 `~/venv/bin/python …` 또는 `source ~/venv/bin/activate` 뒤 `python …`입니다.

### 실행과 끝내기 (Pi)

```bash
python -m edge.agent --drive            # 실제 보드 + MQTT. 머리 카메라·온도·AI 없이, 가상 과열·장애물 장면 없음
python -m edge.agent --drive --patrol --role A   # 서버 없이 바로 순찰 (보드·선 확인 뒤)
# 끝내기: Ctrl-C → 보드 S, 서버에 idle. 급할 때는 로봇을 들어 올리거나 USB를 뽑음 (보드가 2초 안에 멈춤)
```

자동 실행(systemd, `edge/potent-edge.service`)은 AI까지 도는 `python -m edge.agent`입니다. 오늘 시험에는 쓰지 않고 SSH에서 직접 켭니다. `systemctl stop`도 이제 보드를 멈추고 끝납니다.

### 서버 명령 보내기 (관제 PC)

Mosquitto 명령 (Windows PowerShell 5.1은 JSON의 `"`를 `\"`로 적어야 함):

```powershell
mosquitto_pub -h localhost -t potent/A/cmd -q 1 -m '{\"cmd\":\"patrol\",\"role\":\"A\"}'
mosquitto_pub -h localhost -t potent/A/cmd -q 1 -m '{\"cmd\":\"stop\"}'
mosquitto_pub -h localhost -t potent/A/cmd -q 1 -m '{\"cmd\":\"resume\"}'
mosquitto_sub -h localhost -t 'potent/A/state' -v      # 1초마다 state (새 마커·멈춤은 바로)
```

Linux·macOS·Git Bash에서는 `'{"cmd":"patrol","role":"A"}'` 그대로입니다. `-r`(보관)은 쓰지 않습니다 (Pi가 버림).

관제 서버(`python -m server.api`)가 떠 있으면 서버 입구로도 됩니다:

```powershell
Invoke-RestMethod -Method Post "http://localhost:8000/robots/A/patrol?role=A"
Invoke-RestMethod -Method Post "http://localhost:8000/robots/A/cmd" -ContentType application/json -Body '{"cmd":"stop"}'
Invoke-RestMethod -Method Post "http://localhost:8000/robots/A/cmd" -ContentType application/json -Body '{"cmd":"resume"}'
```

## 검은 선 시험 (실기)

준비: 비전 모듈에 `firmware/vision/patrol_cam` (`python firmware/vision/flash.py`), 보드에 `firmware/dog/main.py` (`python firmware/dog/upload.py`). 둘 다 보드 담당이 굽고 올립니다. `hwcheck dog`의 V에 `line`이 없으면 보드가 비전 모듈을 못 찾은 것입니다.

1. **선 보기 (걷지 않음)**: `hwcheck line`. 테이프를 카메라 아래 왼쪽 → 가운데 → 오른쪽으로 옮김. x가 60보다 작으면 `left`, 100보다 크면 `right`, 사이면 `straight`, 치우면 `lost`(-1). 바닥까지 검게 잡히거나 테이프를 놓치면 `firmware/vision/view.py --thr N`으로 기준 밝기를 맞춤.
2. **직선 1m**: 로봇을 선 위에 놓고 `hwcheck drive --stride 30 --secs 3`. 선 위를 벗어나지 않는지. 괜찮으면 `--secs 6`.
3. **완만한 왼쪽 (반지름 50cm 이상)**: 같은 명령. 출력의 `left`가 나올 때 실제로 왼쪽으로 꺾는지 (보드는 x < 60이면 +각도).
4. **완만한 오른쪽**: `right`일 때 오른쪽으로 꺾는지 (x > 100이면 -각도). 반대로 꺾으면 보드 담당에게 각도 부호 확인 요청.
5. **선 끝**: 선이 끝나는 곳까지 걷게 해서 `선 유실 1.0초`로 멈추는지, 그동안 몇 cm 더 가는지 잼 (길면 `LINE_LOST_S`를 줄임).
6. **초음파**: 걷는 중 손을 14cm 안에 → 멈춤.
7. 위가 되면 보폭을 `PATROL_STRIDE`(50)로 올려 다시, 그다음 시험 7(서버 → Pi → 보드).

## 다른 파트에 전할 것

- **보드 (firmware/dog)**: ① `F` 중 선을 놓치면(-1) 직진함 → 보드에서도 일정 시간 넘게 -1이면 멈추고 `CMD|E|t|lost|1|$`를 보내 주면 Pi보다 빠름 (지금은 Pi가 1초 뒤 멈춤). ② 2초 감시로 멈출 때 알림이 없음 → `CMD|E|t|halt|quiet|$` 같은 알림이 있으면 Pi가 바로 압니다 (지금은 Pi가 포트 다시 열림·2초 넘게 못 보냄으로 짐작). ③ 배터리 값 단위 (Pi는 mV로 보고 6.4~8.4V → %). ④ 실기에서 쓸 보폭·각도 값을 시험 도구 결과(`tools/mechdog-test/results/`)로 남겨 주면 `PATROL_STRIDE`·`PATROL_ANGLE` 기본값을 맞춥니다.
- **서버**: state의 `status`는 보드가 `F`에 ok 한 뒤에만 `patrol`입니다. 안전 정지는 `idle`로 보이고 이유는 state의 `hold`에 있습니다 (`null`이면 정지 이유 없음 → 서버가 자동 출발시켜도 됨). cmd는 retain 없이 보내 주세요 (보관된 cmd는 Pi가 버림).
- **공통 약속**: 바꾼 것 없음 (`common/protocol.py`, `docs/protocol.md` 그대로).
