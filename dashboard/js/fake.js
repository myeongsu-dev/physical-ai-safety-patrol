// 가짜 서버. 서버(server/api.py) 없이 화면을 만들 때 쓴다.
//
// 진짜 서버와 같은 모양으로 흉내 낸다.
//   - fake.socket()  : new WebSocket(".../ws") 대신. onopen · onmessage · onclose · close()
//                      붙자마자 로봇 state 전부, mode, 해제 안 된 경보를 보내고, 그 뒤로 새 메시지
//   - fake.fetch()   : fetch() 대신. POST /events/{id}/check, POST /mode, GET /status
//
// 시험용 조작 (진짜 서버에는 없음)
//   - fake.emit(종류, 로봇) : 경보 하나 바로 보냄. 종류는 obstacle · floor_object · helmet · heat · flame
//   - fake.fire(로봇)      : 화재 경보 + 대피 모드
//   - fake.fire(로봇, true): 위와 같고, 서버와 정하는 중인 제안 필드(화재 통로 구간·의심 구역, 대피 화살표)를 예시 값으로 붙임
//                            (PLACE_SAMPLE, README "서버와 주고받기"). 진짜 서버는 아직 이 필드를 보내지 않는다
//
// 메시지 필드는 server/fake_robots.py, common/protocol.py와 같다.

import { DOCK_MARKER, ROUTES, BATTERY_RETURN } from "./mapdata.js";

// 제안 모양의 예시: 5–6번 통로에서 불, 양옆 A1·B1 의심. 비상구 1(왼쪽)이 불 옆이라 비상구 2(오른쪽)로 안내
export const PLACE_SAMPLE = {
  segment: [5, 6],
  zones: ["A1", "B1"],
  guides: [
    { marker: 2, dir: "E", exit: 2, path: [2, 3, 4, 8] },
    { marker: 7, dir: "E", exit: 2, path: [7, 8] },
    { marker: 11, dir: "E", exit: 2, path: [11, 12, 13, 8] },
  ],
};

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
    };
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
  constructor({ seed = Date.now(), defer = (fn) => setTimeout(fn, 0) } = {}) {
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

    let m = path.match(/^\/events\/(\d+)\/check$/);
    if (m && method === "POST") {
      const e = this.events.find((x) => x.id === Number(m[1]));
      if (!e) return jsonResponse(404, { detail: "이벤트 없음" });
      e.checked = true;
      this._push({ ...e });
      return jsonResponse(200, { ...e });
    }
    if (path === "/mode" && method === "POST") {
      if (!MODES.includes(body.mode)) return jsonResponse(400, { detail: "모르는 모드: " + body.mode });
      this.setMode(body.mode);
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

  fire(name, place = false) {
    const p = place === true ? PLACE_SAMPLE : place || null;
    const r = this.robots[name] || this._pickRobot();
    const e = this._addEvent(r, "flame", p ? { segment: p.segment, zones: p.zones } : {});
    this.setMode("evacuate", p ? { guides: p.guides } : {});
    return e;
  }

  // extra: mode 메시지에 붙일 제안 필드 (guides). 대피 해제(normal)면 붙이지 않는다
  setMode(mode, extra = {}) {
    this.mode = mode;
    this.modeExtra = mode === "evacuate" ? extra : {};
    for (const r of Object.values(this.robots)) r.setMode(mode);
    this._push({ kind: "mode", mode, ...this.modeExtra });
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
      { kind: "mode", mode: this.mode, ...(this.modeExtra ?? {}) },
      ...this._openEvents(),
    ];
  }

  _openEvents() {
    return this.events.filter((e) => !e.checked).map((e) => ({ ...e }));
  }

  _stateMsg(r) {
    return { kind: "state", robot: r.name, ...r.state(), online: true };
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
    };
    this.events.push(e);
    this._push({ ...e });
    return e;
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
