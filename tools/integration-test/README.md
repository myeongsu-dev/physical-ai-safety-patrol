# 통합 시나리오 시험

대시보드 ↔ 서버 ↔ Pi 5 ↔ MechDog을 실제 장치로 끝까지 돌려 보고, 단계마다 PASS/FAIL을 남깁니다. `dev` → `main` PR은 그때 정한 시나리오가 전부 PASS인 결과표를 붙여서 올립니다.

- 서버를 띄운 PC에서 돌립니다. Pi와 MechDog은 평소 프로그램(`edge.agent`, `main.py`)을 그대로 돌리고, 시험 프로그램은 대신하지 않습니다.
- 명령은 운영자와 같은 서버 HTTP(`POST /robots/…`)로 보내고, 상황은 대시보드와 같은 WebSocket `/ws`로 봅니다.
- 서버가 알 수 없는 것(로봇이 실제로 걷는지, 화면에 보이는지)은 사람에게 y/n으로 묻고, 그것도 PASS/FAIL로 남깁니다.
- 하나라도 FAIL이면 그 자리에서 멈춥니다. 어떻게 끝나든(Ctrl-C 포함) 로봇에 `stop`을 보냅니다.

## 시나리오

| 이름 | 내용 |
|---|---|
| `patrol_line` | 길 뚫기. 서버 순찰 명령 → Pi → MechDog 선 따라 걷기, 대시보드에 연결 · 상태 · 배터리 표시, 정지 · 재개, 순찰 중 Pi Wi-Fi 15초 끊김(대시보드에 끊김, 순찰은 계속, 다시 붙으면 순찰 그대로) |

## 준비

| 어디 | 할 일 |
|---|---|
| 노트북 | Mosquitto(포트 1883)와 `python -m server.api`를 켬. 브라우저로 `http://localhost:8000/` 대시보드를 열어 둠 |
| Pi 5 | `.env`에 `ROBOT_ID`, `MQTT_HOST`(노트북 주소), `DOG_PORT`. `python -m edge.agent --drive`(머리 카메라 · 온도 · AI 없이) |
| MechDog | `firmware/dog/main.py`, 비전 모듈 `patrol_cam`. 검은 테이프 위에 세움. 미션맵이 없으면 테이프 1~2m. Wi-Fi 끊김 동안 30초쯤 걸으니 테이프가 짧으면 손으로 시작점에 옮김 |
| Wi-Fi 끊기 | Pi에 SSH 창을 하나 열고 `sudo systemd-run sh -c 'nmcli radio wifi off; sleep 15; nmcli radio wifi on'`을 쳐 둠. 시험이 물을 때 Enter. SSH는 끊겨도 Wi-Fi는 15초 뒤 다시 켜짐 |

먼저 Pi에서 `python -m edge.hwcheck dog`가 PASS인지 봅니다. `--drive`와 "보드가 `F`에 ok해야 `patrol`"은 PR #19(`edge/patrol-communication`)에 있습니다. 그 전 `dev`에서는 `python -m edge.agent --fake-cam --fake-thermo`로 켜고, `patrol` 상태가 보드 응답을 뜻하지 않으니 "선 따라 걷기" 질문으로 확인합니다.

## 실행

```
python tools/integration-test/itest.py patrol_line
python tools/integration-test/itest.py patrol_line --server http://192.168.0.10:8000 --robot B --role A
```

- `--server`: 서버 주소 (기본 `http://localhost:8000`). `--robot`: 시험할 로봇(Pi `.env`의 `ROBOT_ID`, 기본 `A`). `--role`: 순찰 역할(기본 `A`).
- 진행하며 `[PASS]`·`[FAIL]` 줄이 찍히고, 묻는 단계에서 `y`/`n`을 칩니다. 걷는 중에 묻는 단계는 테이프 끝에 닿기 전에 답합니다.
- 끝나면 마크다운 결과표(결과, 시각, 커밋, 단계별 PASS/FAIL)가 찍힙니다. 그대로 PR에 붙입니다. 전부 PASS면 종료 코드 0, 아니면 1.

## 시나리오 쓰는 법

통합마다 확인할 것이 다르니 시나리오는 그때그때 `scenarios/<이름>.py`로 새로 만듭니다. 첫 줄 설명(docstring)이 결과표 제목이 되고, `run(t)` 하나만 있으면 됩니다. 모든 단계는 아래 세 가지 중 하나라서 PASS/FAIL로 남습니다.

| 단계 | 쓰임 | PASS 조건 |
|---|---|---|
| `t.do(이름, 함수)` | 명령 보내기, 즉시 확인 | 예외 없이 끝나고 `False`가 아님 |
| `t.expect(이름, 함수, timeout=초)` | 상태가 바뀌기를 기다림 | 시간 안에 함수가 참 |
| `t.ask(이름, 질문)` | 눈으로 확인 | 사람이 `y` |

단계 안에서 쓰는 것:

| 이름 | 내용 |
|---|---|
| `t.state(로봇=None)` | 대시보드가 받은 로봇 state (`online`, `status`, `battery`, `marker`, `role`, `seen_ago` …) |
| `t.event(종류, 로봇=None)` | 대시보드가 받은 이벤트 목록 (`obstacle`, `heat`, `flame` …) |
| `t.mode` | `normal` · `evacuate` |
| `t.cmd(명령, 로봇=None, **값)` | `POST /robots/{로봇}/cmd` (`stop`, `resume`, `guide` …) |
| `t.http(방식, 경로, body=None, **쿼리)` | 서버 HTTP 그대로 (`/robots/A/patrol`, `/mode`, `/status` …) |
| `t.robot` · `t.role` | 명령줄에서 받은 로봇 · 역할 |

예: 막힘 정지를 더한 시나리오

```python
"""막힘 정지: 순찰 중 손을 대면 멈추고 대시보드에 경보"""

def run(t):
    t.do("순찰 명령", lambda: t.http("POST", "/robots/%s/patrol" % t.robot, role=t.role))
    t.expect("순찰 상태", lambda: t.state().get("status") == "patrol", timeout=5)
    n = len(t.event("obstacle"))
    t.ask("손 대기", "로봇 앞 10cm에 손을 댔나요?")
    t.expect("막힘 경보", lambda: len(t.event("obstacle")) > n, timeout=5)
    t.expect("막힘 상태", lambda: t.state().get("status") == "blocked", timeout=3)
```

## 셀프 체크

```
python tools/integration-test/itest.py --selfcheck
```

진짜 서버(브로커 없이)와 가짜 Pi로 `patrol_line`을 돌려 봅니다. 다 따르면 PASS, Pi가 명령을 안 따르거나 사람이 `n`이면 FAIL에서 멈추고 끝에 `stop`을 보내는지 확인합니다. CI(`tools/check.sh`)도 돌립니다.
