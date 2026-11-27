// 가짜 서버. 서버(server/api.py) 없이 화면을 만들 때 쓴다.
//
// 진짜 서버와 같은 모양으로 흉내 낸다.
//   - fake.socket()  : new WebSocket(".../ws") 대신. onopen · onmessage · onclose · close()
//                      붙자마자 로봇 state 전부, mode, 해제 안 된 경보를 보내고, 그 뒤로 새 메시지
//   - fake.fetch()   : fetch() 대신. POST /events/{id}/check (body {"false_alarm": true}면 오탐), POST /mode, GET /status, GET /admin/check
//                      GET /events?limit=50&before=id&type=heat: 지난 경보 (닫힌 것 포함), 최근 것부터
//                      new FakeServer({ past: 40 })면 지난 경보 40건(닫힘, 일부 오탐)을 미리 넣어 둔다
//                      new FakeServer({ adminPassword: "1234" })면 진짜 서버의 관리자 잠금처럼
//                      경보 해제·모드 변경은 헤더 X-Admin-Password가 맞아야 받는다 (틀리면 401)
//
// 시험용 조작 (진짜 서버에는 없음)
//   - fake.emit(종류, 로봇) : 경보 하나 바로 보냄. 종류는 obstacle · floor_object · helmet · heat · flame
//   - fake.fire(로봇)      : 화재 확정까지 한 번에. 로봇이 과열·불꽃 감지 경보를 보내고, 서버가 교차 확인 끝에
//                            대피 모드로 바꾼 것처럼 (교차 확인 과정은 흉내 내지 않음). 불난 마커는 그 로봇의 마커
//   - fake.fire(로봇, 마커): 위와 같고, 불난 마커를 정해서
//   - fake.halt(로봇, 이유): 길 중간에 멈춤 (state status idle). 이유(hold)를 주면 state에 hold로 실음
//                            (진짜 엣지는 아직 안 보냄, 이슈 #22). fake.resume(로봇)으로 다시 순찰
//
// 대피 모드가 되면 진짜 서버(server/api.py start_evac)처럼 mode 메시지 뒤에 evac 메시지를 보낸다.
// 대피 경로는 server/dispatch.py plan()을 옮긴 evacPlan()으로 센다.
//
// 메시지 필드는 server/fake_robots.py, common/protocol.py와 같다.

import { MARKERS, DOCK_MARKER, ROUTES, RETURN_POINT, EXITS, BATTERY_RETURN } from "./mapdata.js";

// ---------- 대피 경로 (server/dispatch.py를 옮긴 것) ----------

// 마커 → 이웃 마커. ROUTES의 entry → loop 첫 마커, loop 한 바퀴, 복귀 지점 → back (양방향)
function evacEdges() {
  const nb = {};
  for (const m of Object.keys(MARKERS)) nb[m] = new Set();
  const link = (seq) => {
    for (let i = 0; i + 1 < seq.length; i++) {
      if (seq[i] === seq[i + 1]) continue;
      nb[seq[i]].add(seq[i + 1]);
      nb[seq[i + 1]].add(seq[i]);
    }
  };
  for (const [role, r] of Object.entries(ROUTES)) {
    link([...r.entry, r.loop[0]]);
    link([...r.loop, r.loop[0]]);
    link([RETURN_POINT[role], ...r.back]);
  }
  return nb;
}

// 마커 a에서 b로 가는 지도 방향 (위쪽이 N)
function compass(a, b) {
  const [x1, y1] = MARKERS[a], [x2, y2] = MARKERS[b];
  const dx = x2 - x1, dy = y2 - y1;
  if (Math.abs(dx) >= Math.abs(dy)) return dx > 0 ? "E" : "W";
  return dy > 0 ? "S" : "N";
}

// 마커마다 대피 방향. 서버 plan()과 같은 값: [{ marker, dir, exit, path }] 마커 번호 순.
// 출구에 있으면 dir "here", 갈 수 없거나 불난 마커면 dir "x" · exit null · path []
export function evacPlan(fire = null, exits = EXITS) {
  const goals = exits.filter((e) => e !== fire);
  const nb = evacEdges();
  const len = (a, b) => Math.hypot(MARKERS[b][0] - MARKERS[a][0], MARKERS[b][1] - MARKERS[a][1]);
  const dist = {}, nxt = {}, goal = {};
  for (const e of goals) [dist[e], nxt[e], goal[e]] = [0, null, e];
  const done = new Set();
  for (;;) {
    let m = null;
    for (const k of Object.keys(dist)) if (!done.has(k) && (m === null || dist[k] < dist[m])) m = k;
    if (m === null) break;
    done.add(m);
    for (const n of nb[m]) {
      if (n === fire) continue;
      const d = dist[m] + len(m, n);
      if (d < (dist[n] ?? Infinity)) [dist[n], nxt[n], goal[n]] = [d, Number(m), goal[m]];
    }
  }
  return Object.keys(MARKERS).map(Number).sort((a, b) => a - b).map((m) => {
    if (m === fire || !(m in dist)) return { marker: m, dir: "x", exit: null, path: [] };
    const path = [m];
    while (nxt[path.at(-1)] != null) path.push(nxt[path.at(-1)]);
    return { marker: m, dir: path.length === 1 ? "here" : compass(m, path[1]), exit: goal[m], path };
  });
}

export const EVENT_TYPES = ["obstacle", "floor_object", "helmet", "heat", "flame"];
export const MODES = ["normal", "evacuate"];

const MARKER_TICKS = 3;     // 마커 하나 지나는 데 걸리는 초
const DRAIN_PER_MARKER = 0.4;
const BLOCK_TICKS = 6;      // 통로 차단이 풀리기까지 초
const HEAT_TICKS = 8;       // 과열 확인에 머무는 초
const EVENT_CHANCE = 1 / 60; // 순찰 중인 로봇 한 대가 1초에 경보를 낼 확률

// 같은 seed면 같은 순서로 움직인다 (시험용)
function makeRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

class FakeRobot {
  constructor(name, { battery, role, rng }) {
    this.name = name;
    this.rng = rng;
    this.battery = battery;
    this.role = role;
    this.marker = DOCK_MARKER;
    this.status = role ? "patrol" : "idle";
    this.path = role ? ROUTES[role].entry.filter((m) => m !== DOCK_MARKER) : [];
    this.wait = 0;          // blocked · heat_check 남은 초
    this.step = MARKER_TICKS;
    this.before = null;     // 대피 모드 전 상태
    this.sonar = 120;
    this.temp = 24;
    this.hold = null;       // 멈춘 이유 (stop · wait · line ...), 없으면 null
  }

  state() {
    return {
      marker: this.marker,
      status: this.status,
      role: this.role,
      battery: Math.round(this.battery),
      sonar_cm: Math.round(this.sonar * 10) / 10,
      temp_c: Math.round(this.temp * 10) / 10,
      version: "fake",
      hold: this.hold, // 멈춘 이유, 없으면 null (server/fake_robots.py · edge/agent.py와 같게)
    };
  }

  // 길 중간에 멈춤: idle로 바뀌고 resume 전에는 안 움직인다 (edge/agent.py 안전 정지·wait 흉내)
  halt(hold = null) {
    this.status = "idle";
    this.wait = 0;
    this.hold = hold;
  }

  resume() {
    if (this.status !== "idle") return;
    this.hold = null;
    if (this.marker !== DOCK_MARKER && this.role) this.status = "patrol";
  }

  // 1초. 내보낼 경보 종류(없으면 null)
  tick() {
    this.sonar = 80 + this.rng() * 60;
    this.temp += (24 - this.temp) * 0.2 + (this.rng() - 0.5) * 0.6;

    if (this.wait > 0) {
      this.wait -= 1;
      if (this.wait === 0) this.status = "patrol";
      return null;
    }
    if (this.status === "idle" && this.marker === DOCK_MARKER && this.battery < 100) {
      this.battery = Math.min(100, this.battery + 1);
    }
    if (this.status !== "patrol" && this.status !== "dock") return null;

    this.step -= 1;
    if (this.step > 0) return null;
    this.step = MARKER_TICKS;
    this.move();

    if (this.status === "patrol" && this.rng() < EVENT_CHANCE * MARKER_TICKS) {
      const r = this.rng();
      if (r < 0.4) return "obstacle";
      if (r < 0.6) return "floor_object";
      if (r < 0.8) return "helmet";
      return "heat";
    }
    return null;
  }

  move() {
    if (this.path.length === 0) {
      if (this.status === "dock") {
        this.status = "idle";
        return;
      }
      const route = ROUTES[this.role];
      if (this.battery <= BATTERY_RETURN && this.marker === route.loop[route.loop.length - 1]) {
        this.status = "dock";
        this.path = [...route.back];
      } else {
        this.path = [...route.loop];
      }
    }
    this.marker = this.path.shift();
    this.battery = Math.max(0, this.battery - DRAIN_PER_MARKER);
  }

  // 경보에 붙일 값. 로봇 상태도 같이 바뀐다
  eventFields(type) {
    if (type === "obstacle") {
      this.status = "blocked";
      this.wait = BLOCK_TICKS;
      this.sonar = 12;
      return { sonar_cm: 12, person: this.rng() < 0.3 };
    }
    if (type === "heat") {
      this.status = "heat_check";
      this.wait = HEAT_TICKS;
      this.temp = 45.5 + this.rng() * 3.5;
      return { temp_c: Math.round(this.temp * 10) / 10, flame: false, conf: 0.0 };
    }
    if (type === "flame") {
      this.temp = 60 + this.rng() * 20;
      return { temp_c: Math.round(this.temp * 10) / 10, flame: true, conf: round2(0.8 + this.rng() * 0.15) };
    }
    return { conf: round2(0.6 + this.rng() * 0.35) };
  }

  setMode(mode) {
    if (mode === "evacuate" && this.status !== "guide") {
      this.before = this.status;
      this.status = "guide";
      this.wait = 0;
    } else if (mode === "normal" && this.status === "guide") {
      this.status = this.before === "guide" || !this.before ? "idle" : this.before;
      if (this.status === "blocked" || this.status === "heat_check") this.status = "patrol";
      this.before = null;
    }
  }
}

function round2(x) {
  return Math.round(x * 100) / 100;
}

// WebSocket과 같은 모양의 가짜 소켓
class FakeSocket {
  constructor(server) {
    this.server = server;
    this.readyState = 0; // CONNECTING
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
  }

  send() {
    // 화면 → 서버는 HTTP로 한다. 진짜 서버도 소켓으로 온 내용은 버린다.
  }

  close() {
    if (this.readyState >= 2) return;
    this.readyState = 2;
    this.server.defer(() => this._closed(1000, "closed by client"));
  }

  _deliver(msg) {
    if (this.readyState !== 1) return;
    this.onmessage?.({ data: JSON.stringify(msg) });
  }

  _closed(code, reason) {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.server.sockets.delete(this);
    this.onclose?.({ code, reason, wasClean: code === 1000 });
  }
}

export class FakeServer {
  // seed: 같은 값이면 같은 움직임. defer: 소켓 콜백을 미루는 방법 (시험에서 바꿔 끼움)
  constructor({ seed = Date.now(), defer = (fn) => setTimeout(fn, 0), adminPassword = null, past = 0 } = {}) {
    this.adminPassword = adminPassword || null; // 없으면 잠그지 않음 (진짜 서버도 같음)
    this.rng = makeRng(seed);
    this.defer = defer;
    this.mode = "normal";
    this.events = [];       // 해제 여부와 상관없이 모두
    this.nextId = 1;
    this.sockets = new Set();
    this.timer = null;
    this.robots = {
      A: new FakeRobot("A", { battery: 90, role: "A", rng: this.rng }),
      B: new FakeRobot("B", { battery: 70, role: "B", rng: this.rng }),
      C: new FakeRobot("C", { battery: 100, role: null, rng: this.rng }),
      D: new FakeRobot("D", { battery: 40, role: null, rng: this.rng }),
    };
    this.linkAlerts = {}; // 로봇 이름 → active인 link_alert
    if (past > 0) this._seedPast(past);
  }

  // 1초마다 tick. 브라우저에서 쓸 때 부른다
  start() {
    if (!this.timer) this.timer = setInterval(() => this.tick(), 1000);
    return this;
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }

  tick() {
    for (const r of Object.values(this.robots)) {
      const type = r.tick();
      if (type) this._addEvent(r, type);
      this._push(this._stateMsg(r));
    }
  }

  // ---------- 진짜 서버와 같은 입구 ----------

  socket() {
    const s = new FakeSocket(this);
    this.sockets.add(s);
    this.defer(() => this._open(s));
    return s;
  }

  async fetch(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const body = opts.body ? JSON.parse(opts.body) : {};
    const admin = !this.adminPassword || (opts.headers ?? {})["X-Admin-Password"] === this.adminPassword;
    const locked = method === "POST" && /^\/(events\/\d+\/check|mode|fleet\/start|robots\/)/.test(path);
    if (path === "/admin/check" && method === "GET") {
      return admin ? jsonResponse(200, { ok: true }) : jsonResponse(401, { detail: "관리자 비밀번호가 틀림" });
    }
    if (locked && !admin) return jsonResponse(401, { detail: "관리자 비밀번호가 틀림" });

    if (method === "GET" && /^\/events(\?|$)/.test(path)) {
      const q = new URL(path, "http://fake").searchParams;
      const limit = Math.max(1, Math.min(Number(q.get("limit")) || 50, 500));
      const before = q.has("before") ? Number(q.get("before")) : null;
      const type = q.get("type");
      const list = this.events
        .filter((e) => (before == null || e.id < before) && (!type || e.type === type))
        .sort((a, b) => b.id - a.id)
        .slice(0, limit)
        .map((e) => ({ ...e }));
      return jsonResponse(200, list);
    }

    let m = path.match(/^\/events\/(\d+)\/check$/);
    if (m && method === "POST") {
      const e = this.events.find((x) => x.id === Number(m[1]));
      if (!e) return jsonResponse(404, { detail: "이벤트 없음" });
      // 진짜 서버처럼: body {"false_alarm": true}면 오탐, 없으면 정탐. 이미 닫힌 경보는 그대로
      if (!e.checked) {
        e.checked = true;
        e.checked_at = Date.now() / 1000;
        e.false_alarm = body.false_alarm === true;
      }
      this._push({ ...e });
      return jsonResponse(200, { ...e });
    }
    m = path.match(/^\/robots\/([A-Z])\/cmd$/);
    if (m && method === "POST") {
      const r = this.robots[m[1]];
      if (!r) return jsonResponse(404, { detail: "모르는 로봇" });
      if (body.cmd === "stop") r.halt("stop");
      else if (body.cmd === "resume") r.resume();
      else return jsonResponse(400, { detail: "가짜 서버는 stop · resume만" });
      this._push(this._stateMsg(r));
      return jsonResponse(200, { sent: [{ topic: `potent/${r.name}/cmd`, msg: { cmd: body.cmd } }] });
    }
    if (path === "/mode" && method === "POST") {
      if (!MODES.includes(body.mode)) return jsonResponse(400, { detail: "모르는 모드: " + body.mode });
      // fire: 관리자가 불꽃 감지 팝업에서 화재로 확정한 마커 (서버에 받아 달라고 부탁한 값)
      // 진짜 서버처럼: 지도에 없는 마커나 normal에 fire를 주면 400
      if (body.fire != null && (body.mode !== "evacuate" || !Number.isInteger(body.fire) || !(body.fire in MARKERS))) {
        return jsonResponse(400, { detail: "모르는 fire: " + body.fire });
      }
      const fire = body.fire ?? null;
      this.setMode(body.mode, fire);
      return jsonResponse(200, { mode: this.mode });
    }
    if (path === "/status" && method === "GET") {
      return jsonResponse(200, {
        mode: this.mode,
        robots: Object.values(this.robots).map((r) => this._stateMsg(r)),
        open_events: this._openEvents(),
      });
    }
    return jsonResponse(404, { detail: "Not Found" });
  }

  // ---------- 시험용 조작 ----------

  emit(type, name) {
    if (!EVENT_TYPES.includes(type)) throw new Error("모르는 경보: " + type);
    const r = this.robots[name] || this._pickRobot();
    return this._addEvent(r, type);
  }

  // 사람 호출 (이슈 #39): 로봇이 서버와 끊겨 stopMarker에서 멈춤. linkBack으로 내림
  linkLost(name, stopMarker) {
    const r = this.robots[name] || this._pickRobot();
    r.halt("server");
    r.callHuman = true;
    const msg = { kind: "link_alert", robot: r.name, marker: r.marker, stop_marker: stopMarker ?? r.marker, online: false, active: true };
    this.linkAlerts[r.name] = msg;
    this._push(msg);
    this._push(this._stateMsg(r));
    return msg;
  }

  linkBack(name) {
    const r = this.robots[name];
    if (!r) return;
    r.callHuman = false;
    delete this.linkAlerts[r.name];
    this._push({ kind: "link_alert", robot: r.name, marker: r.marker, stop_marker: r.marker, online: true, active: false });
    this._push(this._stateMsg(r));
  }

  halt(name, hold = null) {
    const r = this.robots[name] || this._pickRobot();
    r.halt(hold);
    this._push(this._stateMsg(r));
    return r.name;
  }

  resume(name) {
    const r = this.robots[name];
    if (!r) return;
    r.resume();
    this._push(this._stateMsg(r));
  }

  // 돌려주는 값: 불꽃 감지(flame) 경보. marker: 불난 마커 (안 주면 그 로봇의 마커)
  fire(name, marker) {
    const r = this.robots[name] || this._pickRobot();
    this._addEvent(r, "heat");
    const e = this._addEvent(r, "flame");
    this.setMode("evacuate", marker ?? r.marker);
    return e;
  }

  // fire: 서버가 확정한 불난 마커. 관리자가 /mode로 바꾸면 모름(null). 진짜 서버와 같이
  // 대피가 되면 mode 메시지 다음에 evac 메시지를 보내고, 평상시로 돌아가면 evac은 없어진다
  setMode(mode, fire = null) {
    this.mode = mode;
    for (const r of Object.values(this.robots)) r.setMode(mode);
    this._push({ kind: "mode", mode });
    this.evac = null;
    if (mode === "evacuate") {
      this.evac = { kind: "evac", fire, exits: [...EXITS], arrows: evacPlan(fire) };
      this._push(this.evac);
    }
  }

  // ---------- 안쪽 ----------

  _open(s) {
    if (s.readyState !== 0) return;
    s.readyState = 1;
    s.onopen?.({});
    for (const msg of this._snapshot()) s._deliver(msg);
  }

  _snapshot() {
    return [
      ...Object.values(this.robots).map((r) => this._stateMsg(r)),
      { kind: "mode", mode: this.mode },
      ...(this.evac ? [this.evac] : []),
      ...this._openEvents(),
      ...Object.values(this.linkAlerts), // 사람 호출: 붙자마자 active인 것 (서버도 같게 보내 달라고 부탁)
    ];
  }

  _openEvents() {
    return this.events.filter((e) => !e.checked).map((e) => ({ ...e }));
  }

  _stateMsg(r) {
    return { kind: "state", robot: r.name, ...r.state(), call_human: !!r.callHuman, online: true };
  }

  _addEvent(r, type, extra = {}) {
    const e = {
      kind: "event",
      id: this.nextId++,
      robot: r.name,
      type,
      ts: Math.floor(Date.now() / 1000),
      marker: r.marker,
      photo: null,
      ...r.eventFields(type),
      ...extra,
      checked: false,
      checked_at: null, // 닫은 시각(초). 안 닫혔으면 null (PR #23 서버와 같음)
      false_alarm: null, // 오탐으로 닫았으면 true, 정탐이면 false, 안 닫혔으면 null
    };
    this.events.push(e);
    this._push({ ...e });
    return e;
  }

  // 지난 경보 n건: 지난 하루에 걸쳐, 모두 닫혔고 일부는 오탐. 화면에 보내지는 않는다 (GET /events로만 보임)
  _seedPast(n) {
    const types = ["obstacle", "obstacle", "floor_object", "heat", "heat", "flame", "helmet"];
    const falseRate = { obstacle: 0.15, floor_object: 0.35, heat: 0.3, flame: 0.2, helmet: 0.25 };
    const names = Object.keys(this.robots);
    const now = Math.floor(Date.now() / 1000);
    for (let i = 0; i < n; i++) {
      const type = types[Math.floor(this.rng() * types.length)];
      const ts = now - Math.floor(((n - i) / n) * 86400) - 600;
      const fields = type === "obstacle" ? { sonar_cm: 12, person: this.rng() < 0.3 }
        : type === "heat" ? { temp_c: 46.2, flame: false, conf: 0.0 }
        : type === "flame" ? { temp_c: 63.5, flame: true, conf: 0.86 }
        : { conf: 0.74 };
      this.events.push({
        kind: "event",
        id: this.nextId++,
        robot: names[Math.floor(this.rng() * names.length)],
        type,
        ts,
        marker: 1 + Math.floor(this.rng() * 14),
        photo: null,
        ...fields,
        checked: true,
        checked_at: ts + 60 + Math.floor(this.rng() * 600),
        false_alarm: this.rng() < falseRate[type],
      });
    }
  }

  _pickRobot() {
    const on = Object.values(this.robots).filter((r) => r.status === "patrol");
    const pool = on.length ? on : Object.values(this.robots);
    return pool[Math.floor(this.rng() * pool.length)];
  }

  _push(msg) {
    for (const s of this.sockets) s._deliver(msg);
  }
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}
