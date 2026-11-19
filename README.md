# physical-ai-safety-patrol

> **AI 협동로봇 기반 공장&창고 안전관리 시스템** · 청년 피지컬AI 2기 10팀 포텐 기업 과제 코드

MechDog 4대가 공장·창고 통로를 순찰하며 통로 막힘, 안전모 미착용, 화재를 찾아 관제 대시보드에 알리고, 화재가 확정되면 갈림길에서 LED로 비상구 방향을 가리킵니다.

- 설계 문서: [시스템 아키텍처](https://myeongsu-dev.github.io/physical-ai-corp-project/week02/01_시스템_아키텍처.html) · [기능 정의서](https://myeongsu-dev.github.io/physical-ai-corp-project/week01/02_기능정의서.html) · [주차별 기록](https://myeongsu-dev.github.io/physical-ai-corp-project/)
- 파트끼리 주고받는 메시지: [docs/protocol.md](docs/protocol.md)

## 파트와 담당

| 파트 | 담당 | 폴더 | 하는 일 |
|---|---|---|---|
| MechDog | 박명수 | `firmware/dog/` | 보행, 선 따라가기, 초음파 정지·재개, LED·음성, 배터리 보고 |
| 라즈베리 파이 5 | 김건주 | `edge/` | 카메라·온도 읽기, AI 추론, 사진, 보드 명령, 서버 보고 |
| 관제 서버 | 채웅 | `server/` | MQTT, 로봇 상태·위치, 교차 확인, 교대·출동·대피, 기록 |
| 관제 대시보드 | 이예은 | `dashboard/` | 구역 지도, 사진 팝업, 경보음, 체크로 해제 |

| AI 학습 | 담당 | 폴더 |
|---|---|---|
| 안전모 (person · head · helmet) | 채웅, 이예은 | `ml/helmet/` |
| 불꽃 (fire) | 박명수, 김건주 | `ml/flame/` |

## 구조

```text
로봇 1대 = MechDog 보드 ──USB 시리얼── 라즈베리 파이 5 ──Wi-Fi·MQTT── 관제 서버 ──WebSocket── 대시보드
           (몸: 즉시 반응)              (눈: 보고 판단)                (여러 대를 엮음)
```

판단은 가장 가까운 곳에서 합니다. 멈추는 판단은 보드가, 보고 아는 판단은 Pi가, 여러 대를 엮는 판단은 서버가 맡습니다. 서버가 끊겨도 로봇은 순찰을 계속합니다.

```text
physical-ai-safety-patrol/
├── common/protocol.py   파트 사이 약속 (시리얼 명령, MQTT 토픽·메시지). 모두가 import
├── docs/protocol.md     위 약속을 사람이 읽는 표로
├── firmware/dog/        MechDog MicroPython                 · 박명수
├── edge/                Pi 5 프로그램 (Python)               · 김건주
├── server/              관제 서버 (Python, FastAPI)          · 채웅
├── dashboard/           관제 화면 (HTML·JS)                  · 이예은
├── ml/helmet/ · flame/  데이터·라벨·학습 (데이터는 커밋 안 함)
├── models/              Pi에 배포할 변환된 모델 (버전 폴더)
├── tools/mechdog-test/  MechDog 동작 시험 도구 (웹 블루투스)
└── tools/pi-power-test/ Pi 5 보조배터리 시험 (판정 · 사용 시간)
```

각 폴더의 README에 맡은 일, 입출력, 실행 방법이 있습니다.

## 시작하기

```bash
git clone https://github.com/myeongsu-dev/physical-ai-safety-patrol.git
cd physical-ai-safety-patrol
python -m common.protocol      # 셀프 체크: "protocol ok"
cp .env.example .env           # 로봇 이름, 브로커 주소 등 (커밋 안 함)
```

Python 프로그램은 저장소 맨 위에서 `python -m edge.agent`, `python -m server.api`처럼 모듈로 실행합니다. 그래야 `from common import protocol`이 어디서나 됩니다. 파트별 패키지는 각 폴더의 `requirements.txt`에 있습니다.

## 브랜치

| 브랜치 | 뜻 | 들어오는 길 |
|---|---|---|
| `main` | 시연할 수 있는 안정판. 멘토링·발표 때 이 브랜치로 돌립니다 | `dev` → `main` PR만 |
| `dev` | 기능을 모아 맞춰 보는 곳. 기본 브랜치 | 기능 브랜치 → `dev` PR |
| `파트/할일` | 기능 하나를 만드는 곳. `dev`에서 따고 끝나면 지웁니다 | 직접 push |

```bash
git switch dev && git pull                 # 1. 최신 dev에서
git switch -c edge/thermo                  # 2. 기능 브랜치 따기
git add … && git commit -m "edge: GY-906 온도 읽기"
git push -u origin edge/thermo             # 3. GitHub에서 dev로 PR
```

- 기능 브랜치 이름의 파트는 `firmware` · `edge` · `server` · `dashboard` · `ml` · `common` · `tools` · `docs` 중 하나입니다. 예: `edge/thermo`, `server/fake-robots`, `ml/flame-data`
- `main`과 `dev`에는 직접 push하지 않습니다.
- PR은 한 기능씩 작게 올리고, 다른 한 명이 보고 merge합니다. 자기 폴더 안의 작은 수정은 본인이 merge해도 됩니다.
- 작업이 길어지면 중간에 `git pull origin dev`로 dev를 받아 충돌을 일찍 풉니다.
- `dev`에서 여러 파트가 맞물려 동작하는 것을 확인하면 `dev` → `main` PR을 올립니다. 멘토링·시연 전에 한 번씩 합니다.

## CI (자동 검사)

`main` · `dev`로 push하거나 PR을 올리면 GitHub Actions가 파트마다 `<파트>/check.sh`를 따로 돌립니다(`.github/workflows/ci.yml`). PR 화면에 `edge`, `server`처럼 파트별로 결과가 뜹니다.

| 파트 | 지금 하는 검사 |
|---|---|
| `common` | 약속 셀프 체크 |
| `firmware` · `edge` · `server` · `ml` | 파이썬 문법 |
| `dashboard` | 없음 |
| `tools` | MechDog 시험 도구 셀프 체크 (파이썬 · JS), Pi 전원 시험 판정 셀프 체크 |

검사를 더할 때는 `ci.yml`은 그대로 두고 **자기 파트의 `check.sh`에 명령을 한 줄씩 추가**합니다. 올리기 전에 `bash edge/check.sh`처럼 로컬에서 먼저 돌려 봅니다. 로봇·GPU·데이터가 필요한 검사는 CI에 넣지 않습니다.

## 같이 일하는 규칙

- **자기 폴더만 고칩니다.** 다른 파트 폴더를 고칠 때는 담당에게 먼저 말합니다.
- **약속을 바꿀 때는 함께.** `common/protocol.py`와 `docs/protocol.md`는 한 커밋에서 같이 고치고, 영향을 받는 파트 담당에게 PR 리뷰를 받습니다.
- **커밋 메시지**는 `파트: 한 일` 형식, 한국어로 씁니다. 예: `edge: GY-906 온도 읽기`, `server: 가짜 로봇 4대 상태 보내기`
- **비밀값과 큰 파일은 올리지 않습니다.** `.env`, 학습 데이터, 학습 중간 결과(`runs/`)는 `.gitignore`에 있습니다. 모델은 변환한 최종본만 `models/`에 넣습니다.
- 로봇 없이 먼저 만들 수 있게, 서버·대시보드는 `server/fake_robots.py`로 개발합니다. 메시지 형식만 지키면 나중에 그대로 붙습니다.
