"""관제 서버 실행 진입점 (FastAPI). 실행: python -m server.api

TODO: WebSocket /ws로 state·event·mode 밀어 주기, POST·GET /photos, POST /events/{id}/check, dashboard/ 내어 주기
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
