# 비전 모듈 펌웨어 · 박명수

MechDog 바닥 카메라(ESP32-S3 비전 모듈, 카메라 GC2145)에 올리는 펌웨어 `patrol_cam`과 굽기·영상 보기 도구입니다.

| 하는 일 | 언제 |
|---|---|
| 검은 선 찾기 → MechDog 보드가 I2C로 읽음 | 항상 |
| 영상 보내기 (USB 시리얼, 찾은 선 상자를 그려서) | `view.py`를 켠 동안만 |

영상은 Wi-Fi 없이 모듈 USB로 나옵니다. 모듈을 Pi에 꽂아 두면 Pi에서 `view.py`를 켜고 PC 브라우저로 봅니다.

## 영상 보기

```
python firmware/vision/view.py [포트] [--thr N]
```

- 브라우저에서 `http://<모듈이 꽂힌 기기 주소>:8090`을 엽니다. 초록 상자가 찾은 선, 회색 줄이 구역 경계입니다.
- 켜는 동안만 영상을 보내고 Ctrl-C로 끄면 멈춥니다. 선 찾기는 영상과 상관없이 계속 돕니다.
- `--thr N`: 검정 기준 밝기(1~254, 처음 60). 바닥까지 검정으로 잡히면 낮추고, 테이프를 놓치면 올립니다. 모듈에 저장됩니다.
- 포트를 안 주면 `.env`의 `VISION_PORT`를 씁니다. 포트는 기기마다 다르니 `python -m edge.hwcheck ports`로 찾습니다. 비전 모듈과 MechDog 보드는 같은 CH340 칩이라 Pi에서는 꽂은 USB 자리로 정해지는 `/dev/serial/by-path/...` 이름이 안 헷갈립니다.

## MechDog 보드가 읽는 값

Hiwonder 비전 모듈과 같은 I2C 형식(주소 `0x53`)이라 보드 라이브러리 `cam.line_follow(cam.BLACK)`가 그대로 읽습니다.

- 화면을 위·가운데·아래 3구역으로 나눠 위(`0xA8`)·가운데(`0xA9`) 구역에서 가장 굵은 검정 띠를 찾습니다.
- 각 7바이트 `[ID 5, 중심x, 중심y, 좌상x, 좌상y, 우하x, 우하y]`, 좌표는 160x120 기준(가운데 x=80). 못 찾으면 모두 0.
- 다른 레지스터(빨강·노랑 등 색, 얼굴)는 0입니다.

## 빌드·굽기

```
arduino-cli compile -b esp32:esp32:esp32s3:PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB --output-dir build firmware/vision/patrol_cam
python firmware/vision/flash.py build/patrol_cam.ino.merged.bin [포트]
```

- `arduino-cli`와 ESP32 보드 패키지(`esp32:esp32` 3.x)가 필요합니다. `flash.py`는 `pip install esptool`.
- `flash.py`는 주소 `0x0`, 460800, 80MHz, DIO로 굽습니다(Hiwonder 안내와 같음). `DOG_PORT`와 같은 포트를 주면 굽지 않고 멈춥니다.
- Hiwonder 공장 펌웨어(.bin)로 되돌릴 때도 `flash.py`로 굽습니다. 공장 펌웨어 파일은 Hiwonder MechDog 자료의 비전 모듈 펌웨어 폴더에 있습니다.
