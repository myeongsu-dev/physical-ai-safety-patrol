"""관제 서버 실행 진입점 (FastAPI). 실행: python -m server.api  → http://localhost:8000
셀프 체크(브로커 없이): python -m server.api --selfcheck

MQTT(potent/+/state · event)를 받아 fleet·events에 반영하고 대시보드 WebSocket /ws로 밀어 준다.

| 경로 | 하는 일 |
|---|---|
| WebSocket /ws | 붙자마자 지금 상태(state 전부·mode·해제 안 된 event) → 이후 새 메시지 |
| POST /photos | Pi 사진 받기 (form: file, robot) → {"url": "/photos/이름"} |
| GET /photos/{이름} | 사진 |
| POST /events/{id}/check | 관리자가 확인하고 해제 |
| POST /fleet/start | 비어 있는 역할(A·B)을 대기 로봇에게 나눠 주며 출발 |
| POST /robots/{로봇}/patrol?role=A | 역할을 직접 정해 출발 |
| POST /robots/{로봇}/cmd | 명령 직접 보내기 (body: {"cmd": "stop"} 등) |
| POST /mode | {"mode": "normal" · "evacuate"} |
| GET /status | 지금 상태 (확인용) |
| / | dashboard/ 폴더 (index.html이 생기면) |
"""
import argparse
import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from common import protocol
from server.events import EventLog
from server.fleet import Fleet

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

    def __init__(self, db_path=":memory:", photo_dir=PHOTO_DIR, mqtt=True, host="localhost", port=1883):
        self.fleet = Fleet()
        self.events = EventLog(str(db_path))
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
            self.hub.push(self.state_msg(robot))
        elif kind == "event":
            try:
                e = self.events.add(robot, msg)
            except ValueError as err:
                log.warning("%s: %s", robot, err)
                return
            log.info("이벤트 #%s %s %s", e["id"], robot, e["type"])
            self.hub.push(e)

    # ---------- 화면으로 보낼 것 ----------
    def state_msg(self, robot):
        s = {k: v for k, v in self.fleet.robots[robot].items() if k != "seen"}
        return {"kind": "state", "robot": robot, **s,
                "role": self.fleet.role_of(robot) or s.get("role"),
                "online": self.fleet.online(robot)}

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
        self.publish(protocol.MODE_TOPIC, {"mode": mode})
        self.hub.push({"kind": "mode", "mode": mode})


def create_app(server):
    @asynccontextmanager
    async def lifespan(app):
        server.hub.start(asyncio.get_running_loop())
        pump = asyncio.create_task(server.hub.pump())
        server.connect()
        yield
        pump.cancel()
        server.close()

    app = FastAPI(title="potent 관제 서버", lifespan=lifespan)

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

    @app.post("/events/{event_id}/check")
    def check_event(event_id: int):
        e = server.events.check(event_id)
        if not e:
            raise HTTPException(404, "이벤트 없음")
        server.hub.push(e)
        return e

    @app.post("/fleet/start")
    def fleet_start():
        return {"sent": server.send_all(server.fleet.auto_assign())}

    @app.post("/robots/{robot}/patrol")
    def robot_patrol(robot: str, role: str):
        try:
            return {"sent": server.send_all(server.fleet.assign(robot, role))}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/robots/{robot}/cmd")
    def robot_cmd(robot: str, body: dict):
        try:
            msg = protocol.command(body.pop("cmd", ""), **body)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        return {"sent": server.send_all([(protocol.topic(robot, "cmd"), msg)])}

    @app.post("/mode")
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
        srv = Server(photo_dir=Path(tmp) / "photos", mqtt=False)
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
                # 체크로 해제
                assert c.post(f"/events/{e['id']}/check").json()["checked"] is True
                assert ws.receive_json()["checked"] is True
                assert c.post("/events/999/check").status_code == 404
                # 대피 모드
                assert c.post("/mode", json={"mode": "evacuate"}).json() == {"mode": "evacuate"}
                assert ws.receive_json() == {"kind": "mode", "mode": "evacuate"}
                assert c.post("/mode", json={"mode": "panic"}).status_code == 400
                # 명령 직접 보내기
                assert c.post("/robots/A/cmd", json={"cmd": "guide", "arrow": "left"}).status_code == 200
                assert c.post("/robots/A/cmd", json={"cmd": "fly"}).status_code == 400
            assert srv.sent[-1] == ("potent/A/cmd", {"cmd": "guide", "arrow": "left"})
            assert ("potent/mode", {"mode": "evacuate"}) in srv.sent
            assert c.get("/status").json()["roles"] == {"A": "A", "B": "B"}
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
                     port=int(os.getenv("MQTT_PORT", "1883")))
        uvicorn.run(create_app(srv), host="0.0.0.0", port=a.port)
