#!/bin/sh
# 바닥 카메라 화면 (Wi-Fi), http://<Pi>:8090. 모듈 주소는 DHCP라 이름으로
cd ~/psp-dev
exec ~/physical-ai-safety-patrol/.venv/bin/python -u firmware/vision/view.py --url http://patrol-cam.local/stream
