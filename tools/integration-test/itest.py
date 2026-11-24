"""통합 시나리오 실행기. 서버를 띄운 PC에서 돌린다 (Pi·MechDog은 평소 프로그램 그대로).

  python tools/integration-test/itest.py <시나리오> [--server http://localhost:8000] [--robot A] [--role A]
  python tools/integration-test/itest.py --selfcheck

시나리오는 scenarios/<이름>.py (이름만 줘도 됨) 또는 파일 경로. 사용법·시나리오 쓰는 법은 README.md.
모든 단계는 PASS/FAIL로 남고, 하나라도 FAIL이면 그 자리에서 멈춘다. 어떻게 끝나든 로봇에 stop을 보낸다.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


class Stop(Exception):
    """확인 실패 → 시나리오 중단"""


class Test:
    """시나리오가 받는 t. 서버 HTTP로 명령하고, 대시보드와 같은 /ws로 상황을 본다."""

    def __init__(self, server, robot="A", role="A", answers=None, out=print):
        self.server, self.robot, self.role = server.rstrip("/"), robot, role
        self.answers = answers  # 셀프 체크용: 사람 답을 미리 줌
        self.out = out
        self.results = []       # (단계, PASS/FAIL, 내용)
        self.states, self.events, self.mode = {}, {}, None
        self.ws = None

    # ---------- 기록 ----------
    def _record(self, name, ok, detail=""):
        self.results.append((name, "PASS" if ok else "FAIL", detail))
        self.out("  [%s] %s  %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            raise Stop(name)

    def _now(self):
        s = self.state()
        return "online=%s status=%s battery=%s" % (s.get("online"), s.get("status"), s.get("battery")) if s else "상태 없음"

    # ---------- 단계 세 가지 ----------
    def do(self, name, fn):
        """동작 · 즉시 확인: 예외 없이 끝나고 False가 아니면 PASS"""
        try:
            r = fn()
        except urllib.error.HTTPError as e:
            return self._record(name, False, "HTTP %d %s" % (e.code, e.read().decode(errors="replace")[:200]))
        except Exception as e:
            return self._record(name, False, "%s: %s" % (type(e).__name__, e))
        self._record(name, r is not False)
        return r

    def expect(self, name, fn, timeout=10):
        """fn()이 참이 될 때까지 기다림 (fn은 t.state() 등을 읽음)"""
        t0 = time.time()
        while True:
            try:
                if fn():
                    return self._record(name, True, "%.1f초" % (time.time() - t0))
            except Exception:
                pass  # 아직 값이 없음
            if time.time() - t0 > timeout:
                return self._record(name, False, "%g초 안에 안 됨 (지금: %s)" % (timeout, self._now()))
            time.sleep(0.2)

    def ask(self, name, question):
        """사람이 눈으로 확인하는 단계. y면 PASS"""
        if self.answers is not None:
            a = self.answers.pop(0) if self.answers else ""
        elif sys.stdin.isatty():
            a = input("  ? %s (y/n) " % question)
        else:
            a = ""
        self._record(name, a.strip().lower().startswith("y"), "사람 확인" if a else "답 없음 (터미널에서 돌려야 함)")

    # ---------- 서버 ----------
    def http(self, method, path, body=None, **params):
        url = self.server + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            raw, ctype = r.read(), r.headers.get("Content-Type", "")
        return json.loads(raw) if "json" in ctype else raw.decode(errors="replace")

    def cmd(self, cmd, robot=None, **fields):
        """POST /robots/{로봇}/cmd (stop · resume · guide …)"""
        return self.http("POST", "/robots/%s/cmd" % (robot or self.robot), dict(cmd=cmd, **fields))

    def state(self, robot=None):
        """대시보드가 받은 것과 같은 로봇 state (없으면 {})"""
        return self.states.get(robot or self.robot, {})

    def event(self, type, robot=None):
        """대시보드가 받은 이벤트 중 type이 같은 것 목록 (해제 안 된 지난 이벤트 포함)"""
        return [e for e in self.events.values() if e.get("type") == type and e.get("robot") == (robot or self.robot)]

    def watch(self):
        """대시보드와 같은 WebSocket /ws를 받아 states · events · mode에 모음"""
        from websockets.sync.client import connect

        self.ws = connect("ws" + self.server[4:] + "/ws", open_timeout=5)

        def loop():
            try:
                for raw in self.ws:
                    m = json.loads(raw)
                    if m.get("kind") == "state":
                        self.states[m["robot"]] = m
                    elif m.get("kind") == "event":
                        self.events[m["id"]] = m
                    elif m.get("kind") == "mode":
                        self.mode = m["mode"]
            except Exception:
                pass

        threading.Thread(target=loop, daemon=True).start()


def load(name):
    p = Path(name)
    if p.suffix != ".py":
        p = HERE / "scenarios" / (name + ".py")
    spec = importlib.util.spec_from_file_location(p.stem, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip() or "-"
    except OSError:
        return "-"


def run(mod, t):
    """시나리오 하나를 돌리고 결과 표를 찍는다. 전부 PASS면 0"""
    title = (mod.__doc__ or mod.__name__).strip().splitlines()[0]
    t.out("시나리오: %s\n서버 %s · 로봇 %s · 역할 %s\n" % (title, t.server, t.robot, t.role))
    end = ""
    try:
        t.do("대시보드 피드 연결 (WebSocket /ws)", t.watch)
        mod.run(t)
    except Stop:
        end = "FAIL에서 멈춤, 이후 단계는 안 함"
    except KeyboardInterrupt:
        t.results.append(("(중단)", "FAIL", "Ctrl-C"))
        end = "Ctrl-C로 멈춤"
    finally:
        try:  # 안전: 어떻게 끝나든 로봇을 세움
            t.cmd("stop")
        except Exception as e:
            t.out("  경고: 끝 정지 명령 실패 (%s). 로봇을 직접 세우세요" % e)
        if t.ws:
            t.ws.close()
    ok = bool(t.results) and all(r[1] == "PASS" for r in t.results) and not end
    t.out("\n## 통합 시나리오: %s\n" % title)
    t.out("- 결과: **%s** (%d단계 중 PASS %d)%s" % ("PASS" if ok else "FAIL", len(t.results),
                                              sum(r[1] == "PASS" for r in t.results), " · " + end if end else ""))
    t.out("- 시각 %s · 커밋 %s · 로봇 %s\n" % (time.strftime("%Y-%m-%d %H:%M"), commit(), t.robot))
    t.out("| # | 단계 | 결과 | 내용 |\n|---|---|---|---|")
    for i, (name, res, detail) in enumerate(t.results, 1):
        t.out("| %d | %s | %s | %s |" % (i, name, res, detail.replace("|", "/")))
    return 0 if ok else 1


def selfcheck():
    """진짜 서버(브로커 없이)와 가짜 Pi로 patrol_line을 돌려 본다: 다 되면 PASS, Pi가 안 따르면 FAIL + stop"""
    import socket

    import uvicorn

    sys.path.insert(0, str(ROOT))
    from common import protocol
    from server.api import Server, create_app

    srv = Server(mqtt=False)
    pi = {"status": "idle", "obey": True}

    def publish(topic, msg, qos=1):  # 가짜 Pi: 명령을 받으면 상태를 바꿈
        srv.sent.append((topic, msg))
        if topic == protocol.topic("A", "cmd") and pi["obey"]:
            pi["status"] = {"patrol": "patrol", "resume": "patrol", "stop": "idle"}.get(msg["cmd"], pi["status"])
    srv.publish = publish

    def fake_pi():
        while True:
            if time.time() >= pi.get("quiet_until", 0):  # Wi-Fi 끊김 동안은 조용
                srv.on_mqtt("potent/A/state", protocol.pack({"marker": None, "status": pi["status"], "battery": 90}))
            time.sleep(0.2)

    def drop_wifi(line):  # 사람이 "Wi-Fi 끊기"에 y → 가짜 Pi가 ONLINE_SEC보다 길게 조용
        if "] Wi-Fi 끊기 " in line:
            pi["quiet_until"] = time.time() + 8
    threading.Thread(target=fake_pi, daemon=True).start()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    web = uvicorn.Server(uvicorn.Config(create_app(srv), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=web.run, daemon=True).start()
    while not web.started:
        time.sleep(0.05)
    url, quiet = "http://127.0.0.1:%d" % port, (lambda *a: None)
    try:
        t = Test(url, answers=["y"] * 20, out=drop_wifi)
        assert run(load("patrol_line"), t) == 0, t.results
        assert ("끊김 표시 (서버 → 대시보드)", "PASS") in [r[:2] for r in t.results], t.results
        assert srv.sent[-1] == ("potent/A/cmd", {"cmd": "stop"})
        pi["obey"] = False  # Pi가 순찰 명령을 안 따름 → 순찰 상태에서 FAIL, 그 뒤 단계 없음, 끝에 stop
        t = Test(url, answers=["y"] * 20, out=quiet)
        assert run(load("patrol_line"), t) == 1
        assert t.results[-1][:2] == ("순찰 상태 (Pi가 MechDog F 응답을 받음)", "FAIL"), t.results
        assert srv.sent[-1] == ("potent/A/cmd", {"cmd": "stop"})
        pi["obey"] = True  # 사람이 n이라고 하면 FAIL
        t = Test(url, answers=["y", "n"], out=quiet)
        assert run(load("patrol_line"), t) == 1 and t.results[-1][1] == "FAIL"
    finally:
        web.should_exit = True
    print("integration-test ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", help="scenarios/ 안의 이름 또는 .py 경로")
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--robot", default="A", help="시험할 로봇 이름 (.env의 ROBOT_ID)")
    ap.add_argument("--role", default="A", help="순찰 역할 (A 바깥 · B 안쪽)")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()
    if not a.scenario:
        names = sorted(p.stem for p in (HERE / "scenarios").glob("*.py"))
        ap.error("시나리오를 주세요: " + ", ".join(names))
    return run(load(a.scenario), Test(a.server, a.robot, a.role))


if __name__ == "__main__":
    sys.exit(main())
