#!/usr/bin/env bash
# MechDog 보드 프로그램 · 박명수
# CI가 저장소 맨 위에서 실행한다 (bash firmware/check.sh). 한 줄이라도 실패하면 PR에 빨간 표시가 뜬다.
# 검사를 더할 때는 아래에 명령을 한 줄씩 추가한다.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m compileall -q firmware
python firmware/dog/sim_check.py
python -c "import sys; sys.path.insert(0, 'firmware/vision'); import build; assert build.trim(b'\x01' + b'\xff' * 5000) == b'\x01' + b'\xff' * 4095"
test -s firmware/vision/patrol_cam.bin  # 팀원이 빌드 없이 굽는 펌웨어
