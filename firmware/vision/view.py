"""비전 모듈 영상 보기 (patrol_cam 펌웨어). 모듈 USB가 꽂힌 기기(Pi·PC)에서 실행하고 브라우저로 본다.

  python firmware/vision/view.py [포트] [--thr N] [--http 8090]
  → 같은 네트워크의 브라우저에서 http://<이 기기 주소>:8090

포트를 안 주면 .env의 VISION_PORT. 켜는 동안만 영상을 보내고(STREAM 1), 끄면 멈춘다(STREAM 0).
검은 선 찾기는 영상과 상관없이 계속 돈다. --thr로 검정 기준 밝기를 바꾸면 모듈에 저장된다.
"""
import argparse
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from edge import config  # noqa: E402
from edge.camera import split_jpegs  # noqa: E402

BAUD = 460800  # patrol_cam.ino와 같게
PAGE = """<!doctype html><meta charset="utf-8"><title>vision</title>
<body style="margin:0;background:#111;color:#ccc;font:14px sans-serif">
<img src="/stream" style="width:100%;max-width:960px;image-rendering:pixelated;display:block">
<p style="padding:8px">초록 상자: 찾은 검은 선. 회색 줄: 구역 경계(위 0xA8, 가운데 0xA9)</p></body>""".encode()

latest = {"jpg": None, "n": 0}


def open_port(port):
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, BAUD, 0.1
    if sys.platform == "win32":  # 모듈이 재시작되지 않게 (edge/dog_link.py와 같은 방식)
        s.dtr = s.rts = False
    s.open()
    return s


def reader(s):
    buf = b""
    while True:
        buf += s.read(s.in_waiting or 1)
        frames, buf = split_jpegs(buf)
        for f in frames:
            latest["jpg"], latest["n"] = f, latest["n"] + 1
        if len(buf) > 200_000:  # 깨진 장이 쌓이지 않게
            buf = b""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
        self.end_headers()
        seen = -1
        try:
            while True:
                if latest["n"] == seen or latest["jpg"] is None:
                    time.sleep(0.02)
                    continue
                seen, jpg = latest["n"], latest["jpg"]
                self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=config._get("VISION_PORT", None))
    ap.add_argument("--thr", type=int, help="검정 기준 밝기 1~254 (작을수록 더 검은 것만)")
    ap.add_argument("--http", type=int, default=8090)
    a = ap.parse_args()
    if not a.port:
        sys.exit("포트를 주거나 .env에 VISION_PORT를 적으세요 (찾기: python -m edge.hwcheck ports)")
    s = open_port(a.port)
    if a.thr:
        s.write(b"THR %d\n" % a.thr)
    s.write(b"STREAM 1\n")
    threading.Thread(target=reader, args=(s,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.http), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("영상: http://<이 기기 주소>:%d  (끝내기 Ctrl-C)" % a.http)
    try:
        last = 0
        while True:
            time.sleep(2)
            print("받은 장 %d (초당 %.1f)" % (latest["n"], (latest["n"] - last) / 2))
            last = latest["n"]
    except KeyboardInterrupt:
        pass
    finally:
        s.write(b"STREAM 0\n")
        time.sleep(0.2)
        s.close()


if __name__ == "__main__":
    main()
