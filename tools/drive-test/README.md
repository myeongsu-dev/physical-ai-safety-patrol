# 주행 시험 도구 (Pi에서)

로봇 Pi에서 MechDog을 직접 움직여 선 따라가기 · 걷는 속도 · 도는 속도를 재고 기록을 남깁니다. edge agent 없이 보드에 바로 명령을 보내니, agent가 켜져 있으면 먼저 끕니다.

## Pi에 두는 곳

| 무엇 | 경로 |
|---|---|
| 이 폴더 사본 | `~/psp-tools/` (`scp tools/drive-test/* pi5@<Pi 주소>:psp-tools/`) |
| 시험할 코드 | `~/psp-dev` (dev worktree), 파이썬은 `~/physical-ai-safety-patrol/.venv/bin/python` |
| 기록 | `~/test_logs/<시험>/날짜_시각.log`. 시험별 폴더: `drive`(주행), `speed`(걷는 속도), `turn`(도는 속도) |
| 주행 영상 | `~/test_logs/drive/날짜_시각.mjpg` (같은 이름의 `.log`와 짝, 바닥 카메라 화면 그대로) |
| 카메라 화면 실행 기록 | `~/test_logs/view.log` |

`/tmp`는 전원을 끄면 지워집니다(Debian 13). 기록과 도구를 `/tmp`에 두지 않습니다(2026-11-27 배터리 방전으로 껐다가 주행 기록을 잃음).

## 파일

| 파일 | 하는 일 |
|---|---|
| `drive_test.sh 보폭 최대각도` | 주행 시험. `walk_forever.py`로 선을 따라가며 기록하고, 바닥 카메라 영상(`view_run.sh`가 띄운 8090)을 같은 이름으로 저장 |
| `walk_forever.py` | 선 따라가기를 시간 제한 없이(최대 5분). 보드가 `E lost` · `E stop`을 보내거나 `/tmp/walk.stop`이 생기면 멈춤. 기록은 파일과 화면에 같이 |
| `turn_rate.py speed · turn · rest` | `speed`: 벽을 보고 앞뒤로 걸어 초음파로 걷는 속도. `turn`: 조향별 도는 속도(자이로). `rest`: 벽에 막혀 못 잰 회전만 |
| `view_run.sh` | 바닥 카메라 화면 `http://<Pi>:8090` (Wi-Fi, 모듈은 `patrol-cam.local`) |
| `repl.py 코드파일` | 보드 main을 멈추고 raw REPL에서 코드를 돌린 뒤 소프트 리셋으로 main.py를 다시 띄움 (라이브러리 함수 목록 보기, 업로드 뒤 메모리 부족일 때) |
| `i2cdump.py [횟수]` | 보드가 비전 모듈에서 받는 0xB0 21바이트를 그대로 찍음 |

## 안전

- 로봇을 움직이는 스크립트는 `ssh -tt`로 돌립니다. ssh가 끊기면 SIGHUP을 받아 정지(S)를 보내고 끝납니다. `-tt` 없이 돌리면 ssh를 끊어도 Pi에서 계속 돌아 벽에 부딪힌 적이 있습니다.
- 멈출 때는 `touch /tmp/walk.stop`.
- 앞으로 걷는 중 초음파 20 cm 안이면 시험 스크립트가, 10 cm 안이면 보드가 스스로 멈춥니다. 초음파는 앞만 봅니다.
- 배터리: 출발 전 7.0 V 밑이면 안 가고, 걷는 중 6.7 V 밑이 3번 연속이면 멈춥니다. MechDog 자체 부저는 약 6.8~6.9 V에서 울립니다.
