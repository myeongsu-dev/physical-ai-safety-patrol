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
import { createStore, apply, setServer, robotOnline, connectionSummary, safetySummary, openAlerts, robotPlace, robotLabel, ONLINE_SEC } from "./js/store.js";
import { robotLayer, MARKER_POS } from "./js/map.js";

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

// 경보: 모양, 해제하면 같은 id가 checked:true로 다시 옴
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

// 새로 붙으면 해제 안 된 경보만 받는다
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

  // 서버가 끊김을 알려 줌 (server/link-status): online:false + seen_ago
  // 보고 있던 중이면 마지막 수신 시각이 남고, seen_ago가 있으면 그걸로 맞춘다
  apply(st, { kind: "state", robot: "B", online: false, seen_ago: 5.5 }, T + 12500);
  assert.ok(!robotOnline(st, "B", T + 12500));
  assert.equal(connectionSummary(st, T + 12500).robots.find((r) => r.name === "B").lastSeen, T + 7000);
  // seen_ago 없이 online:false만 오면, 이 화면이 마지막으로 받은 시각을 그대로 둔다
  apply(st, { kind: "state", robot: "A", online: true }, T + 20000);
  apply(st, { kind: "state", robot: "A", online: false }, T + 26000);
  assert.ok(!robotOnline(st, "A", T + 26000));
  assert.equal(connectionSummary(st, T + 26000).robots.find((r) => r.name === "A").lastSeen, T + 20000);
  // 화면을 새로 열었는데 seen_ago가 있으면 마지막 수신 시각을 알 수 있다
  const st3 = createStore();
  apply(st3, { kind: "state", robot: "C", online: false, seen_ago: 30 }, T);
  assert.equal(connectionSummary(st3, T).robots.find((r) => r.name === "C").lastSeen, T - 30000);
  // state가 다시 오면 연결
  apply(st, { kind: "state", robot: "B", online: true }, T + 27000);
  assert.ok(robotOnline(st, "B", T + 27000));

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

  // 해제하면 빠짐
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

  // 최근 경보: 해제 안 된 것만, 새것부터, 안전모 미착용 뺌
  apply(st, { kind: "event", id: 6, type: "obstacle", checked: false });
  apply(st, { kind: "event", id: 7, type: "heat", checked: false });
  assert.deepEqual(openAlerts(st).map((e) => e.id), [7, 6]);
  for (let id = 10; id < 20; id++) apply(st, { kind: "event", id, type: "obstacle", checked: false });
  assert.equal(openAlerts(st).length, 5);

  // 서버가 끊기면 확인 불가
  setServer(st, "closed", T + 7000);
  assert.equal(safetySummary(st, T + 7000).level, "unknown");
}

// ---------- 로봇 위치: 카드 글자와 지도 ----------
assert.equal(robotPlace({ marker: 8, status: "patrol", role: "A" }), "A 경로 · 마커 8");
assert.equal(robotPlace({ marker: 15, status: "idle", role: null }), "충전 스테이션");
assert.equal(robotPlace({ marker: 14, status: "dock" }), "마커 14");
assert.equal(robotPlace(undefined), "-");
// 화면 이름: A~D → MD-1~MD-4, 모르는 이름은 그대로
assert.deepEqual(["A", "B", "C", "D", "E"].map(robotLabel), ["MD-1", "MD-2", "MD-3", "MD-4", "E"]);
assert.deepEqual(Object.keys(MARKER_POS).map(Number), Object.keys(MARKERS).map(Number)); // 지도 마커 번호 = map_config 번호
{
  const svg = robotLayer([
    { name: "A", marker: 8, online: true },
    { name: "B", marker: 15, online: true },
    { name: "D", marker: 15, online: false },
    { name: "X", marker: 99, online: true }, // 없는 마커는 안 그림
  ]);
  assert.equal((svg.match(/class="robot"/g) || []).length, 3);
  assert.ok(svg.includes(">A</text>") && svg.includes(">D</text>") && !svg.includes(">X</text>"));
  assert.ok(svg.includes("stroke-dasharray"), "끊긴 로봇은 점선");
  // 같은 마커(15)에 두 대면 x가 다르다
  const xs = [...svg.matchAll(/<rect x="([\d.-]+)" y="[\d.-]+" width="120"/g)].map((m) => Number(m[1]));
  assert.equal(new Set(xs).size, 3);
}

// ---------- 5) 화면 파일이 오류 없이 돌아가는지 ----------
// 브라우저 대신 아주 작은 가짜 document를 두고 js/app.js를 ?fake로 불러 본다
const realSetInterval = globalThis.setInterval;
const tickWait = (ms = 0) => new Promise((r) => setTimeout(r, ms));

// search: 주소 뒤 ?… 부분. 같은 app.js를 두 번 띄우려고 import 주소에 tag를 붙인다
async function bootApp(search, tag) {
  const els = {};
  const el = (id) =>
    (els[id] ??= {
      id, className: "", textContent: "", innerHTML: "", hidden: false,
      querySelector(sel) { return el(id + " " + sel); },
      setAttribute(k, v) { this[k] = v; },
    });
  const timers = [];
  let onClick = null;
  globalThis.document = {
    getElementById: el,
    addEventListener: (type, fn) => { if (type === "click") onClick = fn; },
  };
  globalThis.location = { search, protocol: "http:", host: "localhost" };
  globalThis.window = globalThis;
  globalThis.setInterval = (fn, ms) => { const t = realSetInterval(fn, ms); timers.push(t); return t; };
  await import(`./js/app.js?${tag}`);
  await tickWait(20); // 가짜 소켓이 열리고 첫 묶음이 오기까지
  const fakeServer = globalThis.fake;
  return {
    el,
    fake: fakeServer,
    // data-… 속성이 붙은 버튼을 누른 것처럼
    click: async (dataset) => {
      onClick?.({ target: { closest: () => ({ dataset }) } });
      await tickWait();
    },
    hasClick: () => onClick !== null,
    stop: () => {
      fakeServer.stop();
      timers.forEach(clearInterval);
      globalThis.setInterval = realSetInterval;
    },
  };
}

{
  const app = await bootApp("?fake", "basic");
  const { el } = app;
  assert.equal(el("server .value").textContent, "연결됨");
  assert.equal(el("robots .value").textContent, "4/4대");
  assert.equal((el("robot-list").innerHTML.match(/<li /g) || []).length, 4);
  for (const n of ["MD-1", "MD-2", "MD-3", "MD-4"]) assert.ok(el("robot-list").innerHTML.includes(`>${n}</span>`));
  assert.equal(el("mode").textContent, "평상시");
  assert.equal(el("evacuate").hidden, true);
  globalThis.fake.fire("A");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("mode").textContent, "대피");
  assert.equal(el("evacuate").hidden, false);
  assert.equal(el("level").textContent, "화재");
  assert.equal(el("count-fire").textContent, "1건");
  assert.match(el("alerts").innerHTML, /화재/);
  globalThis.fake.tick(); // 로봇 상태는 다음 state(1초 뒤)에 바뀌어 온다
  assert.match(el("robot-list").innerHTML, /대피 안내/);
  assert.match(el("robot-list").innerHTML, /충전 스테이션/); // C·D는 충전 스테이션에서 대기
  assert.equal(el("map .robots").innerHTML, ""); // 기본 화면에는 로봇 위치 없음
  // 지도: 마커 15개, 구역 5개, 경보·로봇 표시는 없음
  const svg = el("map").innerHTML;
  assert.equal(el("map").viewBox, "40 40 1920 1330");
  for (let n = 1; n <= 15; n++) assert.ok(svg.includes(`>${n}</text>`), `지도에 마커 ${n} 없음`);
  for (const z of ["A1", "A2", "A3", "B1", "B2"]) assert.ok(svg.includes(`>${z}</text>`), `지도에 구역 ${z} 없음`);
  assert.ok(svg.includes("비상구 1") && svg.includes("비상구 2") && svg.includes("충전 스테이션"));
  assert.ok(!/화재|통로 차단|밟힘/.test(svg));
  assert.match(el("time").textContent, /^\d\d:\d\d:\d\d$/);
  // 기본 화면: 경보 상세 칸은 HTML에서 hidden 그대로, 최근 경보는 누를 수 없음
  assert.equal(el("detail").innerHTML, "");
  assert.ok(!el("alerts").innerHTML.includes("data-alert"));
  assert.ok(!app.hasClick());
  app.stop();
}

// ---------- 6) 관리자 화면: 경보 상세와 경보 해제 ----------
{
  const app = await bootApp("?fake&admin", "admin");
  const { el, fake } = app;
  assert.equal(el("detail-panel").hidden, false);
  assert.match(el("detail").innerHTML, /남은 경보가 없습니다/);

  // 경보가 오면 가장 새 경보가 상세에 뜬다
  const ob = fake.emit("obstacle", "A");
  await tickWait();
  let d = el("detail").innerHTML;
  assert.match(d, /통로 차단/);
  assert.match(d, /사진 없음/); // 가짜 로봇은 사진이 없다
  assert.match(d, new RegExp(`data-check="${ob.id}"`));
  assert.match(d, /<dt>사람<\/dt>/);
  assert.match(el("alerts").innerHTML, new RegExp(`data-alert="${ob.id}"`));

  // 다른 경보가 와도, 고른 경보가 상세에 남는다
  const heat = fake.emit("heat", "B");
  await tickWait();
  assert.match(el("detail").innerHTML, new RegExp(`data-check="${heat.id}"`)); // 고른 게 없으면 새것
  await app.click({ alert: String(ob.id) });
  assert.match(el("detail").innerHTML, new RegExp(`data-check="${ob.id}"`));
  assert.match(el("alerts").innerHTML, /class="selected"/);

  // 경보 해제 → 서버에 보내고 목록에서 빠짐
  await app.click({ check: String(ob.id) });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === ob.id).checked, true);
  assert.ok(!el("alerts").innerHTML.includes(`data-alert="${ob.id}"`));
  assert.match(el("detail").innerHTML, new RegExp(`data-check="${heat.id}"`)); // 남은 경보로 넘어감
  assert.match(el("detail").innerHTML, /<dt>온도<\/dt>/);
  assert.ok(!el("detail").innerHTML.includes("신뢰도")); // 과열은 온도로 판단해서 신뢰도를 안 보여 줌

  // 화재: 한 번 더 확인. 취소하면 그대로
  const fire = fake.emit("flame", "C");
  await tickWait();
  await app.click({ check: String(fire.id) });
  assert.match(el("detail").innerHTML, /대피 모드는 따로 해제/);
  assert.equal(fake.events.find((e) => e.id === fire.id).checked, false);
  await app.click({ cancel: "1" });
  assert.ok(!el("detail").innerHTML.includes("대피 모드는 따로 해제"));
  await app.click({ check: String(fire.id) });
  await app.click({ check: String(fire.id), sure: "1" });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === fire.id).checked, true);

  // 경보 해제를 못 보내면 알려 준다 (없는 경보 번호 → 404)
  const heatId = heat.id;
  fake.events.find((e) => e.id === heatId).id = 9999; // 서버 쪽에서만 번호가 바뀐 것처럼
  await app.click({ check: String(heatId) });
  await tickWait();
  assert.match(el("detail").innerHTML, /경보 해제를 보내지 못했습니다 \(서버 응답 404\)/);

  // 지도에 로봇 위치 (화면 이름으로)
  assert.equal((el("map .robots").innerHTML.match(/class="robot"/g) || []).length, 4);
  assert.ok(el("map .robots").innerHTML.includes(">MD-1</text>"));
  assert.match(el("detail").innerHTML, /<dt>로봇<\/dt><dd>MD-\d<\/dd>/);
  app.stop();
}

// ---------- 7) 경보음 ----------
// 소리를 내는 대신 삐 소리를 몇 번 냈는지 센다
let beeps = 0;
class FakeAudioContext {
  currentTime = 0;
  destination = {};
  resume() {}
  createOscillator() {
    return { frequency: {}, connect() {}, start() { beeps += 1; }, stop() {} };
  }
  createGain() {
    return { gain: {}, connect() {} };
  }
}
{
  const { createAlarm } = await import("./js/sound.js");
  const a = createAlarm(FakeAudioContext);
  assert.equal(a.play("flame"), 0); // 켜기 전에는 소리 안 남
  assert.ok(await a.enable());
  assert.equal(a.play("flame"), 3);
  assert.equal(a.play("obstacle"), 2);
  assert.equal(a.play("heat"), 1);
  a.disable();
  assert.equal(a.play("flame"), 0);
  assert.equal(createAlarm(undefined).supported, false);
}

// ---------- 8) 관리자 화면: 경보 팝업, 깜빡임, 경보음 ----------
{
  globalThis.AudioContext = FakeAudioContext;
  const app = await bootApp("?fake&admin", "popup");
  const { el, fake } = app;
  await tickWait(550); // 화면을 연 직후의 첫 묶음 시간이 지나야 새 경보로 친다

  // 경보음 켜기 버튼
  assert.equal(el("sound").textContent, "경보음 켜기");
  await app.click({ sound: "1" });
  assert.equal(el("sound").textContent, "경보음 끄기");
  beeps = 0;

  // 통로 차단 → 팝업 + 경보음 2번
  const ob = fake.emit("obstacle", "A");
  await tickWait();
  assert.equal(el("popup").hidden, false);
  assert.match(el("popup").innerHTML, /통로 차단 경보/);
  assert.match(el("popup").innerHTML, /mini-map/);
  assert.match(el("popup").innerHTML, /stroke="#ff4d4d"/); // 작은 지도에 위치 표시
  assert.match(el("popup").innerHTML, new RegExp(`data-check="${ob.id}"`));
  assert.equal(beeps, 2);

  // 과열 → 팝업 없이 깜빡임 + 경보음 1번. 팝업은 통로 차단 그대로
  const heat = fake.emit("heat", "B");
  await tickWait();
  assert.equal(beeps, 3);
  assert.match(el("popup").innerHTML, /통로 차단 경보/);
  assert.match(el("alerts").innerHTML, new RegExp(`class="[^"]*blink[^"]*"><button type="button" class="alert-btn" data-alert="${heat.id}"`));

  // 팝업 닫기 → 오른쪽 경보 상세에 그 경보
  await app.click({ popupClose: "1" });
  assert.equal(el("popup").hidden, true);
  assert.match(el("detail").innerHTML, new RegExp(`data-check="${ob.id}"`));

  // 과열을 눌러 보면 깜빡임이 멈춘다
  await app.click({ alert: String(heat.id) });
  assert.ok(!el("alerts").innerHTML.includes("blink"));

  // 화재 → 팝업 + 경보음 3번. 팝업에서 한 번 더 확인하고 해제하면 팝업이 닫힌다
  const fire = fake.emit("flame", "C");
  await tickWait();
  assert.equal(beeps, 6);
  assert.match(el("popup").innerHTML, /화재 경보/);
  await app.click({ check: String(fire.id) });
  assert.match(el("popup").innerHTML, /대피 모드는 따로 해제/);
  await app.click({ check: String(fire.id), sure: "1" });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === fire.id).checked, true);
  assert.equal(el("popup").hidden, true);

  // 팝업이 여러 개면 차례로
  fake.emit("obstacle", "A");
  fake.emit("flame", "B");
  await tickWait();
  assert.match(el("popup").innerHTML, /대기 중인 팝업 1건/);
  await app.click({ popupClose: "1" });
  assert.match(el("popup").innerHTML, /화재 경보/);

  // 경보음 끄기 → 소리 안 남
  await app.click({ sound: "1" });
  beeps = 0;
  fake.emit("obstacle", "D");
  await tickWait();
  assert.equal(beeps, 0);

  app.stop();
  delete globalThis.AudioContext;
}

// ---------- 9) 관리자 화면: 대피 해제, 경보 이력 ----------
{
  const app = await bootApp("?fake&admin", "evac");
  const { el, fake } = app;
  await tickWait(550);

  // 대피 해제: 대피 중에만 버튼, 한 번 더 확인
  assert.equal(el("evacuate-actions").innerHTML, "");
  fake.robots.A.role = "A";
  fake.tick(); // A가 A 경로를 맡고 있다는 state
  const fire = fake.fire("A");
  await tickWait();
  assert.match(el("evacuate-actions").innerHTML, /data-evac="1"/);
  await app.click({ evac: "1" });
  assert.match(el("evacuate-actions").innerHTML, /현장 안전을 확인했나요/);
  await app.click({ evacCancel: "1" });
  assert.ok(!el("evacuate-actions").innerHTML.includes("현장 안전을"));
  await app.click({ evac: "1" });
  await app.click({ evac: "1", sure: "1" });
  await tickWait();
  assert.equal(fake.mode, "normal");
  assert.equal(el("evacuate").hidden, true);
  assert.equal(el("mode").textContent, "평상시");

  // 경보 이력: 사진이 있으면 "사진 보기" 버튼, 없으면 "사진 없음"
  assert.match(el("history").innerHTML, /사진 없음/);
  const orig = fake.robots.B.eventFields.bind(fake.robots.B);
  fake.robots.B.eventFields = (t) => ({ ...orig(t), photo: "/photos/B_1123_120000.jpg" });
  fake.emit("obstacle", "B");
  await tickWait();
  assert.match(el("history").innerHTML, /<a class="photo-btn" href="\/photos\/B_1123_120000.jpg" target="_blank"/);

  // 경보 이력: 해제한 경보도 남고, 눌러 보면 상세에 버튼 없이 해제 시각
  assert.match(el("history").innerHTML, new RegExp(`data-alert="${fire.id}"`));
  assert.match(el("history").innerHTML, /해제 전/);
  assert.match(el("history").innerHTML, /A 경로/);
  await app.click({ check: String(fire.id) });
  await app.click({ check: String(fire.id), sure: "1" });
  await tickWait();
  assert.match(el("history").innerHTML, /경보 해제 \d\d:\d\d:\d\d/);
  assert.ok(!el("alerts").innerHTML.includes(`data-alert="${fire.id}"`)); // 최근 경보에서는 빠짐
  await app.click({ alert: String(fire.id) });
  const d = el("detail").innerHTML;
  assert.match(d, /done-text/);
  assert.ok(!d.includes("data-check"));
  assert.match(d, /<dt>경로<\/dt><dd>A 경로<\/dd>/);
  app.stop();
}

// 경보 이력: 해제된 경보도 남고, 해제 시각은 해제 소식을 받은 시각
{
  const st = createStore();
  apply(st, { kind: "event", id: 1, robot: "A", type: "heat", checked: false }, 1000);
  apply(st, { kind: "event", id: 1, robot: "A", type: "heat", checked: true }, 5000);
  const { alertHistory } = await import("./js/store.js");
  const h = alertHistory(st);
  assert.equal(h.length, 1);
  assert.equal(h[0].checkedAt, 5000);
  assert.equal(h[0].event.checked, true);
}

console.log("dashboard selfcheck OK");
