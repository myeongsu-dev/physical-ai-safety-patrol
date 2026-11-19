"""이벤트 판단 · 과열 교차 확인

TODO: 이벤트 기록(SQLite), 45℃ 의심 → 다른 로봇 출동 → 두 대 모두 50℃ 초과 + 불꽃이면 evacuate
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
