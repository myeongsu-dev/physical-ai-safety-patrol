# 라즈베리 파이 5 프로그램 · 김건주

로봇마다 1대. 카메라와 온도로 판단하고, 결과와 사진만 서버로 보냅니다. 서버가 끊겨도 순찰은 계속합니다.

| 파일 | 하는 일 |
|---|---|
| `agent.py` | 판단 · 보드 명령 · 서버 명령 처리 (실행 진입점) |
| `dog_link.py` | 보드와 `CMD` 주고받기 (USB 시리얼, DTR·RTS 끈 채 열기, 0.8초마다 살아 있음 알림, 끊기면 다시 열기). `FakeDog`는 보드 오류·무응답·끊김·선 유실도 흉내 |
| `camera.py` | XIAO 머리 카메라. 시리얼 JPEG 또는 UVC. 밀린 장은 버리고 가장 최근 장만 |
| `thermo.py` | GY-906(MLX90614) 표면 온도 (I2C 1번, 주소 0x5A) |
| `detect.py` | YOLO 추론. 통합 모델(`0 fire`·`1 head`·`2 helmet`·`3 person`) 하나를 `models/safety/버전/`에서 불러 한 장에 한 번 추론. 클래스별 기준(`HELMET_CONF`·`FLAME_CONF`). 5장 중 3장 확정 |
| `reporter.py` | MQTT state·event 보내기, cmd·mode 받기, 사진 `POST /photos`. 끊긴 동안 이벤트·사진을 쌓아 둠 |
| `config.py` | `.env` 읽기와 기준값 (45℃, 5장 중 3장, 14·18cm …) |
| `hwcheck.py` | 장치 하나씩 확인 (포트, 보드, 선 위치, 짧은 주행, 카메라, 온도, 추론 속도, MQTT, 브로커 + 가짜 보드 흐름) |
| `PATROL.md` | 서버 → Pi → MechDog 순찰 통신·선 따라가기 시험 순서, Pi 설치, 서버 명령 보내기 |
| `selfcheck.py` | 장치 없이 판단 흐름 시험 → `edge ok` |
| `xiao_cam/xiao_cam.ino` | XIAO ESP32-S3 Sense에 올리는 카메라 프로그램 (JPEG를 USB 시리얼로) |
| `potent-edge.service` | Pi가 켜지면 자동 실행 (systemd) |

## 판단

| 상황 | Pi가 하는 일 | 보고 |
|---|---|---|
| 순찰 (`patrol`·`go`·`dock`) | 보드에 `F` 선 따라가기, 15초 끝나기 전에 다시 보냄. `go`·`dock`은 목표 마커에서 멈춤 | state `status` |
| 막힘 (보드 `E stop`, 또는 Pi가 초음파 14cm 안 3번 연속) | 사진을 안전모 모델로 봄. 사람이면 기다림(10초 넘게 서 있으면 보고), 아니면 보고 + 음성 1번. 18cm 밖이면 다시 걸음 | `obstacle` + 사진 |
| 안전모 | 순찰 중 매 장 추론, 맨머리(`head`)가 최근 5장 중 3장 → 보고 + 음성 2번 (30초 안 반복 안 함) | `helmet` + 사진 |
| 과열 | 45℃ 초과 → 멈춤 → 2초마다 3번 다시 재며 통합 모델의 `fire` 판정 → 다시 잰 값이 45℃ 초과면 보고. 60초 동안 서버 명령을 기다리다 없으면 하던 일로 | `heat`(+ 불꽃 확정 시 `flame`) + 사진 |
| 서버 `guide` | 정지 + `L\|arrow\|left` 같은 LED 화살표. `mode normal`이 오면 끔 | |
| 서버 `patrol`·`resume` | 보드 연결·측정값·선이 보일 때만 `F`. 보드 `F\|ok`가 와야 state가 `patrol` (`role`도 알림) | state `status`·`role` |
| 서버 `stop` | `S`, 서버가 `resume`·`patrol`을 보내기 전에는 `F`를 다시 보내지 않음 | state `idle` |
| 안전 정지 | 선 유실 1초, 보드 끊김·다시 붙음, 측정값 끊김, `F` 거부·무응답 → `S`, 다시 붙어도 저절로 안 걸음 ([PATROL.md](PATROL.md)) | state `idle` |

state의 `status`: `idle`(대기) · `patrol`(순찰) · `blocked`(막힘 정지) · `heat_check`(과열 확인) · `go`(출동) · `guide`(대피 안내) · `dock`(복귀)

## 실행

```bash
pip install -r edge/requirements.txt
python -m edge.selfcheck                 # 장치 없이: "edge ok"
python -m edge.agent --fake              # 장치 없이 전체 흐름 (10초 막힘, 20초 과열 장면)
python -m edge.agent                     # 실기. 서버의 patrol 명령을 기다림
python -m edge.agent --patrol            # 서버 없이 바로 순찰
python -m edge.agent --drive             # 통신·주행만 (실제 보드 + MQTT, 머리 카메라·온도·AI 없이)
```

서버 → Pi → MechDog 선 따라가기 시험 순서와 Pi 설치는 [PATROL.md](PATROL.md).

설정은 저장소 맨 위 `.env`: `ROBOT_ID` · `DOG_PORT` · `CAM_PORT` · `MQTT_HOST` · `MQTT_PORT` · `SERVER_URL`.
`DOG_PORT` · `CAM_PORT`는 기기와 꽂는 순서마다 다르므로 `.env.example` 값을 그대로 쓰지 말고 `python -m edge.hwcheck ports`로 찾아 바꿉니다.
그 밖의 값(`DOG_BAUD`, `HEAT_SUSPECT_C`, `VOICE_OBSTACLE` …)은 `config.py`에 기본값이 있고 `.env`에 같은 이름으로 적으면 바뀝니다.
못 보낸 이벤트와 사진은 `~/potent-spool/`에 남습니다 (`EDGE_SPOOL`로 바꿈).

## Pi 처음 준비

```bash
sudo raspi-config nonint do_i2c 0        # I2C 켜기
sudo usermod -aG dialout,i2c $USER       # 시리얼·I2C 권한 (다시 로그인)
python -m venv ~/venv && ~/venv/bin/pip install -r edge/requirements.txt
i2cdetect -y 1                           # 5a가 보여야 GY-906 배선 OK

python -m edge.hwcheck ports             # 보드·XIAO 포트 이름 → 자기 .env의 DOG_PORT · CAM_PORT
python -m edge.hwcheck dog               # V 응답·지원 명령, 측정값(bat·sonar·line), S ok, 오류 답. 포트 열 때 보드가 다시 켜지지 않는지
python -m edge.hwcheck line              # 걷지 않고 선 위치 (left·straight·right·lost)
python -m edge.hwcheck drive             # 로봇이 움직임: 짧게 선 따라 걷고 멈춤
python -m edge.hwcheck cam               # 초당 장 수 (목표 15), cam_check.jpg
python -m edge.hwcheck thermo
python -m edge.hwcheck detect           # 초당 장 수 (목표 10), --imgsz 320/640 비교
python -m edge.hwcheck mqtt              # 서버 노트북 브로커와 연결
python -m edge.hwcheck flow              # 브로커 + 가짜 보드로 patrol·stop·resume 흐름 (시험용 로봇 이름 flowtest)
```

모델 두는 법: `models/safety/v1/best_ncnn_model/` (`yolo export model=best.pt format=ncnn imgsz=320`으로 만든 폴더). 새 버전은 `v2/`로 추가하면 다음 실행부터 씁니다.

## 다른 파트와 맞출 것

- 보드(박명수): 선 유실 시 보드도 멈추고 알림, 2초 감시로 멈출 때 알림 (제안은 [PATROL.md](PATROL.md) 끝), 시리얼 속도(지금 115200 가정), 살아 있음 알림 명령(지금 `V`를 0.8초마다), 선 따라가기(`F`) 중에도 보드가 초음파로 멈추는지(아니면 Pi 보조 정지가 맡음, `PI_GUARD`), LED 화살표 명령 `L|arrow|left·right·up·x`와 `L|clear`, 배터리 값 단위(mV로 가정해 6.4~8.4V → %), MP3 트랙 번호(1 막힘, 2 안전모)
- 서버(채웅): `POST /photos`는 multipart `file` 필드(+`robot`), 답 `{"url": "/photos/이름.jpg"}` 가정. state `status` 값 7가지, `marker`는 보드가 준 색 마커 값 그대로
- 위 약속이 정해지면 `common/protocol.py`와 `docs/protocol.md`에 함께 넣습니다
