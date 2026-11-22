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
- `main()`은 스레드가 아니라 파일 맨 끝에서 직접 돕니다. 파일이 끝나면 REPL이 켜져 Pi 명령을 가로채기 때문입니다.
- 올리기 (PC·Pi 같음): `python firmware/dog/upload.py <포트>`. `<포트>`는 `.env`의 `DOG_PORT`와 같은 값이고, 모르면 `python -m edge.hwcheck ports`로 찾습니다.
  - 보드를 재시작하지 않고 `main.py`를 바꿔 그 자리에서 다시 실행합니다. 이전 파일은 보드에 `main_prev.py`로 남습니다.
  - 보드가 재시작되면 서보 힘이 빠져 로봇이 주저앉습니다(배 쪽 배터리에 충격). `mpremote`는 포트를 열 때 CH340 보드를 재시작시키고 올린 뒤 `reset`도 하므로 쓰지 않습니다.
- 처음 올릴 때(공장 앱이나 시험 도구가 돌고 있을 때)만 `mpremote`를 씁니다. 그 프로그램들은 시리얼을 스레드에서 읽어서 `upload.py`의 Ctrl-C로 멈추지 않습니다. 로봇을 눕힌 채 합니다:
  ```
  mpremote connect <포트> resume exec "import os; os.rename('main.py','main_off.py')"
  (보드 전원을 껐다 켬)
  mpremote connect <포트> resume fs cp firmware/dog/main.py :main.py
  mpremote connect <포트> resume reset
  ```
  `resume`을 빼면 소프트 리셋에서 보드가 죽었다가 다시 켜집니다. `could not enter raw repl`이 나면 부팅이 끝난 뒤 한 번 더 합니다.
- Pi(`edge/dog_link.py`)와 `upload.py`는 DTR·RTS를 끈 채 열어서 포트를 열어도 보드가 재시작되지 않습니다 (Windows에서 확인, Linux는 확인 필요).

## 먼저 확인할 것

- 바닥 카메라로 선 따라가기와 색 마커를 함께 읽을 수 있는지 (지금은 펌웨어가 따로)
- MP3 모듈이 포트 2(주소 0x7B)에서 잡히는지, 1~2m에서 들리는지. `main.py`는 `Hiwonder_IIC.MP3`·`volume`·`playTrack` 이름으로 가정했으니 실제 이름으로 맞춥니다
- LED 화살표가 로봇 기준 방향과 맞는지 (LED는 `Hiwonder.Digitaltube`, IO32·IO33. `showNum`·`setBrightness`·`clear`·`drawStr(x, y, 글자)`·`setLedOnOff(x, y, 켜기)`. 지금은 `drawStr`로 `<` `>` `^` `X` 글자를 띄움)
