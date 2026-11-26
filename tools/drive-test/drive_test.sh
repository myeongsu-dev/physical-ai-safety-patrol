#!/bin/sh
# drive: 선 따라가기 기록 + 바닥 카메라 영상(view.py가 다시 내보내는 8090, 상자 그려진 것)을 같은 이름으로 저장
#   ~/psp-tools/drive_test.sh 보폭 최대각도   → ~/test_logs/drive/날짜_시각.log · .mjpg
D=~/test_logs/drive
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$D"
curl -s http://localhost:8090/stream -o "$D/$TS.mjpg" &
C=$!
trap 'kill $C 2>/dev/null' EXIT HUP INT TERM
cd ~/psp-dev && ~/physical-ai-safety-patrol/.venv/bin/python -u ~/psp-tools/walk_forever.py "$1" "$2" "$D/$TS.log"
