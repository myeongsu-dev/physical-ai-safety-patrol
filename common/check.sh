#!/usr/bin/env bash
# 파트 사이 약속 · 함께 관리
# CI가 저장소 맨 위에서 실행한다 (bash common/check.sh). 한 줄이라도 실패하면 PR에 빨간 표시가 뜬다.
# 검사를 더할 때는 아래에 명령을 한 줄씩 추가한다.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m common.protocol
