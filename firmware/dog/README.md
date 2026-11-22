# MechDog 보드 프로그램 · 박명수

MechDog 제어 보드(ESP32 · MicroPython)에 올리는 프로그램입니다. 몸으로 하는 일과 즉시 반응을 맡습니다.

| 하는 일 | 기능 |
|---|---|
| 선 따라가기, 색 띠 마커 읽기 | F-01 · F-02 |
| 초음파 막힘 정지 · 재개 | F-03 · F-09 |
| LED 화살표·✕, 음성 재생 (MP3 모듈) | F-12 · F-06 |
| 배터리 전압 보고 | F-11 |
| Pi가 2초 넘게 조용하면 정지 + LED 오류 | 안전 |

- 입력·출력: Pi 5와 USB 시리얼 `CMD|…|$` ([docs/protocol.md](../../docs/protocol.md) 1절)
- 프로그램: `main.py`. 시험 도구의 `mechdog_test.py`(BLE)와 명령 처리가 같고, USB 시리얼(REPL과 같은 UART, 115200)로 받습니다. `A`(음성), `L|arrow|left·right·up·x`(LED에 `<` `>` `^` `X` 글자), 2초 감시(멈추고 LED에 `E`)가 더해졌습니다.
- 셀프 체크: `python firmware/dog/sim_check.py` (가짜 Hiwonder 모듈로 PC에서, CI도 돌림)
- 올리기: Pi에서 `mpremote`로 `main.py`를 올리고 보드를 다시 켭니다. 이전 파일은 `main_prev.py`로 남기고, 보드는 `V` 명령에 버전으로 답합니다.

## 먼저 확인할 것

- 보드 USB 시리얼: Pi가 포트를 열 때 보드가 다시 켜지지 않는지, `mpremote`로 올라가는지 (안 되면 BLE로 같은 명령)
- 바닥 카메라로 선 따라가기와 색 마커를 함께 읽을 수 있는지 (지금은 펌웨어가 따로)
- MP3 모듈이 포트 2(주소 0x7B)에서 잡히는지, 1~2m에서 들리는지. `main.py`는 `Hiwonder_IIC.MP3`·`volume`·`playTrack` 이름으로 가정했으니 실제 이름으로 맞춥니다
- LED 모듈에 `showChar`·`clear`가 있는지 (없으면 `L|arrow`가 `!`로 답함)
