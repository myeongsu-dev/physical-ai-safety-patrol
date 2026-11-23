// 대시보드 셀프 체크. 저장소 맨 위에서: node dashboard/selfcheck.mjs
// 1) js/mapdata.js가 server/map_config.py와 같은지
// 2) 가짜 서버가 진짜 서버와 같은 순서·모양으로 메시지를 보내는지
// 3) 로봇 연결 끊김 판단 (js/store.js)
// 4) 안전 현황 단계와 건수
// 5) 화면 파일(js/app.js)이 오류 없이 돌아가는지

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { MARKERS, ROUTES, DOCK_MARKER, BATTERY_RETURN } from "./js/mapdata.js";
import { FakeServer, EVENT_TYPES } from "./js/fake.js";
import { createStore, apply, setServer, robotOnline, connectionSummary, safetySummary, ONLINE_SEC } from "./js/store.js";

// ---------- 1) 지도 데이터 ----------
function readMapConfig() {
  const code =
    "import json; from server import map_config as m, fleet; " +
    "print(json.dumps({'markers': {k: list(v['xy']) for k, v in m.MARKERS.items()}, " +
    "'routes': m.ROUTES, 'dock': m.DOCK_MARKER, 'ret': m.BATTERY_RETURN, 'online': fleet.ONLINE_SEC}))";
  for (const py of ["python3", "python"]) {
    try {
      return JSON.parse(execFileSync(py, ["-c", code], { encoding: "utf8" }));
    } catch {
      // 다음 이름으로
    }
  }
  return null;
}

const cfg = readMapConfig();
if (cfg) {
  const ours = Object.fromEntries(Object.entries(MARKERS).map(([k, v]) => [k, [...v]]));
  assert.deepEqual(ours, cfg.markers, "mapdata.js MARKERS가 map_config.py와 다름");
  assert.deepEqual(ROUTES, cfg.routes, "mapdata.js ROUTES가 map_config.py와 다름");
  assert.equal(DOCK_MARKER, cfg.dock);
  assert.equal(BATTERY_RETURN, cfg.ret);
  assert.equal(ONLINE_SEC, cfg.online, "store.js ONLINE_SEC가 server/fleet.py와 다름");
} else {
  console.log("python을 못 찾아 지도 비교는 건너뜀");
}

// ---------- 시험용 도구 ----------
// 소켓 콜백을 바로 실행하지 않고 쌓아 두었다가 flush()로 한꺼번에 돌린다
const queue = [];
const flush = () => {
  while (queue.length) queue.shift()();
};
const fake = new FakeServer({ seed: 7, defer: (fn) => queue.push(fn) });

function listen(sock) {
  const got = { open: false, msgs: [], close: null };
  sock.onopen = () => (got.open = true);
  sock.onmessage = (ev) => got.msgs.push(JSON.parse(ev.data));
  sock.onclose = (ev) => (got.close = ev);
  return got;
}

// ---------- 2) 붙자마자 받는 것 ----------
const a = listen(fake.socket());
flush();
assert.ok(a.open);
assert.deepEqual(a.msgs.map((m) => m.kind), ["state", "state", "state", "state", "mode"]);
assert.deepEqual(a.msgs.slice(0, 4).map((m) => m.robot), ["A", "B", "C", "D"]);
for (const m of a.msgs.slice(0, 4)) {
  for (const k of ["marker", "status", "role", "battery", "sonar_cm", "temp_c", "version", "online"]) {
    assert.ok(k in m, `state에 ${k} 없음`);
  }
}
assert.equal(a.msgs[4].mode, "normal");

// 시간이 가면 state가 계속 오고, 순찰 로봇이 움직인다
a.msgs.length = 0;
for (let i = 0; i < 30; i++) fake.tick();
const states = a.msgs.filter((m) => m.kind === "state");
assert.ok(states.length >= 30 * 4 - 10, "state가 1초마다 오지 않음");
assert.ok(states.filter((m) => m.robot === "A").some((m) => m.marker !== DOCK_MARKER), "A가 안 움직임");

// 경보: 모양, 처리 완료하면 같은 id가 checked:true로 다시 옴
a.msgs.length = 0;
for (const t of EVENT_TYPES) fake.emit(t, "A");
const evs = a.msgs.filter((m) => m.kind === "event");
assert.deepEqual(evs.map((e) => e.type), EVENT_TYPES);
for (const e of evs) {
  for (const k of ["id", "robot", "type", "ts", "marker", "photo", "checked"]) assert.ok(k in e, `event에 ${k} 없음`);
  assert.equal(e.checked, false);
}
assert.equal(typeof evs.find((e) => e.type === "obstacle").person, "boolean");
assert.ok(evs.find((e) => e.type === "heat").temp_c > 45);

const first = evs[0];
let res = await fake.fetch(`/events/${first.id}/check`, { method: "POST" });
assert.equal(res.status, 200);
const again = a.msgs.filter((m) => m.kind === "event" && m.id === first.id);
assert.equal(again.at(-1).checked, true);
assert.equal((await fake.fetch("/events/9999/check", { method: "POST" })).status, 404);

// 새로 붙으면 처리 완료 안 된 경보만 받는다
const b = listen(fake.socket());
flush();
const openIds = b.msgs.filter((m) => m.kind === "event").map((m) => m.id);
assert.ok(!openIds.includes(first.id) && openIds.length === EVENT_TYPES.length - 1);

// 모드
res = await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate" }) });
assert.equal((await res.json()).mode, "evacuate");
assert.deepEqual(a.msgs.at(-1), { kind: "mode", mode: "evacuate" });
fake.tick();
assert.ok(a.msgs.filter((m) => m.kind === "state").slice(-4).every((m) => m.status === "guide"));
assert.equal((await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "x" }) })).status, 400);
await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });


// ---------- 3) 로봇 연결 끊김 ----------
{
  const st = createStore();
  const T = 1_000_000;
  setServer(st, "open", T);
  // 처음 붙었을 때: A·B는 연결, C는 이미 끊긴 채로 옴, D는 서버가 본 적 없음
  apply(st, { kind: "state", robot: "A", marker: 15, online: true }, T);
  apply(st, { kind: "state", robot: "B", marker: 15, online: true }, T);
  apply(st, { kind: "state", robot: "C", marker: 7, online: false }, T);
  let sum = connectionSummary(st, T);
  assert.equal(sum.total, 4);
  assert.equal(sum.online, 2);
  assert.deepEqual(sum.robots.filter((r) => !r.online).map((r) => r.name), ["C", "D"]);
  assert.equal(sum.robots.find((r) => r.name === "D").known, false);

  // A만 계속 state를 보내고 B는 멈춤 → 5초까지는 연결, 넘으면 끊김
  apply(st, { kind: "state", robot: "A", online: true }, T + 4000);
  assert.ok(robotOnline(st, "B", T + ONLINE_SEC * 1000));
  assert.ok(!robotOnline(st, "B", T + ONLINE_SEC * 1000 + 1));
  assert.ok(robotOnline(st, "A", T + ONLINE_SEC * 1000 + 1));
  assert.equal(connectionSummary(st, T + 6000).online, 1);

  // B가 다시 보내면 바로 연결
  apply(st, { kind: "state", robot: "B", online: true }, T + 7000);
  assert.ok(robotOnline(st, "B", T + 7000));

  // 4대 말고 다른 이름이 오면 함께 센다
  apply(st, { kind: "state", robot: "E", online: true }, T + 7000);
  assert.equal(connectionSummary(st, T + 7000).total, 5);

  // 경보와 모드
  apply(st, { kind: "event", id: 3, type: "heat", checked: false });
  apply(st, { kind: "mode", mode: "evacuate" });
  assert.ok(st.events.has(3) && st.mode === "evacuate");
  apply(st, { kind: "event", id: 3, type: "heat", checked: true });
  assert.ok(!st.events.has(3));

  // 가짜 서버 메시지를 그대로 넣으면 4대 모두 연결
  const st2 = createStore();
  const f2 = new FakeServer({ seed: 1, defer: (fn) => queue.push(fn) });
  const s2 = f2.socket();
  s2.onmessage = (ev) => apply(st2, JSON.parse(ev.data), T);
  flush();
  assert.equal(connectionSummary(st2, T).online, 4);
}

// ---------- 4) 안전 현황 ----------
{
  const T = 2_000_000;
  const st = createStore();
  assert.equal(safetySummary(st, T).level, "unknown"); // 서버에 아직 못 붙음
  setServer(st, "open", T);
  for (const r of ["A", "B", "C", "D"]) apply(st, { kind: "state", robot: r, online: true }, T);
  let s = safetySummary(st, T);
  assert.equal(s.level, "safe");
  assert.deepEqual(s.counts, { fire: 0, obstacle: 0, floor: 0 });

  // 안전모 미착용은 단계와 건수에 안 들어감
  apply(st, { kind: "event", id: 1, type: "helmet", checked: false }, T);
  assert.equal(safetySummary(st, T).level, "safe");

  // 통로 차단 · 밟힘 주의 · 과열 → 주의, 건수
  apply(st, { kind: "event", id: 2, type: "obstacle", checked: false }, T);
  apply(st, { kind: "event", id: 3, type: "floor_object", checked: false }, T);
  apply(st, { kind: "event", id: 4, type: "heat", checked: false }, T);
  s = safetySummary(st, T);
  assert.equal(s.level, "caution");
  assert.deepEqual(s.counts, { fire: 1, obstacle: 1, floor: 1 });

  // 처리 완료하면 빠짐
  for (const id of [2, 3, 4]) apply(st, { kind: "event", id, checked: true }, T);
  assert.equal(safetySummary(st, T).level, "safe");

  // 로봇 한 대가 끊기면 주의
  for (const r of ["A", "B", "C"]) apply(st, { kind: "state", robot: r, online: true }, T + 6000);
  s = safetySummary(st, T + 6000);
  assert.equal(s.level, "caution");
  assert.equal(s.offline, 1);

  // 화재 경보나 대피 모드 → 화재
  apply(st, { kind: "event", id: 5, type: "flame", checked: false }, T + 6000);
  assert.equal(safetySummary(st, T + 6000).level, "fire");
  assert.equal(safetySummary(st, T + 6000).counts.fire, 1);
  apply(st, { kind: "event", id: 5, checked: true });
  apply(st, { kind: "mode", mode: "evacuate" });
  assert.equal(safetySummary(st, T + 6000).level, "fire");
  apply(st, { kind: "mode", mode: "normal" });

  // 서버가 끊기면 확인 불가
  setServer(st, "closed", T + 7000);
  assert.equal(safetySummary(st, T + 7000).level, "unknown");
}

// ---------- 5) 화면 파일이 오류 없이 돌아가는지 ----------
// 브라우저 대신 아주 작은 가짜 document를 두고 js/app.js를 ?fake로 불러 본다
{
  const els = {};
  const el = (id) =>
    (els[id] ??= {
      id, className: "", textContent: "", innerHTML: "", hidden: false,
      querySelector(sel) { return el(id + " " + sel); },
    });
  const timers = [];
  globalThis.document = { getElementById: el };
  globalThis.location = { search: "?fake", protocol: "http:", host: "localhost" };
  globalThis.window = globalThis;
  const realSetInterval = globalThis.setInterval;
  globalThis.setInterval = (fn, ms) => { const t = realSetInterval(fn, ms); timers.push(t); return t; };
  await import("./js/app.js");
  await new Promise((r) => setTimeout(r, 20)); // 가짜 소켓이 열리고 첫 묶음이 오기까지
  assert.equal(el("server .value").textContent, "연결됨");
  assert.equal(el("robots .value").textContent, "4/4대");
  assert.equal((el("robot-list").innerHTML.match(/<li /g) || []).length, 4);
  assert.equal(el("mode").textContent, "평상시");
  assert.equal(el("evacuate").hidden, true);
  globalThis.fake.fire("A");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("mode").textContent, "대피");
  assert.equal(el("evacuate").hidden, false);
  assert.equal(el("level").textContent, "화재");
  assert.equal(el("count-fire").textContent, "1건");
  globalThis.fake.stop();
  timers.forEach(clearInterval);
  globalThis.setInterval = realSetInterval;
}

console.log("dashboard selfcheck OK");
