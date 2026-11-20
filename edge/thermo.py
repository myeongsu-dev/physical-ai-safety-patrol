"""GY-906(MLX90614) 표면 온도 읽기 (Pi I2C 1번, GPIO 2·3)

배선: VIN→1번(3.3V), SDA→3번(GPIO2), SCL→5번(GPIO3), GND→9번
확인: sudo raspi-config nonint do_i2c 0 → i2cdetect -y 1 에 5a가 보여야 함
"""
import logging
import time

from edge import config

log = logging.getLogger("edge.thermo")

REG_AMBIENT = 0x06  # 센서 자체 온도
REG_OBJECT = 0x07   # 보는 곳 표면 온도 (시야 35°)


def raw_to_c(raw):
    """MLX90614 RAM 값 → ℃. 맨 위 비트가 1이면 센서 오류"""
    if raw & 0x8000:
        raise ValueError("센서 오류 플래그")
    return round(raw * 0.02 - 273.15, 2)


def is_suspect(temp_c, limit=config.HEAT_SUSPECT_C):
    """F-05: 45℃ 초과면 과열 의심"""
    return temp_c is not None and temp_c > limit


class Thermo:
    def __init__(self, bus=config.I2C_BUS, addr=config.THERMO_ADDR):
        self.bus_no, self.addr = bus, addr
        self.bus = None
        self.last = None

    def open(self):
        try:
            from smbus2 import SMBus

            self.bus = SMBus(self.bus_no)
            log.info("온도 센서 I2C %d 주소 0x%02X", self.bus_no, self.addr)
        except Exception as e:
            self.bus = None
            log.warning("온도 센서 열기 실패: %s", e)

    def close(self):
        if self.bus:
            self.bus.close()

    def _read(self, reg):
        if self.bus is None:
            return None
        for _ in range(2):  # I2C가 한 번 튀어도 다시
            try:
                return raw_to_c(self.bus.read_word_data(self.addr, reg))
            except Exception as e:
                err = e
                time.sleep(0.01)
        log.debug("온도 읽기 실패: %s", err)
        return None

    def read(self):
        """표면 온도 ℃, 못 읽으면 None"""
        self.last = self._read(REG_OBJECT)
        return self.last

    def ambient(self):
        return self._read(REG_AMBIENT)


class FakeThermo:
    """센서 없이 개발용. value를 바꾸거나, script=[(시작초, 끝초, ℃), …]로 시간에 따라 바뀜"""

    def __init__(self, value=25.0, script=None, clock=time.monotonic):
        self.value, self.script, self.clock = value, script or [], clock
        self._t0 = clock()
        self.last = None

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        t = self.clock() - self._t0
        v = self.value
        for a, b, temp in self.script:
            if a <= t < b:
                v = temp
        self.last = v
        return v

    def ambient(self):
        return 25.0
