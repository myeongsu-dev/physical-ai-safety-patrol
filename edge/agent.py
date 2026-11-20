"""Pi 5 실행 진입점: 판단 · MQTT 보고 · 서버 명령 처리. 실행: python -m edge.agent

  python -m edge.agent                # 실기 (보드·카메라·온도 센서·MQTT)
  python -m edge.agent --patrol       # 서버 명령 없이 바로 순찰 시작
  python -m edge.agent --fake         # 장치 없이 PC에서: 가짜 보드·카메라·온도 + 막힘·과열 장면
  python -m edge.agent --fake-dog     # 일부만 가짜로 (--fake-cam, --fake-thermo도 있음)

한 바퀴(step)마다: 보드 줄 처리 → 서버 명령 처리 → 온도 → 카메라 추론 → 이동 유지 → 1초마다 state
"""
import argparse
import logging
import queue
import subprocess
import threading
import time

from common import protocol
from edge import config
from edge.detect import FLAME, HELMET, Detector, Voter, fire, has_person, no_helmet
from edge.dog_link import BoardState
from edge.thermo import is_suspect

log = logging.getLogger("edge.agent")

# 로봇 상태 (서버 fleet.py와 맞출 것): 대기·순찰·막힘 정지·과열 확인·출동·대피 안내·복귀
STATUSES = ("idle", "patrol", "blocked", "heat_check", "go", "guide", "dock")
MOVING = ("patrol", "go", "dock")
PERSON_WAIT_S = 10.0   # F-07: 사람이 이만큼 계속 길을 막고 서 있으면 장애물로 보고
STREAM_LOST_S = 3.0    # 측정값이 이만큼 안 오면 T를 다시 보냄 (보드가 다시 켜졌을 때)


def _same(a, b):
    """마커 비교: 보드는 "7"·7.0, 서버는 7로 줄 수 있음"""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def git_version():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT,
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class Agent:
    def __init__(self, dog, cam, thermo, reporter, helmet=None, flame=None, clock=time.monotonic, version=None):
        self.dog, self.cam, self.thermo, self.reporter = dog, cam, thermo, reporter
        self.helmet, self.flame = helmet, flame
        self.clock = clock
        self.version = version or git_version()
        self.board = BoardState()

        self.status = "idle"
        self.mode = "normal"
        self.marker = None
        self.target = None        # go·dock 목표 구역(마커)
        self.resume_to = None     # 막힘·과열·정지가 끝나면 돌아갈 상태
        self.temp_c = None

        self.blocked = None       # {"since", "person", "reported"}
        self.heat = None          # {"temps", "next", "flame", "flame_hit", "reported", "until"}
        self.heat_quiet_until = 0.0
        self.near = 0             # Pi 보조 정지: 연속으로 가까웠던 횟수

        self.helmet_vote = Voter()
        self.flame_vote = Voter()
        self.last_event = {}      # 종류 → 마지막 보고 시각
        self.last_frame_ts = None
        self.last_move = -1e9
        self.last_d = -1e9
        self.last_t_cmd = -1e9
        self.next_thermo = 0.0
        self.next_state = 0.0

    # ---------- 시작 · 끝 ----------
    def start(self):
        for dev in (self.dog, self.cam, self.thermo):
            dev.open()
        self._start_stream()
        self.reporter.start()
        log.info("로봇 %s 시작 (버전 %s, 안전모 모델 %s, 불꽃 모델 %s)", config.ROBOT_ID, self.version,
                 self._model_ver(self.helmet), self._model_ver(self.flame))

    @staticmethod
    def _model_ver(det):
        return det.version if det is not None and det.ready else "없음"

    def stop(self):
        self.dog.send("S")
        for dev in (self.dog, self.cam, self.thermo):
            dev.close()
        self.reporter.stop()

    def _start_stream(self):
        self.dog.send("T", config.STREAM_MS, config.STREAM_CHANS)
        self.dog.send("V")
        self.last_t_cmd = self.clock()

    def run(self, hz=20):
        period = 1.0 / hz
        while True:
            t = time.perf_counter()
            self.step()
            time.sleep(max(0.0, period - (time.perf_counter() - t)))

    def step(self):
        now = self.clock()
        self._drain(self.dog.inbox, self.on_board)
        self._drain(self.reporter.inbox, lambda m: self.on_command(m[1]) if m[0] == "cmd" else self.on_mode(m[1]))
        self._thermo(now)
        self._vision(now)
        self._keepalive(now)
        if now >= self.next_state:
            self.next_state = now + 1.0
            self.reporter.publish_state(self.state())

    @staticmethod
    def _drain(q, fn):
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                return
            try:
                fn(item)
            except Exception:
                log.exception("처리 중 오류: %r", item)

    def state(self):
        """potent/{로봇}/state 내용 (docs/protocol.md 2절)"""
        return {"marker": self.marker, "status": self.status, "battery": self.board.battery,
                "sonar_cm": self.board.sonar_cm, "temp_c": self.temp_c, "version": self.version}

    # ---------- 움직임 ----------
    def _move(self, force=False):
        """순찰·출동·복귀 중이면 선 따라가기를 보냄. 보드는 한 번에 최대 15초라 끝나기 전에 다시 보냄"""
        if self.status not in MOVING:
            return
        now = self.clock()
        if force or now - self.last_move > config.MOVE_MS / 1000 - 2:
            self.dog.send("F", config.PATROL_STRIDE, config.PATROL_ANGLE, config.MOVE_MS)
            self.last_move = now

    def _halt(self, status):
        self.dog.send("S")
        self.status = status

    def _keepalive(self, now):
        self._move()
        if self.dog.connected and now - self.last_d > STREAM_LOST_S and now - self.last_t_cmd > STREAM_LOST_S:
            self._start_stream()
        if self.blocked and not self.blocked["reported"] and now - self.blocked["since"] > PERSON_WAIT_S:
            self._report_obstacle(self.board.sonar_cm, person=True)  # 가만히 서 있는 사람

    # ---------- 보드에서 온 것 ----------
    def on_board(self, parts):
        if parts[0] == "D":
            self.last_d = self.clock()
        ev = self.board.update(parts)
        if parts[0] == "D":
            self._pi_guard()
        if ev:
            self.on_board_event(*ev)

    def on_board_event(self, ch, val):
        if ch == "stop":
            self.on_blocked(val)
        elif ch == "resume":
            self.on_clear()
        elif ch == "color":
            if val in ("-", ""):
                return
            self.marker = val
            if self.status in ("go", "dock") and self.target is not None and _same(val, self.target):
                log.info("목표 %s 도착", self.target)
                self.target = None
                self._halt("idle")
        elif ch == "done":
            self._move(force=True)

    def _pi_guard(self):
        """F(선 따라가기)는 보드가 초음파로 멈추지 않을 수 있어 Pi도 봄: 14cm 안 3번 연속 → 정지, 18cm 밖 → 재개"""
        d = self.board.sonar_cm
        if not config.PI_GUARD or d is None:
            return
        if self.status in MOVING:
            self.near = self.near + 1 if d < config.SONAR_STOP_CM else 0
            if self.near >= 3:
                self.dog.send("S")
                self.on_blocked(d)
        elif self.status == "blocked" and d > config.SONAR_RESUME_CM:
            self.on_clear()

    def on_blocked(self, sonar):
        """F-03: 막힘 정지. 사진을 보고 사람이면 기다리고, 아니면 obstacle 보고 + 음성"""
        if self.status == "blocked":
            return
        self.resume_to = self.status if self.status in MOVING else self.resume_to
        self.status = "blocked"
        self.near = 0
        frame = self.cam.latest()
        dets = self.helmet(frame.image) if (frame and self.helmet is not None and self.helmet.ready) else []
        self.blocked = {"since": self.clock(), "person": has_person(dets), "reported": False}
        if self.blocked["person"]:
            log.info("막힘: 사람 → 비킬 때까지 기다림")
            return
        self._report_obstacle(sonar, person=False, frame=frame)

    def _report_obstacle(self, sonar, person, frame=None):
        frame = frame or self.cam.latest()
        self.reporter.report_event("obstacle", jpeg=frame.jpeg if frame else None,
                                   marker=self.marker, sonar_cm=sonar, person=person)
        self.dog.send("A", config.VOICE_OBSTACLE, config.VOICE_VOLUME)
        self.blocked["reported"] = True

    def on_clear(self):
        if self.status != "blocked":
            return
        log.info("막힘 풀림")
        self.status = self.resume_to or "patrol"
        self.resume_to = None
        self.blocked = None
        self._move(force=True)

    # ---------- 서버에서 온 것 ----------
    def on_command(self, msg):
        cmd = msg.get("cmd")
        if cmd not in protocol.COMMANDS:
            log.warning("모르는 명령 버림: %s", msg)
            return
        log.info("서버 명령 %s", msg)
        if self.heat:  # 과열 확인 중 명령이 오면 서버가 넘겨받은 것
            self.heat = None
            self.heat_quiet_until = self.clock() + config.HEAT_COOLDOWN_S
        self.blocked = None if cmd != "resume" else self.blocked
        if cmd == "patrol":
            self.target, self.status = None, "patrol"
            self._move(force=True)
        elif cmd == "stop":
            if self.status in MOVING:
                self.resume_to = self.status
            self._halt("idle")
        elif cmd == "resume":
            self.status = self.resume_to or "patrol"
            self.resume_to = None
            self.blocked = None
            self._move(force=True)
        elif cmd in ("go", "dock"):
            self.target, self.status = msg.get("zone"), cmd
            self._move(force=True)
        elif cmd == "wait":
            self._halt("idle")
            self.dog.send("P")
        elif cmd == "guide":
            self._halt("guide")
            self.dog.send("L", "arrow", msg.get("arrow", "x"))

    def on_mode(self, msg):
        mode = msg.get("mode")
        if mode not in protocol.MODES:
            log.warning("모르는 모드 버림: %s", msg)
            return
        log.info("모드 %s", mode)
        self.mode = mode
        if mode == "normal" and self.status == "guide":
            self.dog.send("L", "clear")
            self.status = "idle"

    # ---------- 온도 (F-05) ----------
    def _thermo(self, now):
        if self.heat:
            self._heat_tick(now)
            return
        if now < self.next_thermo:
            return
        self.next_thermo = now + config.THERMO_PERIOD_S
        t = self.thermo.read()
        if t is not None:
            self.temp_c = t
        if is_suspect(t) and now >= self.heat_quiet_until and self.status != "guide":
            self._start_heat(t, now)

    def _start_heat(self, t, now):
        log.info("과열 의심 %.1f℃ → 멈춰서 다시 잼", t)
        prev = self.resume_to if self.status == "blocked" else self.status
        self.resume_to = prev if prev in MOVING else None
        self.blocked = None
        self._halt("heat_check")
        self.flame_vote.reset()
        self.heat = {"temps": [t], "next": now + config.HEAT_RECHECK_S, "flame": 0.0,
                     "flame_hit": False, "reported": False, "until": None}

    def _heat_tick(self, now):
        h = self.heat
        if h["reported"]:
            if now >= self.next_thermo:  # 기다리는 동안에도 state의 온도는 갱신
                self.next_thermo = now + config.THERMO_PERIOD_S
                self.temp_c = self.thermo.read() or self.temp_c
            if now >= h["until"]:
                log.info("과열 보고 뒤 서버 명령 없음 → 하던 일로")
                self._end_heat(now)
            return
        if now < h["next"]:
            return
        h["next"] = now + config.HEAT_RECHECK_S
        t = self.thermo.read()
        if t is not None:
            h["temps"].append(t)
            self.temp_c = t
        if len(h["temps"]) > config.HEAT_CHECKS:
            self._finish_heat(now)

    def _finish_heat(self, now):
        h = self.heat
        peak = max(h["temps"][1:])
        if not is_suspect(peak):
            log.info("다시 재니 %.1f℃ → 과열 아님", peak)
            self._end_heat(now)
            return
        frame = self.cam.latest()
        jpeg = frame.jpeg if frame else None
        self.reporter.report_event("heat", jpeg=jpeg, marker=self.marker, temp_c=peak,
                                   flame=h["flame_hit"], conf=round(h["flame"], 2))
        if h["flame_hit"]:
            self.reporter.report_event("flame", jpeg=jpeg, marker=self.marker, temp_c=peak, conf=round(h["flame"], 2))
        h["reported"], h["until"] = True, now + config.HEAT_HOLD_S

    def _end_heat(self, now):
        self.heat = None
        self.heat_quiet_until = now + config.HEAT_COOLDOWN_S
        self.status = self.resume_to or "idle"
        self.resume_to = None
        self._move(force=True)

    # ---------- 카메라 (F-06 안전모, F-05 불꽃) ----------
    def _vision(self, now):
        frame = self.cam.latest()
        if frame is None or frame.ts == self.last_frame_ts:
            return
        self.last_frame_ts = frame.ts
        if self.heat and not self.heat["reported"]:
            if self.flame is not None and self.flame.ready:  # 불꽃 모델은 과열 의심 때만
                hit, c = fire(self.flame(frame.image))
                self.heat["flame"] = max(self.heat["flame"], c)
                if self.flame_vote.push(hit):
                    self.heat["flame_hit"] = True
            return
        if self.helmet is None or not self.helmet.ready or self.status == "guide":
            return
        hit, c = no_helmet(self.helmet(frame.image))
        if self.helmet_vote.push(hit) and self._cooled("helmet", now):
            self.reporter.report_event("helmet", jpeg=frame.jpeg, marker=self.marker, conf=round(c, 2))
            self.dog.send("A", config.VOICE_HELMET, config.VOICE_VOLUME)

    def _cooled(self, kind, now):
        if now - self.last_event.get(kind, -1e9) < config.EVENT_COOLDOWN_S:
            return False
        self.last_event[kind] = now
        return True


# ---------- 실행 ----------
def _fake_scene(dog):
    """--fake: 10초부터 4초간 앞에 물건 (막힘 → 풀림)"""
    time.sleep(10)
    log.info("[가짜] 앞에 물건")
    dog.sonar = 10.0
    time.sleep(4)
    log.info("[가짜] 물건 치움")
    dog.sonar = 60.0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pi 5 edge agent")
    ap.add_argument("--fake", action="store_true", help="보드·카메라·온도 모두 가짜 + 막힘·과열 장면")
    ap.add_argument("--fake-dog", action="store_true")
    ap.add_argument("--fake-cam", action="store_true")
    ap.add_argument("--fake-thermo", action="store_true")
    ap.add_argument("--patrol", action="store_true", help="서버 명령 없이 바로 순찰")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    from edge.reporter import Reporter

    if a.fake or a.fake_dog:
        from edge.dog_link import FakeDog
        dog = FakeDog()
    else:
        from edge.dog_link import DogLink
        dog = DogLink()
    if a.fake or a.fake_cam:
        from edge.camera import FakeCamera
        cam = FakeCamera()
    else:
        from edge.camera import Camera
        cam = Camera()
    if a.fake or a.fake_thermo:
        from edge.thermo import FakeThermo
        thermo = FakeThermo(script=[(20, 32, 55.0)] if a.fake else None)  # 20~32초 55℃
    else:
        from edge.thermo import Thermo
        thermo = Thermo()

    agent = Agent(dog, cam, thermo, Reporter(),
                  helmet=Detector(HELMET, config.HELMET_CONF), flame=Detector(FLAME, config.FLAME_CONF))
    agent.start()
    if a.fake:
        threading.Thread(target=_fake_scene, args=(dog,), daemon=True).start()
    if a.patrol or a.fake:
        agent.on_command(protocol.command("patrol"))
    try:
        agent.run()
    except KeyboardInterrupt:
        log.info("끝냄")
    finally:
        agent.stop()


if __name__ == "__main__":
    main()
