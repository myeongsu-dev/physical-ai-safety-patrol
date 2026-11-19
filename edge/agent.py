"""Pi 5 실행 진입점: 판단 · MQTT 보고 · 서버 명령 처리. 실행: python -m edge.agent

TODO: state 1초마다, event는 생길 때(사진은 POST /photos 뒤 주소 첨부), cmd·mode 받아 보드에 전달, 서버가 끊기면 이벤트를 쌓아 두었다 다시 보내기
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
