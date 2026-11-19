"""YOLO 추론. models/의 안전모 모델(person·head·helmet)과 불꽃 모델(fire)을 불러 씀

TODO: 최신 버전 모델 불러오기, 한 장 넣으면 [(클래스, 신뢰도, 상자)] 돌려주기, 최근 5장 중 3장 확정
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
