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
- 출발점: `tools/mechdog-test/devices/dog/mechdog_test.py` (BLE로 같은 명령을 이미 처리함). 받는 곳을 BLE에서 USB 시리얼로 바꾸고 `A`(음성) 명령과 2초 감시를 더해 `main.py`로 만듭니다.
- 셀프 체크: 시험 도구의 `sim_check.py`처럼 가짜 Hiwonder 모듈로 PC에서 돌립니다.
- 올리기: Pi에서 `mpremote`로 `main.py`를 올리고 보드를 다시 켭니다. 이전 파일은 `main_prev.py`로 남기고, 보드는 `V` 명령에 버전으로 답합니다.

## 먼저 확인할 것

- 보드 USB 시리얼: Pi가 포트를 열 때 보드가 다시 켜지지 않는지, `mpremote`로 올라가는지 (안 되면 BLE로 같은 명령)
- 바닥 카메라로 선 따라가기와 색 마커를 함께 읽을 수 있는지 (지금은 펌웨어가 따로)
- MP3 모듈이 포트 2(주소 0x7B)에서 잡히는지, 1~2m에서 들리는지
