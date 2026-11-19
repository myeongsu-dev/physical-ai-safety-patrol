"""가짜 로봇 4대. 실제 로봇과 같은 MQTT 메시지로 상태·이벤트를 흉내 냄. 실행: python -m server.fake_robots

TODO: A~D 상태를 1초마다 보내고 가끔 obstacle·helmet·heat 이벤트 보내기, cmd에 반응하기
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
