"""GY-906(MLX90614) 표면 온도 읽기 (Pi I2C, GPIO 2·3)

TODO: smbus2로 물체 온도 읽기, 45℃ 초과면 과열 의심
"""
from common import protocol  # noqa: F401  (메시지 이름·형식은 여기서만 가져온다)
