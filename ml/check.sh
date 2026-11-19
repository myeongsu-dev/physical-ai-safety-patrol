#!/usr/bin/env bash
# AI 학습 · 안전모(채웅·이예은) · 불꽃(박명수·김건주)
# CI가 저장소 맨 위에서 실행한다 (bash ml/check.sh). 한 줄이라도 실패하면 PR에 빨간 표시가 뜬다.
# 검사를 더할 때는 아래에 명령을 한 줄씩 추가한다.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m compileall -q ml
# 학습은 CI에서 돌리지 않는다 (GPU·데이터 없음). 스크립트 문법과 작은 검사만
