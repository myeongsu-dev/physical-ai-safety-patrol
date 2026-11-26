"""디버깅용 웹 화면. agent 안에서 스레드로 돌며 브라우저로 카메라·판단·Pi 상태를 본다 (읽기 전용)

  python -m edge.agent              # 기본으로 켜짐 → 브라우저에서 http://<Pi IP>:8080
  python -m edge.agent --web 0      # 끄기 (.env WEB_PORT=0 도 같음)

카메라 포트는 agent가 열고 있어 따로 띄우지 않고 같은 프로세스에서 agent의 카메라·상태를 그대로 읽는다.
명령(걷기·정지)은 보내지 않는다. 같은 네트워크의 누구나 볼 수 있으니 공용망에서는 WEB_PORT=0.

  /                 화면 (edge/webview.html)
  /stream.mjpg      머리 카메라 MJPEG (WEB_FPS장/초까지)
  /snapshot.jpg     지금 한 장
  /api/status       agent·보드·카메라·모델·MQTT·Pi 상태 JSON
  /api/log          최근 기록 (?after=번호 → 그 뒤 줄만)
"""
import collections
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from edge import config

log = logging.getLogger("edge.web")

PAGE = Path(__file__).with_name("webview.html")
BOUNDARY = "frame"


# ---------- 최근 기록 ----------
class LogRing(logging.Handler):
    """최근 기록 n줄을 번호와 함께 들고 있음 (웹에서 이어 받기)"""

    def __init__(self, n=500):
        super().__init__(logging.INFO)
        self.lines = collections.deque(maxlen=n)
        self.seq = 0
        self._lk = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            return
        with self._lk:
            self.seq += 1
            self.lines.append((self.seq, record.levelname, msg))

    def since(self, after=0):
        with self._lk:
            return [{"n": n, "level": lv, "msg": m} for n, lv, m in self.lines if n > after]


# ---------- Pi 상태 (/proc · /sys, 없으면 None) ----------
def _read(path):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


class SysStats:
    def __init__(self):
        self._cpu_prev = None
        self._throttle = (0.0, None)
        self._ips = (0.0, None)

    def cpu_pct(self):
        s = _read("/proc/stat")
        if not s:
            return None
        v = [int(x) for x in s.splitlines()[0].split()[1:]]
        idle, total = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    @staticmethod
    def cpu_temp():
        t = _read("/sys/class/thermal/thermal_zone0/temp")
        return round(int(t) / 1000, 1) if t and t.isdigit() else None

    @staticmethod
    def mem():
        s = _read("/proc/meminfo")
        if not s:
            return None
        kv = {ln.split(":")[0]: int(ln.split()[1]) for ln in s.splitlines() if len(ln.split()) >= 2}
        total, avail = kv.get("MemTotal"), kv.get("MemAvailable")
        if not total or avail is None:
            return None
        return {"used_mb": (total - avail) // 1024, "total_mb": total // 1024, "pct": round(100 * (total - avail) / total, 1)}

    def throttled(self):
        """vcgencmd get_throttled → 저전압·과열 제한 (5초마다만 물음)"""
        now = time.monotonic()
        if now - self._throttle[0] < 5:
            return self._throttle[1]
        val = None
        try:
            r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=1)
            bits = int(r.stdout.strip().split("=")[1], 16)
            val = {"raw": hex(bits), "undervolt_now": bool(bits & 0x1), "throttled_now": bool(bits & 0x4),
                   "undervolt_seen": bool(bits & 0x10000), "throttled_seen": bool(bits & 0x40000)}
        except Exception:
            pass
        self._throttle = (now, val)
        return val

    def ips(self):
        now = time.monotonic()
        if now - self._ips[0] < 30:
            return self._ips[1]
        val = None
        try:
            r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=1)
            val = r.stdout.split() or None
        except Exception:
            pass
        self._ips = (now, val)
        return val

    def snapshot(self):
        up = _read("/proc/uptime")
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except (OSError, AttributeError):
            load = None
        try:
            du = shutil.disk_usage(str(config.ROOT))
            disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1),
                    "pct": round(100 * du.used / du.total, 1)}
        except Exception:
            disk = None
        return {"host": socket.gethostname(), "ips": self.ips(), "platform": sys.platform,
                "uptime_s": int(float(up.split()[0])) if up else None, "load": load, "cpu_pct": self.cpu_pct(),
                "cpu_temp_c": self.cpu_temp(), "mem": self.mem(), "disk": disk, "throttled": self.throttled()}


# ---------- agent 상태 ----------
def _age(now, t):
    return None if t is None or t < -1e8 else round(now - t, 2)


def collect(agent, stats, fps):
    """agent를 건드리지 않고 읽기만 함 (다른 스레드라 값이 한 박자 어긋날 수 있음)"""
    now = agent.clock()
    dog, cam, b, rep, det = agent.dog, agent.cam, agent.board, agent.reporter, agent.model
    frame = cam.latest(max_age=1e9) if hasattr(cam, "latest") else None
    dets = getattr(agent, "last_dets", None) or {}
    return {
        "robot": config.ROBOT_ID, "version": agent.version, "time": time.strftime("%H:%M:%S"),
        "agent": {"state": agent.state(), "status": agent.status, "hold": agent.hold, "mode": agent.mode,
                  "acked": agent.acked, "target": agent.target, "resume_to": agent.resume_to,
                  "blocked": agent.blocked and {k: v for k, v in agent.blocked.items() if k != "since"},
                  "heat": agent.heat and {"temps": agent.heat["temps"], "flame": agent.heat["flame"],
                                          "flame_hit": agent.heat["flame_hit"], "reported": agent.heat["reported"]},
                  "helmet_votes": list(agent.helmet_vote.hist)},
        "board": {"port": getattr(dog, "port", "fake"), "connected": dog.connected, "fake": type(dog).__name__ != "DogLink",
                  "opens": getattr(dog, "opens", None), "quiet_breaks": getattr(dog, "quiet_breaks", None),
                  "version": b.version, "commands": b.commands, "values": b.values, "battery": b.battery,
                  "sonar_cm": b.sonar_cm, "line": b.line, "last_error": b.last_error,
                  "d_age_s": _age(now, agent.last_d)},
        "camera": {"port": getattr(cam, "port", "fake"), "fake": type(cam).__name__ != "Camera",
                   "frames": getattr(cam, "frames", None), "fps": fps,
                   "frame_age_s": round(time.monotonic() - frame.ts, 2) if frame else None},
        "model": {"ready": bool(det is not None and det.ready), "version": getattr(det, "version", None) if det else None,
                  "path": str(getattr(det, "path", "") or "") or None, "ms": round(getattr(det, "ms", None) or 0, 1) or None,
                  "imgsz": getattr(det, "imgsz", None), "dets": dets.get("dets", []), "size": dets.get("size"),
                  "dets_age_s": _age(now, dets.get("ts"))},
        "mqtt": {"host": f"{config.MQTT_HOST}:{config.MQTT_PORT}", "connected": getattr(rep, "connected", None),
                 "outbox": len(getattr(rep, "outbox", []) or [])},
        "temp_c": agent.temp_c,
        "pi": stats.snapshot(),
    }


# ---------- 웹 서버 ----------
class WebView:
    def __init__(self, agent, port=config.WEB_PORT, host="0.0.0.0", fps=config.WEB_FPS):
        self.agent, self.port, self.host, self.max_fps = agent, port, host, max(1, fps)
        self.ring = LogRing()
        self.stats = SysStats()
        self.httpd = None
        self._fps = (0.0, 0, None)   # (시각, 장 수, 초당 장 수)

    def cam_fps(self):
        now, n = time.monotonic(), getattr(self.agent.cam, "frames", 0) or 0
        t0, n0, fps = self._fps
        if now - t0 >= 1.0:
            fps = round((n - n0) / (now - t0), 1) if t0 else None
            self._fps = (now, n, fps)
        return fps

    def start(self):
        logging.getLogger().addHandler(self.ring)
        view = self

        class Handler(_Handler):
            web = view

        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as e:
            log.warning("웹 화면 못 띄움 (포트 %d): %s", self.port, e)
            return False
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]   # port=0이면 빈 포트 (시험용)
        threading.Thread(target=self.httpd.serve_forever, name="web", daemon=True).start()
        log.info("웹 화면 http://%s:%d", (self.stats.ips() or ["localhost"])[0], self.port)
        return True

    def stop(self):
        logging.getLogger().removeHandler(self.ring)
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    web = None  # WebView
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 요청마다 기록하지 않음
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/status":
                data = collect(self.web.agent, self.web.stats, self.web.cam_fps())
                self._send(200, json.dumps(data, ensure_ascii=False, default=str).encode(), "application/json")
            elif u.path == "/api/log":
                after = int((parse_qs(u.query).get("after") or ["0"])[0] or 0)
                self._send(200, json.dumps(self.web.ring.since(after), ensure_ascii=False).encode(), "application/json")
            elif u.path == "/snapshot.jpg":
                f = self.web.agent.cam.latest(max_age=5)
                jpeg = f.jpeg if f else None
                if jpeg:
                    self._send(200, jpeg, "image/jpeg")
                else:
                    self._send(503, "카메라 장 없음".encode(), "text/plain; charset=utf-8")
            elif u.path == "/stream.mjpg":
                self._stream()
            else:
                self._send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        period, last_ts = 1.0 / self.web.max_fps, None
        while True:
            t = time.monotonic()
            f = self.web.agent.cam.latest(max_age=2)
            if f is not None and f.ts != last_ts:
                last_ts = f.ts
                jpeg = f.jpeg
                if jpeg:
                    self.wfile.write(b"--" + BOUNDARY.encode() + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                    self.wfile.flush()
            time.sleep(max(0.02, period - (time.monotonic() - t)))
