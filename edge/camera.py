"""XIAO ESP32-S3 머리 카메라 영상 받기 (USB)

CAM_PORT 종류에 따라 두 방식:
- 시리얼 경로(/dev/ttyACM1, COM5): XIAO가 JPEG를 그대로 흘려보냄(edge/xiao_cam). FFD8…FFD9로 잘라 받음
- 숫자(0) 또는 /dev/video*: UVC 카메라로 OpenCV가 받음 (PC 웹캠 시험도 이것)

받는 스레드가 계속 읽고 가장 최근 장 하나만 들고 있다. 밀린 장은 버린다.
"""
import logging
import threading
import time

from edge import config

log = logging.getLogger("edge.cam")

SOI, EOI = b"\xff\xd8", b"\xff\xd9"
MAX_BUF = 2_000_000


def split_jpegs(buf):
    """바이트 덩어리에서 완성된 JPEG들을 잘라냄 → (장 목록, 남은 바이트)"""
    frames = []
    while True:
        s = buf.find(SOI)
        if s < 0:
            return frames, buf[-1:]  # FF 하나가 잘려 있을 수 있음
        e = buf.find(EOI, s + 2)
        if e < 0:
            return frames, buf[s:]
        frames.append(buf[s:e + 2])
        buf = buf[e + 2:]


def decode(jpeg):
    import cv2
    import numpy as np

    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def encode(frame, quality=85):
    import cv2

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


class Frame:
    """한 장. jpeg와 그림(BGR) 중 있는 쪽에서 다른 쪽을 필요할 때만 만든다"""

    def __init__(self, ts, jpeg=None, image=None):
        self.ts, self._jpeg, self._image = ts, jpeg, image

    @property
    def image(self):
        if self._image is None and self._jpeg is not None:
            self._image = decode(self._jpeg)
        return self._image

    @property
    def jpeg(self):
        if self._jpeg is None and self._image is not None:
            self._jpeg = encode(self._image)
        return self._jpeg


class Camera:
    def __init__(self, port=config.CAM_PORT, baud=config.CAM_BAUD):
        self.port, self.baud = str(port), baud
        self._latest = None
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self.frames = 0  # 받은 장 수 (초당 장 수 계산용)

    @property
    def is_uvc(self):
        return self.port.isdigit() or self.port.startswith("/dev/video")

    def open(self):
        target = self._uvc_loop if self.is_uvc else self._serial_loop
        threading.Thread(target=target, name="cam", daemon=True).start()

    def close(self):
        self._closing.set()

    def latest(self, max_age=1.0):
        """가장 최근 장. max_age초보다 오래됐으면 None (카메라가 멈춘 것)"""
        with self._lock:
            f = self._latest
        if f is None or time.monotonic() - f.ts > max_age:
            return None
        return f

    def _put(self, frame):
        with self._lock:
            self._latest = frame
        self.frames += 1

    def _serial_loop(self):
        import serial

        while not self._closing.is_set():
            try:
                s = serial.Serial(self.port, self.baud, timeout=0.1)
                s.dtr = False
                s.rts = False
                log.info("카메라 연결 %s (시리얼 JPEG)", self.port)
            except Exception as e:
                log.warning("카메라 포트 열기 실패 %s: %s", self.port, e)
                time.sleep(2)
                continue
            buf = b""
            try:
                while not self._closing.is_set():
                    chunk = s.read(max(1, s.in_waiting or 4096))
                    if not chunk:
                        continue
                    buf += chunk
                    frames, buf = split_jpegs(buf)
                    if frames:
                        self._put(Frame(time.monotonic(), jpeg=frames[-1]))  # 밀린 장은 버림
                    if len(buf) > MAX_BUF:
                        buf = b""
            except Exception as e:
                log.warning("카메라 읽기 실패: %s", e)
            finally:
                try:
                    s.close()
                except Exception:
                    pass
            time.sleep(1)

    def _uvc_loop(self):
        import cv2

        src = int(self.port) if self.port.isdigit() else self.port
        while not self._closing.is_set():
            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                log.warning("카메라 열기 실패 %s", self.port)
                time.sleep(2)
                continue
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            log.info("카메라 연결 %s (UVC)", self.port)
            while not self._closing.is_set():
                ok, img = cap.read()
                if not ok:
                    break
                self._put(Frame(time.monotonic(), image=img))
            cap.release()
            time.sleep(1)


class FakeCamera:
    """카메라 없이 개발용. 회색 그림에 시각을 적어 돌려줌. image를 넣으면 그 그림을 계속 줌"""

    def __init__(self, image=None):
        self.image = image
        self.frames = 0

    def open(self):
        pass

    def close(self):
        pass

    def latest(self, max_age=1.0):
        self.frames += 1
        img = self.image
        if img is None:
            try:
                import cv2
                import numpy as np

                img = np.full((480, 640, 3), 90, np.uint8)
                cv2.putText(img, time.strftime("fake %H:%M:%S"), (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            except ImportError:
                return Frame(time.monotonic(), jpeg=SOI + b"fake" + EOI)
        return Frame(time.monotonic(), image=img)
