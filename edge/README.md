# 라즈베리 파이 5 프로그램 · 김건주

로봇마다 1대. 카메라와 온도로 판단하고, 결과와 사진만 서버로 보냅니다. 서버가 끊겨도 순찰은 계속합니다.

| 파일 | 하는 일 |
|---|---|
| `dog_link.py` | 보드와 `CMD` 주고받기 (USB 시리얼, pyserial) |
| `camera.py` | XIAO 머리 카메라 영상 받기. 밀린 장은 버리고 가장 최근 장만 |
| `thermo.py` | GY-906 표면 온도 (I2C, smbus2) |
| `detect.py` | 추론. `models/`의 안전모 모델과 불꽃 모델을 불러 씀 |
| `agent.py` | 판단 · MQTT 보고 · 서버 명령 처리 (실행 진입점) |

- 판단: 최근 5장 중 3장이면 확정, 45℃ 초과면 과열 의심 → 2초마다 다시 재며 불꽃 추론
- 입력: 보드 `CMD`([1절](../docs/protocol.md)), 서버 `potent/{로봇}/cmd`, `potent/mode`
- 출력: `potent/{로봇}/state`(1초마다), `potent/{로봇}/event` + 사진 `POST /photos` ([2절](../docs/protocol.md))
- 설정: `.env`의 `ROBOT_ID` · `DOG_PORT` · `CAM_PORT` · `MQTT_HOST` · `SERVER_URL`

```bash
pip install -r edge/requirements.txt
python -m edge.agent
```

먼저 확인할 것: Pi 5 CPU 추론 속도(초당 10장 목표, 입력 640·320), XIAO 영상을 USB로 초당 15장 받는지
