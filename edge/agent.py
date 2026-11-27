"""Pi 5 실행 진입점: 판단 · MQTT 보고 · 서버 명령 처리. 실행: python -m edge.agent

  python -m edge.agent                # 실기 (보드·카메라·온도 센서·MQTT)
  python -m edge.agent --patrol       # 서버 명령 없이 바로 순찰 시작
  python -m edge.agent --fake         # 장치 없이 PC에서: 가짜 보드·카메라·온도 + 막힘·과열 장면
  python -m edge.agent --fake-dog     # 일부만 가짜로 (--fake-cam, --fake-thermo도 있음)
  python -m edge.agent --drive        # 통신·주행만: 실제 보드 + MQTT, 머리 카메라·온도·AI 없이 (가상 과열·장애물 장면 없음)
  python -m edge.agent --web 0        # 디버그 웹 화면 끄기 (기본: http://<Pi IP>:8080, edge/webview.py)

한 바퀴(step)마다: 보드 줄 처리 → 서버 명령 처리 → 보드 연결 감시 → 온도 → 카메라 추론 → 이동 유지 → 1초마다 state

걷기 안전 (보드 F는 선을 놓치면 직진하고, 끊겼다 다시 붙으면 Pi가 F를 다시 보낼 수 있어서):
- 서버 stop·wait·guide 뒤, 또는 Pi가 안전 정지한 뒤에는 서버가 patrol·resume을 보내기 전까지 F를 보내지 않음 (hold)
- 안전 정지: 보드 포트 끊김·다시 붙음, 측정값(D) 끊김, 선 유실(line=-1이 LINE_LOST_S), F에 보드 오류·무응답
- F에 보드 ok가 오기 전에는 state에 patrol이 아니라 idle로 알림
- 서버 신호(potent/server/alive, 1초마다)가 ALIVE_TIMEOUT_S 안 오면 단독 모드: 다음 마커까지 가서 멈춤 (hold server).
  다시 붙어도 서버 resume·patrol을 기다림. 서버 신호를 한 번도 못 받았으면(예전 서버) 이 감시는 꺼져 있음
- 배터리(최근 BATTERY_AVG_S 3초 평균, state battery도 이 값)가 BATTERY_STOP_PCT(15%) 이하면 그 자리에서 멈춤 (hold battery)
"""
import argparse
import collections
import logging
import queue
import signal
import subprocess
import threading
import time

from common import protocol
from edge import config
from edge.detect import SAFETY, Detector, Voter, fire, has_person, no_helmet
from edge.dog_link import BoardState
from edge.thermo import is_suspect

log = logging.getLogger("edge.agent")

# 로봇 상태 (서버 fleet.py와 맞출 것): 대기·순찰·막힘 정지·과열 확인·출동·대피 안내·복귀
STATUSES = ("idle", "patrol", "blocked", "heat_check", "go", "guide", "dock")
MOVING = ("patrol", "go", "dock")
WATCHED = MOVING + ("blocked", "heat_check")   # 보드가 끊기면 안전 정지할 상태 (멈춰 있어도 곧 다시 걸을 수 있음)
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
    def __init__(self, dog, cam, thermo, reporter, model=None, clock=time.monotonic, version=None):
        self.dog, self.cam, self.thermo, self.reporter = dog, cam, thermo, reporter
        self.model = model        # 통합 모델 (0 fire · 1 head · 2 helmet · 3 person), 한 장에 한 번 추론
        self.clock = clock
        self.version = version or git_version()
        self.board = BoardState()

        self.status = "idle"
        self.mode = "normal"
        self.role = None          # 서버가 patrol로 정해 준 순찰 역할 A·B (ROBOT_ID와 별개)
        self.hold = None          # 다시 걷지 않는 이유: stop·wait·guide·link·stream·line·board·no_ack·send·server·battery·not_ready (patrol·resume으로만 풀림)
        self.acked = False        # 지금 이동(F)을 보드가 ok 했는지
        self.await_ack = None     # 보드 답을 기다리는 F를 보낸 시각
        self.line_lost_since = None
        self.link_mark = None     # (포트 연 횟수, 감시 끊김 횟수): 바뀌면 보드가 멈췄을 수 있음
        self.marker = None
        self.target = None        # go·dock 목표 구역(마커)
        self.resume_to = None     # 막힘·과열·정지가 끝나면 돌아갈 상태
        self.temp_c = None
        self.alive_at = None      # 서버 신호를 마지막으로 받은 시각 (None: 아직 한 번도 못 받음 → 감시 안 함)
        self.server_lost = False
        self.solo = None          # 단독 모드에서 멈추러 가는 중: {"from": 끊길 때 마커, "by": 늦어도 멈출 시각}
        self.bat_samples = collections.deque()  # (시각, %) 최근 BATTERY_AVG_S 동안의 보드 배터리

        self.blocked = None       # {"since", "person", "reported"}
        self.heat = None          # {"temps", "next", "flame", "flame_hit", "reported", "until"}
        self.heat_quiet_until = 0.0
        self.near = 0             # Pi 보조 정지: 연속으로 가까웠던 횟수
        self.last_dets = None     # 마지막 추론 {"ts", "size": (w, h), "dets": [...]} (웹 화면용)

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
        self.link_mark = self._link_now()
        self._start_stream()
        self.reporter.start()
        log.info("로봇 %s 시작 (버전 %s, 통합 모델 %s)", config.ROBOT_ID, self.version, self._model_ver(self.model))

    @staticmethod
    def _model_ver(det):
        return det.version if det is not None and det.ready else "없음"

    def stop(self):
        """프로그램 끝: 보드 정지(S) → 서버에 idle 한 번 알리고 닫음"""
        self.dog.send("S")
        self.status, self.hold, self.acked = "idle", "exit", False
        try:
            self.reporter.publish_state(self.state())
        except Exception:
            log.exception("마지막 state 보내기 실패")
        for dev in (self.dog, self.cam, self.thermo):
            dev.close()
        self.reporter.stop()

    def _start_stream(self):
        self.dog.send("T", config.STREAM_MS, config.STREAM_CHANS)
        self.dog.send("V")
        self.last_t_cmd = self.clock()

    def run(self, hz=20, until=None):
        """until(threading.Event)가 켜질 때까지 (없으면 계속)"""
        period = 1.0 / hz
        while until is None or not until.is_set():
            t = time.perf_counter()
            self.step()
            time.sleep(max(0.0, period - (time.perf_counter() - t)))

    def step(self):
        now = self.clock()
        self._drain(self.dog.inbox, self.on_board)
        self._drain(self.reporter.inbox, self.on_server)
        self._watch_link(now)
        self._watch_server(now)
        self._watch_battery(now)
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
        """potent/{로봇}/state 내용 (docs/protocol.md 2절). 걷는 상태는 보드가 F에 ok 한 뒤에만 알림"""
        status = self.status if self.status not in MOVING or self.acked else "idle"
        return {"marker": self.marker, "status": status, "role": self.role, "battery": self.battery,
                "sonar_cm": self.board.sonar_cm, "temp_c": self.temp_c, "version": self.version,
                "hold": self.hold}   # 다시 걷지 않는 이유 (없으면 None → null). 서버는 이것이 None일 때만 자동 출발

    # ---------- 움직임 ----------
    def _move(self, force=False):
        """순찰·출동·복귀 중이면 선 따라가기를 보냄. 보드는 한 번에 최대 15초라 끝나기 전에 다시 보냄.
        hold(정지 명령·안전 정지) 중에는 절대 보내지 않음"""
        if self.status not in MOVING or self.hold:
            return
        now = self.clock()
        if force or now - self.last_move > config.MOVE_MS / 1000 - 2:
            if force:
                self.acked = False
            if not self.dog.send("F", config.PATROL_STRIDE, config.PATROL_ANGLE, config.MOVE_MS):
                self._safe_halt("send")
                return
            self.last_move = now
            self.await_ack = now

    def _halt(self, status):
        self.dog.send("S")
        self.status = status
        self.acked = False
        self.await_ack = None
        self.next_state = 0.0   # 멈춤을 서버에 바로 알림

    def _safe_halt(self, reason):
        """Pi가 판단한 안전 정지: S → idle. 서버가 resume·patrol을 보내기 전에는 다시 걷지 않음 (하던 일은 resume_to에)"""
        if self.status in MOVING:
            self.resume_to = self.status
        log.warning("안전 정지 (%s) → 서버 resume·patrol을 기다림", reason)
        self.hold = reason
        self.solo = None
        self.near, self.line_lost_since = 0, None
        if self.status in MOVING or self.status == "blocked":
            self.blocked = None
            self._halt("idle")
        else:  # 과열 확인 중: 확인은 이어 가되 끝나도 걷지 않음
            self.dog.send("S")
            self.acked, self.await_ack = False, None

    def _link_now(self):
        return getattr(self.dog, "opens", 0), getattr(self.dog, "quiet_breaks", 0)

    def _watch_link(self, now):
        """보드 연결 감시. 서버 연결과 달리 보드가 끊기면 멈추고, 다시 붙어도 저절로 걷지 않음"""
        mark = self._link_now()
        changed, self.link_mark = mark != self.link_mark, mark
        if self.status not in WATCHED or self.hold:
            return
        if not self.dog.connected or changed:  # 끊김, 또는 다시 붙음·감시 끊김 (그사이 보드가 멈췄을 수 있음)
            self._safe_halt("link")
        elif now - self.last_d > config.SENSOR_STALE_S:
            self._safe_halt("stream")

    def _cant_move(self):
        """걷기 시작해도 되는지. 안 되면 이유 (patrol·resume·go·dock 전에 봄)"""
        if not self.dog.connected:
            return "보드 연결 없음"
        if self.clock() - self.last_d > config.SENSOR_STALE_S:
            return "보드 측정값(D)이 안 옴"
        if self._battery_low():
            return "배터리 %s%% (%d%% 이하)" % (self.battery, config.BATTERY_STOP_PCT)
        if self.board.commands is not None and "F" not in self.board.commands:
            return "보드가 F를 지원하지 않음 (main.py 확인)"
        if config.LINE_GUARD and "line" in self.board.chans:
            x = self.board.line
            if x is None or x < 0:
                return "바닥 비전 모듈이 선을 못 찾음 (line=%s)" % x
        return None

    def _keepalive(self, now):
        if self.await_ack is not None and now - self.await_ack > config.ACK_TIMEOUT_S:
            log.warning("보드가 F에 %.1f초 동안 답이 없음", config.ACK_TIMEOUT_S)
            self.await_ack = None
            if self.status in MOVING:
                self._safe_halt("no_ack")
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
            self._note_battery(self.last_d)
            self._pi_guard()
            self._line_guard()
        elif len(parts) >= 2 and parts[1] == "ok":
            self.on_ack(parts[0], None)
        elif parts[0] == "!" and len(parts) >= 2:
            self.on_ack(parts[1], parts[2] if len(parts) > 2 else "")
        if ev:
            self.on_board_event(*ev)

    def on_ack(self, name, error):
        """보드 답: CMD|F|ok|$ (error=None) · CMD|!|F|이유|$. 보낸 것(쓰기 성공)과 보드가 받아들인 것을 나눔"""
        if name == "F":
            self.await_ack = None
            if error is None:
                if self.status in MOVING and not self.hold and not self.acked:  # 정지 뒤 늦게 온 ok는 무시
                    self.acked = True
                    self.next_state = 0.0   # 걷기 시작을 서버에 바로 알림
            else:
                log.warning("보드가 F를 거부: %s", error)
                if self.status in MOVING:
                    self._safe_halt("board")
        elif name == "loop" and error is not None:  # 보드 동작 반복에서 오류 → 보드가 스스로 멈춤
            log.warning("보드 동작 오류(보드가 멈춤): %s", error)
            if self.status in WATCHED and not self.hold:
                self._safe_halt("board")
        elif name == "S" and error is not None:
            log.error("보드가 정지(S)를 못 함: %s", error)

    def _line_guard(self):
        """보드가 선을 못 찾고 E lost를 못 보낸 경우의 예비: LINE_LOST_S 넘게 선이 안 보이면 Pi가 멈춤 (line 채널을 받을 때만)"""
        if not config.LINE_GUARD or "line" not in self.board.chans or self.status not in MOVING:
            self.line_lost_since = None
            return
        x, now = self.board.line, self.clock()
        if x is not None and x >= 0:
            self.line_lost_since = None
        elif self.line_lost_since is None:
            self.line_lost_since = now
        elif now - self.line_lost_since >= config.LINE_LOST_S:
            log.warning("선이 %.1f초 넘게 안 보임", now - self.line_lost_since)
            self._safe_halt("line")

    def on_board_event(self, ch, val):
        if ch == "stop":
            self.on_blocked(val)
        elif ch == "resume":
            self.on_clear()
        elif ch == "color":
            if val in ("-", ""):
                return
            if not _same(val, self.marker):
                self.next_state = 0.0   # 새 마커는 서버에 바로 알림 (교통 정리 예약·wait가 늦지 않게)
            self.marker = val
            if self.status in ("go", "dock") and self.target is not None and _same(val, self.target):
                log.info("목표 %s 도착", self.target)
                self.target = None
                self._halt("idle")
            elif self.solo and self.status in MOVING and not _same(val, self.solo["from"]):
                self._solo_stop("다음 마커 %s" % val)
        elif ch == "done":
            self._move(force=True)
        elif ch == "lost":  # 보드가 놓친 선을 제자리 돌며 찾다가 못 찾고 멈춤 (val = 돈 각도)
            log.warning("보드가 선을 못 찾음 (%s° 돌아 봄)", val)
            if self.status in MOVING:
                self._safe_halt("line")

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
        dets = self.model(frame.image) if (frame and self.model is not None and self.model.ready) else []
        self._keep_dets(frame, dets)
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
        self.blocked = None
        self._auto_resume(self.resume_to or "patrol")

    def _auto_resume(self, target):
        """막힘·과열이 끝나 하던 일로. hold(정지 명령·안전 정지) 중이면 걷지 않고 idle로 기다림"""
        if self.hold and target in MOVING:
            self.status, self.resume_to = "idle", target
            return
        self.status, self.resume_to = target, None
        self._move(force=True)

    # ---------- 서버에서 온 것 ----------
    def on_command(self, msg):
        cmd = msg.get("cmd") if isinstance(msg, dict) else None
        if not isinstance(cmd, str) or cmd not in protocol.COMMANDS:
            log.warning("모르는 명령 버림: %s", msg)
            return
        role = msg.get("role")
        if cmd == "patrol" and role is not None and role not in protocol.ROLES:
            log.warning("모르는 순찰 역할 버림: %s", msg)
            return
        log.info("서버 명령 %s", msg)
        if self.heat:  # 과열 확인 중 명령이 오면 서버가 넘겨받은 것
            self.heat = None
            self.heat_quiet_until = self.clock() + config.HEAT_COOLDOWN_S
        self.blocked = None if cmd != "resume" else self.blocked
        if cmd == "patrol":
            if role is not None:  # 역할 없이 온 patrol은 지난 역할을 이어 감
                self.role = role
            self._start("patrol", target=None)
        elif cmd == "stop":
            if self.status in MOVING:
                self.resume_to = self.status
            self._halt("idle")
            self.hold = "stop"
        elif cmd == "resume":
            self._start(self.resume_to or "patrol", target=self.target)
        elif cmd == "go":
            self._start(cmd, target=msg.get("zone"))
        elif cmd == "dock":  # 서버는 zone 없이 보냄 → 충전 스테이션 마커에서 멈춤
            zone = msg.get("zone")
            self._start(cmd, target=config.DOCK_MARKER if zone is None else zone)
        elif cmd == "wait":
            self._halt("idle")
            self.hold = "wait"
            self.dog.send("P")
        elif cmd == "guide":
            self._halt("guide")
            self.hold = "guide"
            self.dog.send("L", "arrow", msg.get("arrow", "x"))

    def _start(self, status, target):
        """서버가 걷게 할 때 (patrol·resume·go·dock): 보드·측정값·선을 확인하고 F. 안 되면 멈춘 채 idle"""
        why = self._cant_move()
        if why:
            log.warning("%s 못 함: %s → idle로 멈춰 있음", status, why)
            if self.status in MOVING or self.status == "blocked":
                self.resume_to = status
                self._halt("idle")
            self.hold = "battery" if self._battery_low() else (self.hold or "not_ready")
            return
        self.solo = None
        self.status, self.target, self.resume_to = status, target, None
        self.blocked, self.hold, self.near, self.line_lost_since = None, None, 0, None
        self._move(force=True)

    def on_server(self, item):
        kind, msg = item
        if kind == "cmd":
            self.on_command(msg)
        elif kind == "mode":
            self.on_mode(msg)
        elif kind == "alive":
            self.on_alive(msg)

    def on_alive(self, msg):
        """서버가 1초마다 보내는 살아 있다는 신호. 다시 와도 스스로 걷지 않음 (서버가 위치를 보고 resume)"""
        self.alive_at = self.clock()
        if self.server_lost:
            log.info("서버 신호 다시 옴 → 멈춘 채 서버 resume·patrol을 기다림")
            self.server_lost = False
        mode = msg.get("mode") if isinstance(msg, dict) else None
        if mode in protocol.MODES and mode != self.mode:  # 끊긴 동안 놓친 mode 바꿈을 맞춤
            self.on_mode({"mode": mode})

    def _watch_server(self, now):
        """서버 신호가 ALIVE_TIMEOUT_S 안 오면 단독 모드: 걷는 중이면 다음 마커까지 가서 멈춤, 막힘·과열 확인 중이면 끝나도 안 걸음"""
        if self.solo and now >= self.solo["by"] and self.status in MOVING:
            self._solo_stop("%.0f초 안에 다음 마커가 안 나옴" % config.SERVER_STOP_MAX_S)
        if self.solo and self.status not in MOVING:  # 그사이 막힘·과열·안전 정지·서버 명령으로 이미 멈춤
            self.solo = None
            if self.status in ("blocked", "heat_check"):
                self.hold = self.hold or "server"
        if config.ALIVE_TIMEOUT_S <= 0 or self.alive_at is None or self.server_lost:
            return
        if now - self.alive_at <= config.ALIVE_TIMEOUT_S:
            return
        self.server_lost = True
        log.warning("서버 신호가 %.0f초 넘게 안 옴 → 단독 모드", now - self.alive_at)
        if self.hold:
            return
        if self.status in MOVING:
            self.solo = {"from": self.marker, "by": now + config.SERVER_STOP_MAX_S}
            log.warning("단독 모드: 다음 마커까지 가서 멈춤 (지금 마커 %s)", self.marker)
        elif self.status in ("blocked", "heat_check"):
            self.hold = "server"

    def _solo_stop(self, why):
        log.warning("단독 모드 정지 (%s) → 서버 resume·patrol을 기다림", why)
        self.solo = None
        self.resume_to = self.status
        self._halt("idle")
        self.hold = "server"

    def _note_battery(self, now):
        """D 한 줄의 배터리를 평균에 넣음. 보드가 0으로 잘못 읽은 값은 뺌 (넣으면 20%대에서도 평균이 15% 아래로 떨어짐)"""
        try:
            raw = float(self.board.values.get("bat"))
        except (TypeError, ValueError):
            return
        b = self.board.battery
        if b is None or raw == 0:
            return
        q = self.bat_samples
        q.append((now, b))
        while now - q[0][0] > config.BATTERY_AVG_S:
            q.popleft()

    @property
    def battery(self):
        """보드 배터리 %의 최근 BATTERY_AVG_S 평균 (state battery). 걸을 때 전압이 잠깐 떨어지는 것을 거름"""
        q = self.bat_samples
        return int(round(sum(b for _, b in q) / len(q))) if q else None

    def _battery_low(self):
        """평균이 BATTERY_STOP_PCT 이하인지. 평균 시간을 거의 다 채운 뒤에만 판단 (표본 몇 개로 멈추거나 출발을 막지 않게).
        걷는 중 정지와 출발 판단(patrol · resume · go · dock)이 같은 기준을 씀"""
        b, q = self.battery, self.bat_samples
        if b is None or b > config.BATTERY_STOP_PCT:
            return False
        return q[-1][0] - q[0][0] >= config.BATTERY_AVG_S * 0.8

    def _watch_battery(self, now):
        """배터리 평균이 BATTERY_STOP_PCT 이하면 그 자리에서 멈춤"""
        if self._battery_low() and self.status in WATCHED and self.hold != "battery":
            log.warning("배터리 %s%% (최근 %.0f초 평균) → 멈춤", self.battery, config.BATTERY_AVG_S)
            self._safe_halt("battery")

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
        self._auto_resume(self.resume_to or "idle")

    # ---------- 카메라 (F-06 안전모, F-05 불꽃) ----------
    def _vision(self, now):
        frame = self.cam.latest()
        if frame is None or frame.ts == self.last_frame_ts:
            return
        self.last_frame_ts = frame.ts
        if self.model is None or not self.model.ready:
            return
        heat_check = self.heat and not self.heat["reported"]
        if not heat_check and self.status == "guide":
            return
        dets = self.model(frame.image)   # 통합 모델: 한 장에 한 번 추론하고 결과를 나눠 씀
        self._keep_dets(frame, dets)
        if heat_check:                   # 불꽃 판정은 과열 의심 때만 (그동안 안전모 판정은 쉼)
            hit, c = fire(dets)
            self.heat["flame"] = max(self.heat["flame"], c)
            if self.flame_vote.push(hit):
                self.heat["flame_hit"] = True
            return
        hit, c = no_helmet(dets)
        if self.helmet_vote.push(hit) and self._cooled("helmet", now):
            self.reporter.report_event("helmet", jpeg=frame.jpeg, marker=self.marker, conf=round(c, 2))
            self.dog.send("A", config.VOICE_HELMET, config.VOICE_VOLUME)

    def _keep_dets(self, frame, dets):
        """웹 화면에 상자를 그리게 마지막 결과를 남김 (새 dict로 바꿔 끼워 다른 스레드가 읽어도 안전)"""
        shape = getattr(frame.image, "shape", None) if frame else None
        self.last_dets = {"ts": self.clock(), "size": (shape[1], shape[0]) if shape else None,
                          "dets": [{"cls": d.cls, "conf": round(d.conf, 3), "box": [round(v, 1) for v in d.box]}
                                   for d in dets]}

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
    ap.add_argument("--drive", action="store_true",
                    help="통신·주행만: 머리 카메라·온도·AI 없이 (가짜 카메라·온도, 장면 없음). 보드는 --fake-dog가 없으면 실제")
    ap.add_argument("--patrol", action="store_true", help="서버 명령 없이 바로 순찰")
    ap.add_argument("--role", choices=protocol.ROLES, help="--patrol 때 순찰 역할")
    ap.add_argument("--web", type=int, default=config.WEB_PORT, metavar="PORT",
                    help="디버그 웹 화면 포트 (0이면 끔, 기본 %(default)s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if a.drive:
        a.fake_cam = a.fake_thermo = True

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

    agent = Agent(dog, cam, thermo, Reporter(), model=None if a.drive else Detector(SAFETY))

    def _on_term(*_):  # systemctl stop·kill도 Ctrl-C처럼 → finally에서 보드 정지
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)
    web = None
    try:
        if a.web:  # 장치보다 먼저: 포트 열기 실패 같은 시작 기록도 웹에 남게
            from edge.webview import WebView
            web = WebView(agent, port=a.web)
            web.start()
        agent.start()
        if a.fake:
            threading.Thread(target=_fake_scene, args=(dog,), daemon=True).start()
        if a.patrol or a.fake:
            _wait_ready(agent)
            agent.on_command(protocol.command("patrol", **({"role": a.role} if a.role else {})))
        agent.run()
    except KeyboardInterrupt:
        log.info("끝냄")
    finally:
        agent.stop()   # 보드 S. 강제 종료(kill -9·전원)면 보드가 2초 감시로 멈춤
        if web:
            web.stop()


def _wait_ready(agent, secs=3.0):
    """--patrol: 보드 V·측정값이 올 때까지 잠깐 돌림 (안 오면 patrol이 이유를 기록하고 멈춰 있음)"""
    end = time.monotonic() + secs
    while time.monotonic() < end and agent._cant_move():
        agent.step()
        time.sleep(0.05)


if __name__ == "__main__":
    main()
