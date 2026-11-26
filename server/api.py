"""관제 서버 실행 진입점 (FastAPI). 실행: python -m server.api  → http://localhost:8000
셀프 체크(브로커 없이): python -m server.api --selfcheck

MQTT(potent/+/state · event)를 받아 fleet·events에 반영하고 대시보드 WebSocket /ws로 밀어 준다.
heat 이벤트는 events.HeatCheck로 교차 확인해 출동·대피 명령을 보낸다.

| 경로 | 하는 일 |
|---|---|
| WebSocket /ws | 붙자마자 지금 상태(state 전부·mode·해제 안 된 event) → 이후 새 메시지 |
| POST /photos | Pi 사진 받기 (form: file, robot) → {"url": "/photos/이름"} |
| GET /photos/{이름} | 사진 |
| POST /events/{id}/check | 관리자가 경보를 닫음. body {"false_alarm": true}면 오탐으로 저장 (body 없으면 정탐) |
| GET /events?limit=50&before=id&type=heat | 지난 경보 목록 (닫힌 것 포함), 최근 것부터 |
| GET /admin/check | 관리자 비밀번호 맞는지 (맞으면 200, 틀리면 401) |
| POST /fleet/start | 비어 있는 역할(A·B)을 대기 로봇에게 나눠 주며 출발 |
| POST /robots/{로봇}/patrol?role=A | 역할을 직접 정해 출발 |
| POST /robots/{로봇}/cmd | 명령 직접 보내기 (body: {"cmd": "stop"} 등) |
| POST /mode | {"mode": "normal" · "evacuate"} (normal로 돌리면 교차 확인도 처음부터) |
| GET /status | 지금 상태 (확인용) |

관리자 잠금: .env에 ADMIN_PASSWORD를 적으면 경보 닫기 · 출발 · 명령 · 모드 변경은
헤더 X-Admin-Password가 맞을 때만 받는다 (틀리면 401). 비워 두면 잠그지 않는다 (개발용).

state 메시지의 online이 false면 연결 끊김 (ONLINE_SEC초 넘게 state 없음). 끊길 때 한 번, 다시 붙을 때 다시 보낸다.
| / | dashboard/ 폴더 (index.html이 생기면) |
"""
import argparse
import asyncio
import hmac
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from common import protocol
from server.events import EventLog, HeatCheck
from server.fleet import ONLINE_SEC, Fleet

log = logging.getLogger("server")

ROOT = Path(__file__).resolve().parent.parent
PHOTO_DIR = Path(__file__).resolve().parent / "photos"
DB_PATH = Path(__file__).resolve().parent / "events.sqlite3"
DASHBOARD_DIR = ROOT / "dashboard"
PHOTO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.(jpg|jpeg|png)$")


class Hub:
    """MQTT 스레드에서 온 메시지를 웹 서버 쪽 WebSocket들로 나눠 보낸다."""

    def __init__(self):
        self.clients = set()
        self.loop = None
        self.queue = None

    def start(self, loop):
        self.loop, self.queue = loop, asyncio.Queue()

    def push(self, msg):
        """어느 스레드에서든 부를 수 있다."""
        if self.loop:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, msg)

    async def pump(self):
        while True:
            msg = await self.queue.get()
            for ws in list(self.clients):
                try:
                    await ws.send_json(msg)
                except Exception:
                    self.clients.discard(ws)


class Server:
    """MQTT와 화면 사이에서 상태를 들고 있는 것. mqtt=False면 브로커 없이(시험용)."""

    def __init__(self, db_path=":memory:", photo_dir=PHOTO_DIR, mqtt=True, host="localhost", port=1883,
                 clock=time.time, admin_password=None):
        self.clock = clock
        self.admin_password = admin_password or None   # 없으면 잠그지 않음
        self.fleet = Fleet(clock=clock)
        self.linked = {}    # 로봇 → 지난번에 본 연결 상태 (끊김·다시 붙음을 화면에 알리려고)
        self.events = EventLog(str(db_path))
        self.heat = HeatCheck(self.fleet)
        self.lock = threading.Lock()   # MQTT 스레드와 1초 tick이 heat를 같이 건드림
        self.hub = Hub()
        self.mode = "normal"
        self.photo_dir = Path(photo_dir)
        self.host, self.port = host, port
        self.client = None
        self.sent = []      # mqtt=False일 때 보낸 것 (시험용)
        self.use_mqtt = mqtt

    # ---------- MQTT ----------
    def connect(self):
        if not self.use_mqtt:
            return
        import paho.mqtt.client as mqtt

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="potent-server")
        c.on_connect = lambda cl, u, f, rc, p=None: (
            log.info("브로커 연결 %s:%s (%s)", self.host, self.port, rc),
            cl.subscribe(f"{protocol.ROOT}/+/state", qos=0),
            cl.subscribe(f"{protocol.ROOT}/+/event", qos=1))
        c.on_message = lambda cl, u, m: self.on_mqtt(m.topic, m.payload)
        c.connect_async(self.host, self.port)
        c.loop_start()
        self.client = c

    def close(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    def publish(self, topic, msg, qos=1):
        if self.client:
            self.client.publish(topic, protocol.pack(msg), qos=qos)
        else:
            self.sent.append((topic, msg))

    def on_mqtt(self, topic, payload):
        t = protocol.parse_topic(topic)
        if not t:
            return
        robot, kind = t
        try:
            msg = protocol.unpack(payload)
        except ValueError as e:
            log.warning("깨진 메시지 버림 %s: %s", topic, e)
            return
        if kind == "state":
            self.fleet.update_state(robot, msg)
            if self.linked.get(robot) is False:
                log.info("%s 다시 연결됨", robot)
            self.linked[robot] = True
            self.hub.push(self.state_msg(robot))
        elif kind == "event":
            try:
                e = self.events.add(robot, msg)
            except ValueError as err:
                log.warning("%s: %s", robot, err)
                return
            log.info("이벤트 #%s %s %s", e["id"], robot, e["type"])
            self.hub.push(e)
            if msg["type"] == "heat":
                with self.lock:
                    self.apply(self.heat.on_heat(robot, msg))

    def tick(self):
        """1초마다: 연결 끊긴 로봇 알리기, 교차 확인 시간 끝난 것 해제"""
        for robot in list(self.fleet.robots):
            if self.linked.get(robot) and not self.fleet.online(robot):
                self.linked[robot] = False
                log.warning("%s 연결 끊김 (%d초 넘게 state 없음)", robot, ONLINE_SEC)
                self.hub.push(self.state_msg(robot))
        with self.lock:
            self.apply(self.heat.tick())

    def apply(self, actions):
        """HeatCheck가 돌려준 할 일 실행"""
        for a in actions:
            if a[0] == "cmd":
                self.publish(protocol.topic(a[1], "cmd"), a[2])
            elif a[0] == "mode":
                self.set_mode(a[1])

    # ---------- 화면으로 보낼 것 ----------
    def state_msg(self, robot):
        s = {k: v for k, v in self.fleet.robots[robot].items() if k != "seen"}
        return {"kind": "state", "robot": robot, **s,
                "role": self.fleet.role_of(robot) or s.get("role"),
                "online": self.fleet.online(robot),
                "seen_ago": round(self.clock() - self.fleet.robots[robot]["seen"], 1)}

    def snapshot(self):
        return ([self.state_msg(r) for r in sorted(self.fleet.robots)]
                + [{"kind": "mode", "mode": self.mode}]
                + self.events.open_events())

    def send_all(self, pairs):
        for topic, msg in pairs:
            self.publish(topic, msg)
        return [{"topic": t, "msg": m} for t, m in pairs]

    def set_mode(self, mode):
        if mode not in protocol.MODES:
            raise ValueError("모르는 모드: " + str(mode))
        self.mode = mode
        if mode == "normal":
            self.heat.reset()
        self.publish(protocol.MODE_TOPIC, {"mode": mode})
        self.hub.push({"kind": "mode", "mode": mode})

    def is_admin(self, password):
        if not self.admin_password:
            return True
        return hmac.compare_digest((password or "").encode(), self.admin_password.encode())


def create_app(server):
    @asynccontextmanager
    async def lifespan(app):
        server.hub.start(asyncio.get_running_loop())
        pump = asyncio.create_task(server.hub.pump())

        async def ticker():
            while True:
                await asyncio.sleep(1)
                server.tick()
        tick = asyncio.create_task(ticker())
        server.connect()
        yield
        pump.cancel()
        tick.cancel()
        server.close()

    app = FastAPI(title="potent 관제 서버", lifespan=lifespan)

    def admin(x_admin_password: str = Header(None)):
        if not server.is_admin(x_admin_password):
            raise HTTPException(401, "관리자 비밀번호가 틀림")
    lock = [Depends(admin)]

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        for msg in server.snapshot():
            await sock.send_json(msg)
        server.hub.clients.add(sock)
        try:
            while True:
                await sock.receive_text()      # 화면 → 서버는 HTTP로. 여기선 끊김만 본다
        except WebSocketDisconnect:
            pass
        finally:
            server.hub.clients.discard(sock)

    @app.post("/photos")
    async def upload_photo(file: UploadFile = File(...), robot: str = Form("")):
        name = Path(file.filename or "").name
        if not PHOTO_NAME.match(name):
            name = f"{robot or 'X'}_{time.strftime('%m%d_%H%M%S')}.jpg"
        server.photo_dir.mkdir(parents=True, exist_ok=True)
        dst, i = server.photo_dir / name, 2
        while dst.exists():
            dst, i = server.photo_dir / f"{Path(name).stem}_{i}{Path(name).suffix}", i + 1
        dst.write_bytes(await file.read())
        return {"url": "/photos/" + dst.name}

    @app.get("/photos/{name}")
    def get_photo(name: str):
        p = server.photo_dir / name
        if not PHOTO_NAME.match(name) or not p.is_file():
            raise HTTPException(404, "사진 없음")
        return FileResponse(p)

    @app.post("/events/{event_id}/check", dependencies=lock)
    def check_event(event_id: int, body: dict = None):
        e = server.events.check(event_id, false_alarm=bool((body or {}).get("false_alarm")))
        if not e:
            raise HTTPException(404, "이벤트 없음")
        server.hub.push(e)
        return e

    @app.get("/events")
    def list_events(limit: int = 50, before: int = None, type: str = None):
        return server.events.history(limit=max(1, min(limit, 500)), before=before, type=type)

    @app.get("/admin/check", dependencies=lock)
    def admin_check():
        return {"ok": True}

    @app.post("/fleet/start", dependencies=lock)
    def fleet_start():
        return {"sent": server.send_all(server.fleet.auto_assign())}

    @app.post("/robots/{robot}/patrol", dependencies=lock)
    def robot_patrol(robot: str, role: str):
        try:
            return {"sent": server.send_all(server.fleet.assign(robot, role))}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/robots/{robot}/cmd", dependencies=lock)
    def robot_cmd(robot: str, body: dict):
        try:
            msg = protocol.command(body.pop("cmd", ""), **body)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        return {"sent": server.send_all([(protocol.topic(robot, "cmd"), msg)])}

    @app.post("/mode", dependencies=lock)
    def set_mode(body: dict):
        try:
            server.set_mode(body.get("mode"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"mode": server.mode}

    @app.get("/status")
    def status():
        return {"mode": server.mode, "roles": server.fleet.roles,
                "robots": [server.state_msg(r) for r in sorted(server.fleet.robots)],
                "open_events": server.events.open_events()}

    if (DASHBOARD_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
    return app


def selfcheck():
    import tempfile

    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        now = [time.time()]
        srv = Server(photo_dir=Path(tmp) / "photos", mqtt=False, clock=lambda: now[0])
        with TestClient(create_app(srv)) as c:
            # 로봇 두 대가 state를 보내면 대시보드 WebSocket에 붙자마자 보인다
            srv.on_mqtt("potent/A/state", protocol.pack({"marker": 15, "status": "idle", "battery": 90}))
            srv.on_mqtt("potent/B/state", protocol.pack({"marker": 15, "status": "idle", "battery": 70}))
            srv.on_mqtt("potent/B/state", b"not json")                  # 깨진 건 버림
            with c.websocket_connect("/ws") as ws:
                first = [ws.receive_json() for _ in range(3)]
                assert [m["kind"] for m in first] == ["state", "state", "mode"], first
                # 출발: 배터리 많은 A가 A 역할, B가 B 역할
                r = c.post("/fleet/start").json()["sent"]
                assert [(s["topic"], s["msg"]["role"]) for s in r] == [("potent/A/cmd", "A"), ("potent/B/cmd", "B")]
                # 사진 올리고 이벤트가 오면 화면에 번호와 함께 뜬다
                url = c.post("/photos", files={"file": ("B_1117_103512.jpg", b"jpgdata", "image/jpeg")},
                             data={"robot": "B"}).json()["url"]
                assert url == "/photos/B_1117_103512.jpg" and c.get(url).content == b"jpgdata"
                assert c.get("/photos/..%2Fapi.py").status_code == 404
                srv.on_mqtt("potent/B/event", protocol.pack(protocol.event("obstacle", marker=14, sonar_cm=12, photo=url)))
                e = ws.receive_json()
                assert e["kind"] == "event" and e["robot"] == "B" and e["photo"] == url and not e["checked"]
                # 체크로 해제 (body 없으면 정탐), 오탐으로 닫기
                r = c.post(f"/events/{e['id']}/check").json()
                assert r["checked"] is True and r["false_alarm"] is False
                assert ws.receive_json()["checked"] is True
                assert c.post("/events/999/check").status_code == 404
                srv.on_mqtt("potent/A/event", protocol.pack(protocol.event("helmet", marker=3, conf=0.6)))
                e2 = ws.receive_json()
                assert c.post(f"/events/{e2['id']}/check", json={"false_alarm": True}).json()["false_alarm"] is True
                ws.receive_json()
                # 지난 경보 목록: 닫힌 것까지 최근 것부터
                h = c.get("/events").json()
                assert [x["id"] for x in h] == [e2["id"], e["id"]] and h[0]["false_alarm"] is True
                assert [x["id"] for x in c.get(f"/events?before={e2['id']}").json()] == [e["id"]]
                assert [x["type"] for x in c.get("/events?type=helmet").json()] == ["helmet"]
                # 대피 모드
                assert c.post("/mode", json={"mode": "evacuate"}).json() == {"mode": "evacuate"}
                assert ws.receive_json() == {"kind": "mode", "mode": "evacuate"}
                assert c.post("/mode", json={"mode": "panic"}).status_code == 400
                # 명령 직접 보내기
                assert c.post("/robots/A/cmd", json={"cmd": "guide", "arrow": "left"}).status_code == 200
                assert c.post("/robots/A/cmd", json={"cmd": "fly"}).status_code == 400
            assert srv.sent[-1] == ("potent/A/cmd", {"cmd": "guide", "arrow": "left"})
            # 과열 교차 확인: normal로 돌리고, A가 의심 → B 출동 → B도 확인 → 대피
            c.post("/mode", json={"mode": "normal"})
            srv.on_mqtt("potent/A/state", protocol.pack({"marker": 7, "status": "heat_check", "battery": 88}))
            srv.on_mqtt("potent/B/state", protocol.pack({"marker": 3, "status": "patrol", "battery": 70}))
            srv.on_mqtt("potent/A/event", protocol.pack(protocol.event("heat", marker=7, temp_c=52, flame=True)))
            assert srv.sent[-2:] == [("potent/A/cmd", {"cmd": "wait"}), ("potent/B/cmd", {"cmd": "go", "zone": 7})]
            srv.on_mqtt("potent/B/event", protocol.pack(protocol.event("heat", marker=7, temp_c=51, flame=False)))
            assert srv.mode == "evacuate" and srv.sent[-1] == ("potent/mode", {"mode": "evacuate"})
            # 연결 끊김: A만 계속 보내고 B는 조용 → ONLINE_SEC 넘으면 화면에 B online false 한 번
            with c.websocket_connect("/ws") as ws:
                n = len(srv.snapshot())
                for _ in range(n):
                    ws.receive_json()
                now[0] += ONLINE_SEC + 1
                srv.on_mqtt("potent/A/state", protocol.pack({"marker": 7, "status": "idle", "battery": 88}))
                assert ws.receive_json()["robot"] == "A"
                srv.tick()
                m = ws.receive_json()
                assert m["robot"] == "B" and m["online"] is False and m["seen_ago"] > ONLINE_SEC, m
                srv.tick()                                   # 한 번만 알림
                srv.on_mqtt("potent/B/state", protocol.pack({"marker": 3, "status": "patrol", "battery": 69}))
                m = ws.receive_json()
                assert m["robot"] == "B" and m["online"] is True
            assert ("potent/mode", {"mode": "evacuate"}) in srv.sent
            assert c.get("/status").json()["roles"] == {"A": "A", "B": "B"}
        # 관리자 잠금: 비밀번호를 정하면 경보 닫기·출발·명령·모드는 맞는 헤더가 있어야 함
        srv = Server(photo_dir=Path(tmp) / "photos", mqtt=False, admin_password="pw1234")
        with TestClient(create_app(srv)) as c:
            ok = {"X-Admin-Password": "pw1234"}
            assert c.get("/admin/check").status_code == 401
            assert c.get("/admin/check", headers={"X-Admin-Password": "nope"}).status_code == 401
            assert c.get("/admin/check", headers=ok).status_code == 200
            assert c.post("/mode", json={"mode": "evacuate"}).status_code == 401 and srv.mode == "normal"
            assert c.post("/mode", json={"mode": "evacuate"}, headers=ok).status_code == 200
            assert c.post("/robots/A/cmd", json={"cmd": "stop"}).status_code == 401
            assert c.post("/fleet/start").status_code == 401
            srv.on_mqtt("potent/A/event", protocol.pack(protocol.event("helmet", marker=3, conf=0.6)))
            assert c.post("/events/1/check").status_code == 401
            assert c.post("/events/1/check", headers=ok).status_code == 200
            assert c.get("/events").status_code == 200 and c.get("/status").status_code == 200   # 보기는 잠그지 않음
    print("api ok")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="관제 서버")
    p.add_argument("--selfcheck", action="store_true")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    if a.selfcheck:
        selfcheck()
    else:
        import uvicorn
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
        srv = Server(db_path=DB_PATH, host=os.getenv("MQTT_HOST", "localhost"),
                     port=int(os.getenv("MQTT_PORT", "1883")), admin_password=os.getenv("ADMIN_PASSWORD"))
        if not srv.admin_password:
            log.warning("ADMIN_PASSWORD가 없어 관리자 요청을 잠그지 않음 (개발용)")
        uvicorn.run(create_app(srv), host="0.0.0.0", port=a.port)
