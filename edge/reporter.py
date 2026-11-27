"""서버 보고: MQTT(state·event 보내기, cmd·mode 받기) + 사진 POST /photos

- state는 1초마다 그때그때 (끊겨 있으면 버림, 다음 초에 새로 감)
- event는 보낼 통(outbox)에 넣고 파일(SPOOL_DIR/outbox.jsonl)에도 적음.
  서버가 끊겨 있으면 쌓아 두었다가 다시 붙으면 차례대로 보냄 (Pi가 다시 켜져도 남아 있음)
- 사진은 먼저 SPOOL_DIR/photos/에 저장 → 보낼 차례에 POST /photos → 받은 주소를 event의 photo에
"""
import json
import logging
import queue
import threading
import time
from pathlib import Path

from common import protocol
from edge import config

log = logging.getLogger("edge.report")

PHOTO_RETRIES = 3  # 사진만 계속 실패하면 사진 없이라도 이벤트를 보냄


def photo_name(robot, when=None, taken=()):
    """B_1117_103512.jpg (로봇_월일_시분초). 같은 초에 또 찍으면 _2, _3"""
    base = f"{robot}_{time.strftime('%m%d_%H%M%S', time.localtime(when))}"
    name, i = base + ".jpg", 2
    while name in taken:
        name, i = f"{base}_{i}.jpg", i + 1
    return name


def photo_url_from(resp_text, name):
    """POST /photos 답에서 주소 꺼내기. {"url"|"photo"|"path": …} 또는 글자, 모르면 /photos/이름"""
    try:
        data = json.loads(resp_text)
        if isinstance(data, dict):
            for k in ("url", "photo", "path"):
                if isinstance(data.get(k), str):
                    return data[k]
        if isinstance(data, str):
            return data
    except ValueError:
        t = resp_text.strip()
        if t.startswith("/") or t.startswith("http"):
            return t
    return "/photos/" + name


class Reporter:
    def __init__(self, robot=config.ROBOT_ID, host=config.MQTT_HOST, port=config.MQTT_PORT,
                 server_url=config.SERVER_URL, spool_dir=config.SPOOL_DIR):
        self.robot, self.host, self.port, self.server_url = robot, host, port, server_url
        self.spool = Path(spool_dir)
        self.photo_dir = self.spool / "photos"
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_file = self.spool / "outbox.jsonl"
        self.inbox = queue.Queue()  # 서버에서 온 것: ("cmd", {...}) · ("mode", {...}) · ("alive", {...})
        self.client = None
        self.connected = False
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self.outbox = self._load()
        if self.outbox:
            log.info("지난번에 못 보낸 이벤트 %d개", len(self.outbox))

    # ---------- 보낼 통 ----------
    def _load(self):
        if not self.outbox_file.exists():
            return []
        out = []
        for line in self.outbox_file.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def _save(self):
        tmp = self.outbox_file.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in self.outbox), encoding="utf-8")
        tmp.replace(self.outbox_file)

    def report_event(self, type, jpeg=None, **fields):
        """이벤트 하나를 보낼 통에. jpeg가 있으면 사진도 함께. 돌려주는 값은 사진 파일 이름"""
        msg = protocol.event(type, **fields)
        name = None
        if jpeg:
            name = photo_name(self.robot, msg["ts"], {p.name for p in self.photo_dir.iterdir()})
            (self.photo_dir / name).write_bytes(jpeg)
            msg["_photo_file"] = name
            msg.setdefault("photo", None)
        with self._lock:
            self.outbox.append(msg)
            self._save()
        log.info("이벤트 %s %s", type, {k: v for k, v in fields.items()})
        return name

    # ---------- MQTT ----------
    def start(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt 없음 → 서버 보고 안 함 (이벤트는 쌓아 둠)")
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"potent-edge-{self.robot}", clean_session=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 10)
        c.connect_async(self.host, self.port, keepalive=10)
        c.loop_start()
        self.client = c
        threading.Thread(target=self._flush_loop, name="outbox", daemon=True).start()
        log.info("MQTT %s:%d 연결 시도", self.host, self.port)

    def stop(self):
        self._closing.set()
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT 연결 거부: %s", reason_code)
            return
        self.connected = True
        # 다시 붙을 때마다 구독. clean session이라 끊긴 동안의 명령은 브로커가 쌓아 두지 않음 (옛 이동 명령이 늦게 오지 않게)
        client.subscribe(protocol.topic(self.robot, "cmd"), qos=1)
        client.subscribe(protocol.MODE_TOPIC, qos=1)
        client.subscribe(protocol.ALIVE_TOPIC, qos=0)
        log.info("MQTT 연결됨 %s:%d (구독 %s)", self.host, self.port, protocol.topic(self.robot, "cmd"))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected:
            log.warning("MQTT 끊김: %s", reason_code)
        self.connected = False

    def _on_message(self, client, userdata, m):
        try:
            msg = protocol.unpack(m.payload)
        except ValueError as e:
            log.warning("깨진 메시지 버림 %s: %s", m.topic, e)
            return
        if m.topic == protocol.MODE_TOPIC:
            self.inbox.put(("mode", msg))
        elif m.topic == protocol.ALIVE_TOPIC:
            if getattr(m, "retain", False):  # 보관된 신호는 서버가 지금 살아 있다는 뜻이 아님
                return
            self.inbox.put(("alive", msg))
        elif protocol.parse_topic(m.topic) == (self.robot, "cmd"):
            if getattr(m, "retain", False):  # 브로커에 남아 있던 옛 명령 (다시 붙을 때마다 와서 지난 patrol이 다시 실행됨)
                log.warning("브로커에 보관된(retained) 명령 버림: %s", msg)
                return
            self.inbox.put(("cmd", msg))
        else:
            log.debug("이 로봇 것이 아닌 메시지 버림 %s", m.topic)

    def publish_state(self, state):
        if self.client and self.connected:
            self.client.publish(protocol.topic(self.robot, "state"), protocol.pack(state), qos=0)

    # ---------- 쌓인 이벤트 보내기 ----------
    def _flush_loop(self):
        while not self._closing.is_set():
            if not (self.connected and self.outbox):
                time.sleep(0.3)
                continue
            if not self.flush_one():
                time.sleep(1)

    def flush_one(self, publish=None, upload=None):
        """맨 앞 이벤트 하나 보내기. 성공하면 True (publish·upload는 시험용으로 바꿔 끼움)"""
        publish = publish or self._publish
        upload = upload or self._upload
        with self._lock:
            if not self.outbox:
                return True
            msg = self.outbox[0]
        name = msg.get("_photo_file")
        if name and not msg.get("photo"):
            url = upload(name)
            if url:
                msg["photo"] = url
            else:
                msg["_tries"] = msg.get("_tries", 0) + 1
                if msg["_tries"] < PHOTO_RETRIES:
                    return False
                log.warning("사진 올리기 계속 실패 → 사진 없이 보냄 (%s는 Pi에 남음)", name)
        out = {k: v for k, v in msg.items() if not k.startswith("_")}
        if not publish(protocol.topic(self.robot, "event"), protocol.pack(out)):
            return False
        with self._lock:
            if self.outbox and self.outbox[0] is msg:
                self.outbox.pop(0)
                self._save()
        return True

    def _publish(self, topic, payload):
        info = self.client.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(timeout=3)
        except Exception:
            return False
        return info.is_published()

    def _upload(self, name):
        import requests

        try:
            data = (self.photo_dir / name).read_bytes()
            r = requests.post(self.server_url + "/photos", files={"file": (name, data, "image/jpeg")},
                              data={"robot": self.robot}, timeout=5)
            r.raise_for_status()
            return photo_url_from(r.text, name)
        except Exception as e:
            log.warning("사진 올리기 실패 %s: %s", name, e)
            return None
