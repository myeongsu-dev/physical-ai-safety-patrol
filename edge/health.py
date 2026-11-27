"""로봇 상태(health): Pi · MechDog 보드 · 카메라 · 센서가 괜찮은지. state에 매초 같이 실어 서버를 거쳐 대시보드로 간다 (#54).

ok 판단은 여기서만 한다 (대시보드는 ok 값만 보고 시간 기준을 따로 두지 않음):
  board.ok  보드 포트가 열려 있고 측정값(D)이 BOARD_OK_S(1초) 안에 옴
  cam.ok    머리 카메라 장이 CAM_OK_S(2초) 안에 옴
  thermo.ok 온도 센서를 마지막으로 읽었을 때 값이 나옴
영상은 MQTT로 보내지 않고 web(디버그 화면 8080) · floor_web(바닥 비전 모듈 화면 8090, 켜져 있을 때만) 링크로 본다.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from edge import config


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




def _local_ip():
    """서버(MQTT 브로커) 쪽으로 나가는 이 기기의 주소. 패킷은 보내지 않음"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((config.MQTT_HOST, config.MQTT_PORT))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("0."):
                return ip
    except OSError:
        pass
    return None


def _listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


class Health:
    """agent.state()가 매초 부름. 무거운 것(vcgencmd · 주소 · 8090 확인)은 몇 초에 한 번만"""

    def __init__(self, agent, web_port=config.WEB_PORT, floor_port=config.FLOOR_WEB_PORT):
        self.agent, self.web_port, self.floor_port = agent, web_port, floor_port
        self.stats = SysStats()
        self._fps = (None, 0, None)        # (시각, 장 수, 초당 장 수)
        self._addr = (-1e9, None, False)   # (확인한 시각, 주소, 8090 켜짐)

    def _cam(self, now):
        cam = self.agent.cam
        frame = cam.latest(max_age=1e9) if hasattr(cam, "latest") else None
        age = round(now - frame.ts, 2) if frame else None
        n = getattr(cam, "frames", None)
        t0, n0, fps = self._fps
        if n is not None and (t0 is None or now - t0 >= 1.0):
            fps = round((n - n0) / (now - t0), 1) if t0 is not None and now > t0 else None
            self._fps = (now, n, fps)
        return {"ok": age is not None and age <= config.CAM_OK_S, "fps": fps, "age_s": age}

    def _addresses(self, now):
        t, ip, floor = self._addr
        if now - t >= 5:
            ips = self.stats.ips() or []
            ip = _local_ip() or (ips[0] if ips else None)
            floor = bool(self.floor_port) and _listening(self.floor_port)
            self._addr = (now, ip, floor)
        return ip, floor

    def snapshot(self):
        ag = self.agent
        now = ag.clock()
        b = ag.board
        d_age = None if ag.last_d < -1e8 else round(now - ag.last_d, 2)
        pi_now = time.monotonic()
        ip, floor = self._addresses(pi_now)
        thr = self.stats.throttled() or {}
        mem = self.stats.mem() or {}
        det = ag.model
        return {
            "board": {"ok": bool(ag.dog.connected) and d_age is not None and d_age <= config.BOARD_OK_S,
                      "version": b.version, "age_s": d_age, "line": b.line, "sonar_cm": b.sonar_cm,
                      "error": b.last_error},
            "cam": self._cam(now),
            "model": {"ready": bool(det is not None and det.ready), "version": getattr(det, "version", None) if det else None},
            "thermo": {"ok": getattr(ag.thermo, "last", None) is not None},
            "pi": {"cpu_temp_c": self.stats.cpu_temp(), "cpu_pct": self.stats.cpu_pct(), "mem_pct": mem.get("pct"),
                   "undervolt": thr.get("undervolt_now"), "throttled": thr.get("throttled_now")},
            "web": f"http://{ip}:{self.web_port}" if ip and self.web_port else None,
            "floor_web": f"http://{ip}:{self.floor_port}" if ip and floor else None,
        }
