"""파트끼리 주고받는 약속 (보드 ↔ Pi 시리얼, Pi ↔ 서버 MQTT).

여기 있는 이름을 바꾸면 네 파트가 모두 영향을 받는다. 바꿀 때는 docs/protocol.md를 함께 고치고 팀에 알린다.
셀프 체크: python -m common.protocol
"""
import json
import time

# ---------- 보드 ↔ Pi 5 (USB 시리얼, CMD|…|$) ----------


def dog_cmd(name, *args):
    """Pi → 보드 명령 한 줄. dog_cmd("F", 50, 25, 1000) → b"CMD|F|50|25|1000|$\\n" """
    return ("CMD|" + "|".join(str(p) for p in (name, *args)) + "|$\n").encode()


def parse_dog(line):
    """보드 → Pi 한 줄을 ["D", t, 값…] 같은 목록으로. 형식이 아니면 None (시리얼 잡음은 버린다)"""
    s = line.decode(errors="replace") if isinstance(line, bytes) else line
    s = s.strip()
    if not (s.startswith("CMD|") and s.endswith("|$")):
        return None
    return s[4:-2].split("|")


# ---------- Pi 5 ↔ 서버 (MQTT) ----------

ROOT = "potent"
KINDS = ("state", "event", "cmd")
MODE_TOPIC = ROOT + "/mode"

EVENT_TYPES = ("obstacle", "floor_object", "helmet", "heat", "flame")
COMMANDS = ("patrol", "stop", "resume", "go", "wait", "dock", "guide")
ROLES = ("A", "B")   # 순찰 동선(역할). 서버가 patrol 명령으로 정해 준다. 로봇 이름(ROBOT_ID)과는 별개
MODES = ("normal", "evacuate")


def topic(robot, kind):
    """topic("B", "event") → "potent/B/event" """
    if kind not in KINDS:
        raise ValueError("모르는 토픽 종류: " + kind)
    return f"{ROOT}/{robot}/{kind}"


def parse_topic(t):
    """"potent/B/event" → ("B", "event"). 이 시스템 토픽이 아니면 None"""
    parts = t.split("/")
    if len(parts) == 3 and parts[0] == ROOT and parts[2] in KINDS:
        return parts[1], parts[2]
    return None


def event(type, **fields):
    """이벤트 메시지. event("obstacle", marker=14, sonar_cm=12, photo="/photos/…")"""
    if type not in EVENT_TYPES:
        raise ValueError("모르는 이벤트: " + type)
    return {"type": type, "ts": int(time.time()), **fields}


def command(name, **fields):
    """서버 → Pi 명령 메시지. command("go", zone=7), command("patrol", role="A")"""
    if name not in COMMANDS:
        raise ValueError("모르는 명령: " + name)
    if name == "patrol" and "role" in fields and fields["role"] not in ROLES:
        raise ValueError("모르는 역할: " + str(fields["role"]))
    return {"cmd": name, **fields}


def pack(msg):
    return json.dumps(msg, ensure_ascii=False).encode()


def unpack(payload):
    """받은 MQTT 내용을 dict로. 깨진 메시지는 ValueError (받는 쪽에서 버리고 기록)"""
    msg = json.loads(payload)
    if not isinstance(msg, dict):
        raise ValueError("메시지는 JSON 객체여야 함")
    return msg


if __name__ == "__main__":
    assert dog_cmd("F", 50, 25, 1000) == b"CMD|F|50|25|1000|$\n"
    assert dog_cmd("S") == b"CMD|S|$\n"
    assert parse_dog(b"CMD|E|1200|stop|13.0|$\r\n") == ["E", "1200", "stop", "13.0"]
    assert parse_dog("garbage") is None
    assert topic("B", "event") == "potent/B/event"
    assert parse_topic("potent/B/event") == ("B", "event")
    assert parse_topic("potent/mode") is None and parse_topic("other/B/event") is None
    e = event("obstacle", marker=14, sonar_cm=12)
    assert e["type"] == "obstacle" and isinstance(e["ts"], int)
    assert unpack(pack(e)) == e
    assert command("go", zone=7) == {"cmd": "go", "zone": 7}
    assert command("patrol", role="B") == {"cmd": "patrol", "role": "B"}
    for bad in (lambda: event("smoke"), lambda: command("fly"), lambda: command("patrol", role="C"), lambda: topic("B", "x"), lambda: unpack(b"[1]")):
        try:
            bad()
            raise AssertionError("ValueError가 나야 함")
        except ValueError:
            pass
    print("protocol ok")
