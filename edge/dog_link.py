"""보드와 CMD|…|$ 주고받기 (USB 시리얼). docs/protocol.md 1절

TODO: DTR·RTS를 끈 채 DOG_PORT 열기, protocol.dog_cmd로 보내고 parse_dog로 받기, 1초에 한 번 이상 보내 보드의 2초 감시 막기
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
