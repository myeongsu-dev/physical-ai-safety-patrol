"""장치 하나씩 확인 (Pi에서 처음 꽂았을 때, 통합 전에)

  python -m edge.hwcheck ports                 # 시리얼 포트 목록 + Linux 고정 이름(/dev/serial/by-path·by-id)
  python -m edge.hwcheck dog [--secs 5]        # 보드 V 응답·지원 명령, T 측정값(bat·sonar·line), S ok, 오류 답(!) 읽기
  python -m edge.hwcheck line [--secs 20]      # 걷지 않고 선 위치만: 테이프를 좌우로 옮기며 left·straight·right 확인
  python -m edge.hwcheck drive [--stride 30 --secs 3]   # 서버 없이 Pi에서 F → 짧게 선 따라 걷고 S (로봇이 움직임!)
  python -m edge.hwcheck flow                  # 실제 브로커 + 가짜 보드: 서버처럼 patrol·stop·resume을 보내 F·S·F와 state 확인
  python -m edge.hwcheck cam [--secs 5]        # 초당 장 수 (목표 15), 마지막 장을 cam_check.jpg로
  python -m edge.hwcheck thermo                # GY-906 표면·주변 온도 10번
  python -m edge.hwcheck detect [--image a.jpg] [--n 30]   # 통합 모델 추론 속도 (목표 초당 10장)
  python -m edge.hwcheck mqtt                  # 브로커에 state 한 번 보내고 cmd·mode 10초 듣기
"""
import argparse
import os
import sys
import time

from edge import config

USB_NAMES = {(0x1A86, 0x7523): "CH340 (MechDog 보드 또는 바닥 비전 모듈)",
             (0x303A, 0x1001): "ESP32-S3 내장 USB (XIAO 머리 카메라)"}


def c_ports(a):
    from serial.tools import list_ports

    for p in list_ports.comports():
        guess = USB_NAMES.get((p.vid, p.pid), "")
        print(f"{p.device:16} {p.vid or '':>6} {p.pid or '':>6} {p.location or '':>10}  {p.description}  {guess}")
    for d in ("/dev/serial/by-path", "/dev/serial/by-id"):  # Linux: 꽂은 USB 자리(by-path)는 같은 CH340 두 개도 안 헷갈림
        if os.path.isdir(d):
            print(f"--- {d}")
            for n in sorted(os.listdir(d)):
                print(f"{os.path.join(d, n)} -> {os.path.realpath(os.path.join(d, n))}")
    print("MechDog 보드와 바닥 비전 모듈은 같은 CH340이라 하나씩 뽑아 보며 구분. DOG_PORT는 by-path 이름을 권장")


def _open_dog(a):
    from edge.dog_link import DogLink

    link = DogLink(a.port or config.DOG_PORT)
    link.open()   # DTR·RTS 처리는 DogLink 그대로 (보드가 다시 켜지지 않게)
    time.sleep(0.5)
    if not link.connected:
        print(f"FAIL 보드 포트를 못 엶: {link.port} (ports로 이름 확인, Linux는 dialout 그룹)")
        link.close()
        return None
    return link


def _read(link, st, secs, on=None, until=None):
    """secs 동안 보드 줄을 읽어 st에 반영. until(parts)가 참이면 그 줄을 돌려주고 끝냄"""
    end = time.monotonic() + secs
    while time.monotonic() < end:
        try:
            parts = link.inbox.get(timeout=0.1)
        except Exception:
            continue
        st.update(parts)
        if on:
            on(parts)
        if until and until(parts):
            return parts
    return None


def _answer(name):
    """보드 답 고르기: CMD|name|ok|$ 또는 CMD|!|name|이유|$"""
    return lambda p: (p[0] == name and p[1:2] == ["ok"]) or (p[0] == "!" and p[1:2] == [name])


def _verdict(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    return bool(ok)


def c_dog(a):
    """보드 통신 확인. 포트가 열리는 것만으로는 성공이 아님: V 답, 명령별 ok, 오류 답(!)을 나눠 봄. 걷지 않음"""
    from edge.dog_link import BoardState, line_side

    link = _open_dog(a)
    if not link:
        return 1
    st = BoardState(config.STREAM_CHANS)
    n = {"D": 0}

    def show(parts):
        n["D"] += parts[0] == "D"
        if parts[0] != "D" or n["D"] % 5 == 1:   # 측정값은 5줄에 한 줄만
            print("  <", "|".join(parts))

    ok = True
    link.send("V")
    v = _read(link, st, 2.0, show, lambda p: p[0] == "V")
    ok &= _verdict(v is not None, f"V 응답: 버전 {st.version}, 명령 {st.commands}, 숫자 채널 {st.board_chans}, "
                                  f"이벤트 {sorted(st.event_chans)}")
    if v and st.commands is not None:
        ok &= _verdict("F" in st.commands and "S" in st.commands, "F·S 지원")
        ok &= _verdict("line" in (st.board_chans or []), "line 채널 (보드가 바닥 비전 모듈을 찾음)")
    link.send("T", config.STREAM_MS, config.STREAM_CHANS)
    t = _read(link, st, 1.5, show, _answer("T"))
    ok &= _verdict(t is not None and t[0] == "T", f"T ok ({config.STREAM_MS}ms {config.STREAM_CHANS})")
    _read(link, st, a.secs, show)
    ok &= _verdict(n["D"] >= a.secs * 1000 / config.STREAM_MS * 0.5,
                   f"측정값 D {n['D']}줄 / {a.secs:.0f}초 (배터리 {st.battery}%, 초음파 {st.sonar_cm}cm, "
                   f"선 {st.line} {line_side(st.line)})")
    link.send("S")
    s = _read(link, st, 1.5, show, _answer("S"))
    ok &= _verdict(s is not None and s[0] == "S", "S ok (정지 명령을 받아들임)")
    link.send("Z")   # 없는 명령 → 보드가 CMD|!|Z|이유|$ 로 답해야 함 (움직이지 않는 명령)
    e = _read(link, st, 1.5, show, lambda p: p[0] == "!")
    ok &= _verdict(e is not None and st.last_error and st.last_error[0] == "Z", f"오류 답 읽기: {st.last_error}")
    link.send("T", 0)
    link.close()
    print("확인: 포트를 열 때 보드가 다시 켜지지 않았는지(자세가 풀리지 않음). 배터리 % 단위는 보드 담당과 확인")
    return 0 if ok else 1


def c_line(a):
    """걷지 않고 선 위치만. 보드 F와 같은 기준(60·100)으로 어느 쪽으로 꺾을지 보여 줌"""
    from edge.dog_link import BoardState, line_side

    link = _open_dog(a)
    if not link:
        return 1
    st = BoardState("line")
    link.send("T", 100, "line")
    seen = {"left": 0, "straight": 0, "right": 0, "lost": 0}
    last = [0.0]

    def show(parts):
        if parts[0] != "D":
            return
        side = line_side(st.line)
        seen[side] += 1
        if time.monotonic() - last[0] > 0.3:
            last[0] = time.monotonic()
            x = st.line if st.line is not None else -1
            bar = ["."] * 32
            if x >= 0:
                bar[min(31, int(x / 160 * 32))] = "|"
            print(f"  line {x:6.1f} [{''.join(bar)}] {side}")

    print(f"{a.secs:.0f}초 동안 테이프를 바닥 카메라 아래에서 왼쪽·가운데·오른쪽으로 옮겨 보세요 (Ctrl-C로 끝)")
    try:
        _read(link, st, a.secs, show)
    except KeyboardInterrupt:
        pass
    link.send("T", 0)
    link.close()
    print("--- 받은 횟수", seen)
    if sum(seen.values()) == 0:
        _verdict(False, "line 값을 못 받음 (보드 main.py·비전 모듈 patrol_cam 확인)")
        return 1
    ok = _verdict(seen["left"] and seen["straight"] and seen["right"],
                  "left·straight·right가 모두 나옴 (테이프 위치와 맞는지 눈으로 확인)")
    return 0 if ok else 1


def c_drive(a):
    """서버 없이 Pi에서 짧게 선 따라 걷기. Pi 보조 정지(선 유실·초음파 14cm·측정값 끊김)도 같이 시험"""
    from edge.dog_link import BoardState, line_side

    link = _open_dog(a)
    if not link:
        return 1
    st = BoardState("bat,sonar,line")
    link.send("V")
    link.send("T", config.STREAM_MS, "bat,sonar,line")
    _read(link, st, 1.0)
    if not _verdict(st.version is not None, f"V 응답 (버전 {st.version})"):
        link.close()
        return 1
    if not _verdict(a.no_line_check or (st.line is not None and st.line >= 0), f"출발 전 선 보임 (line={st.line})"):
        print("바닥 카메라를 검은 선 위에 놓고 다시 (선 없이 시험하려면 --no-line-check)")
        link.send("T", 0)
        link.close()
        return 1
    ms = int(min(a.secs * 1000, 15000))
    print(f"F|{a.stride}|{a.angle}|{ms} 를 보냅니다. 로봇 앞 1m를 비우고 손으로 잡을 준비. (보폭은 속도 %가 아님)")
    if not a.yes:
        try:
            input("준비되면 Enter (취소는 Ctrl-C) ")
        except KeyboardInterrupt:
            link.send("T", 0)
            link.close()
            return 1
    reason, ok = "시간 끝", True
    try:
        t0 = time.monotonic()
        link.send("F", a.stride, a.angle, ms)
        f = _read(link, st, config.ACK_TIMEOUT_S, until=_answer("F"))
        ok = _verdict(f is not None and f[0] == "F", f"F ok ({'답 없음' if f is None else '|'.join(f)})")
        if not ok:
            reason = "F 실패"
        near, lost_since, last_d, shown = 0, None, time.monotonic(), 0.0
        while ok and time.monotonic() - t0 < a.secs:
            p = _read(link, st, 0.1, until=lambda p: p[0] in ("D", "E", "!"))
            now = time.monotonic()
            if p and p[0] == "D":
                last_d = now
                d = st.sonar_cm
                near = near + 1 if d is not None and d < config.SONAR_STOP_CM else 0
                lost_since = None if line_side(st.line) != "lost" else (lost_since or now)
                if now - shown > 0.4:
                    shown = now
                    print(f"  {now - t0:4.1f}s line {st.line} {line_side(st.line):8} sonar {d}")
            elif p and p[0] == "E" and len(p) > 2 and p[2] == "done":
                reason = "보드 이동 시간 끝(done)"
                break
            elif p and p[0] == "!":
                reason, ok = "보드 오류 " + "|".join(p), False
                break
            if near >= 3:
                reason = f"초음파 {config.SONAR_STOP_CM}cm 안 3번"
                break
            if lost_since and now - lost_since >= config.LINE_LOST_S:
                reason = f"선 유실 {config.LINE_LOST_S}초"
                break
            if now - last_d > config.SENSOR_STALE_S:
                reason, ok = "측정값 끊김", False
                break
    except KeyboardInterrupt:
        reason = "Ctrl-C"
    finally:
        link.send("S")
    s = _read(link, st, 1.5, until=_answer("S"))
    ok &= _verdict(s is not None and s[0] == "S", f"S ok (멈춘 이유: {reason})")
    link.send("T", 0)
    link.close()
    print("확인: 선을 따라 꺾었는지, 멈춘 이유가 실제와 맞는지, S 뒤 바로 섰는지")
    return 0 if ok else 1


def c_cam(a):
    from edge.camera import Camera

    cam = Camera(a.port or config.CAM_PORT)
    cam.open()
    time.sleep(1.0)
    n0, t0 = cam.frames, time.monotonic()
    time.sleep(a.secs)
    fps = (cam.frames - n0) / (time.monotonic() - t0)
    f = cam.latest()
    print(f"초당 {fps:.1f}장 (목표 15)")
    if f is not None and f.jpeg:
        open("cam_check.jpg", "wb").write(f.jpeg)
        img = f.image
        print(f"마지막 장 {len(f.jpeg) // 1024}KB {'' if img is None else img.shape} → cam_check.jpg")
    else:
        print("받은 장 없음: CAM_PORT와 XIAO 프로그램(edge/xiao_cam) 확인")
    cam.close()


def c_thermo(a):
    from edge.thermo import Thermo

    th = Thermo()
    th.open()
    for _ in range(10):
        print(f"표면 {th.read()}℃  주변 {th.ambient()}℃")
        time.sleep(0.5)
    print(f"과열 의심 기준 {config.HEAT_SUSPECT_C}℃ 초과. 손바닥·뜨거운 물컵으로 값이 오르는지 보세요")


def c_detect(a):
    from edge.camera import Camera, decode
    from edge.detect import Detector

    det = Detector(a.name, 0.25, imgsz=a.imgsz)
    if not det.ready:
        print("모델 없음: models/{}/버전/ 아래 *_ncnn_model 또는 .pt".format(a.name))
        return
    if a.image:
        img = decode(open(a.image, "rb").read())
        get = lambda: img  # noqa: E731
    else:
        cam = Camera(a.port or config.CAM_PORT)
        cam.open()
        time.sleep(1.5)

        def get():
            f = cam.latest()
            return f.image if f else None
    det(get())  # 첫 장은 준비 시간이라 뺌
    times, dets = [], []
    for _ in range(a.n):
        img = get()
        if img is None:
            time.sleep(0.05)
            continue
        dets = det(img)
        times.append(det.ms)
    if not times:
        print("그림을 못 받음")
        return
    ms = sum(times) / len(times)
    print(f"{det.path}  입력 {a.imgsz}  평균 {ms:.0f}ms → 초당 {1000 / ms:.1f}장 (목표 10)")
    print("마지막 장:", [(d.cls, round(d.conf, 2)) for d in dets])


def c_mqtt(a):
    from common import protocol
    from edge.reporter import Reporter

    r = Reporter()
    r.start()
    for _ in range(20):
        if r.connected:
            break
        time.sleep(0.25)
    print("연결됨" if r.connected else f"연결 안 됨: {config.MQTT_HOST}:{config.MQTT_PORT}")
    r.publish_state({"marker": None, "status": "idle", "battery": None, "sonar_cm": None,
                     "temp_c": None, "version": "hwcheck"})
    topic = protocol.topic(config.ROBOT_ID, "cmd")
    print(f"{protocol.topic(config.ROBOT_ID, 'state')} 보냄. {a.secs:.0f}초 동안 {topic}·{protocol.MODE_TOPIC} 기다림")
    end = time.monotonic() + a.secs
    while time.monotonic() < end:
        try:
            print("받음:", r.inbox.get(timeout=0.5))
        except Exception:
            pass
    r.stop()
    return 0 if r.connected else 1


def c_flow(a):
    """Test 4: 실제 MQTT 브로커 + 가짜 보드(FakeDog). 이 명령이 서버 역할로 cmd를 보내고, 에이전트가 보드에 보낸 것과 state를 확인.
    로봇 이름은 진짜 로봇이 받지 않게 시험용(--robot, 기본 flowtest)을 씀"""
    import json
    import tempfile
    import threading

    import paho.mqtt.client as mqtt

    from common import protocol
    from edge.agent import Agent
    from edge.camera import FakeCamera
    from edge.dog_link import FakeDog
    from edge.reporter import Reporter
    from edge.thermo import FakeThermo

    robot = a.robot
    F = ("F", config.PATROL_STRIDE, config.PATROL_ANGLE, config.MOVE_MS)
    dog = FakeDog()
    ok = True
    with tempfile.TemporaryDirectory() as spool:
        rep = Reporter(robot, spool_dir=spool)
        ag = Agent(dog, FakeCamera(), FakeThermo(25.0), rep, model=None, version="flow")
        ag.start()
        done = threading.Event()
        threading.Thread(target=ag.run, kwargs={"until": done}, daemon=True).start()

        states = []
        srv = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"potent-flowcheck-{os.getpid()}")
        srv.on_message = lambda c, u, m: states.append(json.loads(m.payload))
        srv.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=10)
        srv.subscribe(protocol.topic(robot, "state"), qos=0)
        srv.loop_start()
        try:
            for _ in range(40):
                if rep.connected:
                    break
                time.sleep(0.25)
            if not _verdict(rep.connected, f"에이전트 MQTT 연결 {config.MQTT_HOST}:{config.MQTT_PORT}, "
                                           f"구독 {protocol.topic(robot, 'cmd')}"):
                return 1
            time.sleep(1.5)  # 구독·첫 측정값

            def send(topic, payload):
                print(f"  > {topic} {payload}")
                srv.publish(topic, payload, qos=1).wait_for_publish(3)

            def wait_state(status, secs=4.0):
                end = time.monotonic() + secs
                while time.monotonic() < end:
                    if states and states[-1].get("status") == status:
                        return states[-1]
                    time.sleep(0.1)
                return states[-1] if states else None

            cmd = protocol.topic(robot, "cmd")
            # 버려야 하는 것: 다른 로봇, 깨진 JSON, 모르는 명령, 모르는 역할
            dog.sent.clear()
            send(protocol.topic(robot + "-other", "cmd"), '{"cmd":"patrol","role":"A"}')
            send(cmd, '{"cmd":"patrol"')
            send(cmd, '{"cmd":"fly"}')
            send(cmd, '{"cmd":"patrol","role":"C"}')
            time.sleep(1.5)
            ok &= _verdict(F not in dog.sent and ag.status == "idle",
                           f"다른 로봇·깨진 JSON·모르는 명령·모르는 역할 무시 (보드로 간 것 {dog.sent})")

            send(cmd, json.dumps(protocol.command("patrol", role="A")))
            s = wait_state("patrol")
            ok &= _verdict(F in dog.sent and s and s["status"] == "patrol" and s["role"] == "A",
                           f"patrol(role A) → 보드 {F} ok, state {s}")
            ok &= _verdict(s is not None and {"marker", "status", "role", "battery", "sonar_cm", "temp_c", "version"} <= set(s),
                           "state 항목 marker·status·role·battery·sonar_cm·temp_c·version")

            dog.sent.clear()
            send(cmd, json.dumps(protocol.command("stop")))
            s = wait_state("idle")
            ok &= _verdict(dog.sent[:1] == [("S",)] and s and s["status"] == "idle",
                           f"stop → 보드 {dog.sent[:1]}, state {s and s['status']}")
            dog.inject("E", 1, "done", 1)   # 보드의 이동 끝 알림이 늦게 와도
            time.sleep(a.hold)
            ok &= _verdict(F not in dog.sent and not dog.moving, f"stop 뒤 {a.hold:.0f}초 동안 F를 다시 안 보냄")

            send(cmd, json.dumps(protocol.command("resume")))
            s = wait_state("patrol")
            ok &= _verdict(F in dog.sent and s and s["status"] == "patrol" and s["role"] == "A",
                           f"resume → 보드 F, 역할 유지 state {s}")

            dog.sent.clear()
            dog.disconnect()
            s = wait_state("idle")
            dog.reconnect()
            time.sleep(a.hold)
            ok &= _verdict(s and s["status"] == "idle" and F not in dog.sent and ag.status == "idle",
                           "보드 끊김 → idle, 다시 붙어도 저절로 안 걸음")

            n = len(states)
            time.sleep(3.2)
            ok &= _verdict(3 <= len(states) - n <= 4, f"state 1초마다 ({len(states) - n}개 / 3.2초)")
            send(cmd, json.dumps(protocol.command("stop")))
            wait_state("idle")
        finally:
            done.set()
            time.sleep(0.2)
            ag.stop()
            srv.loop_stop()
            srv.disconnect()
    print("flow " + ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="edge 장치 확인")
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("ports")
    for name, secs in (("dog", 5), ("cam", 5), ("line", 20)):
        p = sub.add_parser(name)
        p.add_argument("--port")
        p.add_argument("--secs", type=float, default=secs)
    p = sub.add_parser("drive", help="로봇이 움직임: 주변을 비우고")
    p.add_argument("--port")
    p.add_argument("--stride", type=int, default=30, help="보폭 (기본 30, 낮게 시작. 순찰 기본은 PATROL_STRIDE)")
    p.add_argument("--angle", type=int, default=config.PATROL_ANGLE)
    p.add_argument("--secs", type=float, default=3.0, help="걷는 시간 (최대 15초)")
    p.add_argument("--no-line-check", action="store_true", help="출발 전 선이 보이는지 확인하지 않음")
    p.add_argument("--yes", action="store_true", help="Enter 확인 없이")
    sub.add_parser("thermo")
    p = sub.add_parser("detect")
    p.add_argument("name", nargs="?", default="safety", help="models/ 아래 모델 폴더 (기본 safety)")
    p.add_argument("--image")
    p.add_argument("--port")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--imgsz", type=int, default=config.DETECT_IMGSZ)
    p = sub.add_parser("mqtt")
    p.add_argument("--secs", type=float, default=10)
    p = sub.add_parser("flow")
    p.add_argument("--robot", default="flowtest", help="시험용 로봇 이름 (진짜 로봇 이름을 쓰면 그 로봇이 움직일 수 있음)")
    p.add_argument("--hold", type=float, default=3.0, help="stop 뒤 F가 안 가는지 지켜보는 시간")
    a = ap.parse_args(argv)
    fn = {"ports": c_ports, "dog": c_dog, "line": c_line, "drive": c_drive, "cam": c_cam, "thermo": c_thermo,
          "detect": c_detect, "mqtt": c_mqtt, "flow": c_flow}[a.what]
    sys.exit(fn(a) or 0)


if __name__ == "__main__":
    main()
