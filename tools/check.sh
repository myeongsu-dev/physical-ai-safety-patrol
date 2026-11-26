#!/usr/bin/env bash
# 공용 도구
# CI가 저장소 맨 위에서 실행한다 (bash tools/check.sh). 한 줄이라도 실패하면 PR에 빨간 표시가 뜬다.
# 검사를 더할 때는 아래에 명령을 한 줄씩 추가한다.
set -euo pipefail
cd "$(dirname "$0")/.."

python tools/mechdog-test/devices/dog/sim_check.py
node tools/mechdog-test/app/selfcheck.mjs
python tools/pi-power-test/power_test.py selfcheck
python -m pip install -q -r server/requirements.txt
python tools/integration-test/itest.py --selfcheck
python -c "import ast, glob; [ast.parse(open(f, encoding=\"utf-8\").read(), f) for f in glob.glob(\"tools/drive-test/*.py\")]"  # 로봇이 있어야 도는 도구라 문법만
