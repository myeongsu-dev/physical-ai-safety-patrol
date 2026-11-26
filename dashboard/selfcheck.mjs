// 대시보드 셀프 체크. 저장소 맨 위에서: node dashboard/selfcheck.mjs
// 1) js/mapdata.js가 server/map_config.py와 같은지
// 2) 가짜 서버가 진짜 서버와 같은 순서·모양으로 메시지를 보내는지
// 3) 로봇 연결 끊김 판단 (js/store.js)
// 4) 안전 현황 단계와 건수
// 5) 화면 파일(js/app.js)이 오류 없이 돌아가는지

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { MARKERS, ROUTES, DOCK_MARKER, BATTERY_RETURN, RETURN_POINT, EXITS } from "./js/mapdata.js";
import { FakeServer, EVENT_TYPES, evacPlan } from "./js/fake.js";
import { createStore, apply, setServer, robotOnline, connectionSummary, safetySummary, openAlerts, robotPlace, robotLabel, evacView, loadHistory, alarmStats, ONLINE_SEC } from "./js/store.js";
import { robotLayer, fireLayer, guideLayer, corridorPoints, MARKER_POS } from "./js/map.js";
import { checkAdmin, withPassword, ADMIN_HEADER } from "./js/auth.js";

// ---------- 1) 지도 데이터 ----------
function readMapConfig() {
  const code =
    "import json; from server import map_config as m, fleet; " +
    "print(json.dumps({'markers': {k: list(v['xy']) for k, v in m.MARKERS.items()}, " +
    "'routes': m.ROUTES, 'dock': m.DOCK_MARKER, 'ret': m.BATTERY_RETURN, 'online': fleet.ONLINE_SEC, " +
    "'retpt': {k: v['marker'] for k, v in m.RETURN_POINT.items()}, 'exits': getattr(m, 'EXITS', None)}))";
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
  assert.deepEqual(RETURN_POINT, cfg.retpt, "mapdata.js RETURN_POINT가 map_config.py와 다름");
  // EXITS는 PR #23부터. 서버 설정이 아직 다르면 대시보드 CI를 막지 않고 알리기만 한다
  if (cfg.exits && JSON.stringify(cfg.exits) !== JSON.stringify(EXITS)) {
    console.log(`알림: 대피 출구가 다름. 대시보드 ${JSON.stringify(EXITS)}, server/map_config.py ${JSON.stringify(cfg.exits)}`);
  }
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
// 진짜 서버처럼 mode 다음에 evac (관리자가 바꾼 것이라 불난 마커는 모름)
assert.deepEqual(a.msgs.at(-2), { kind: "mode", mode: "evacuate" });
assert.deepEqual(a.msgs.at(-1), { kind: "evac", fire: null, exits: EXITS, arrows: evacPlan(null) });
const cSock = fake.socket();
const c = listen(cSock);
flush();
assert.deepEqual(c.msgs.filter((m) => m.kind === "mode" || m.kind === "evac").map((m) => m.kind), ["mode", "evac"], "새로 붙어도 evac을 받음");
cSock.close();
fake.tick();
assert.ok(a.msgs.filter((m) => m.kind === "state").slice(-4).every((m) => m.status === "guide"));
assert.equal((await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "x" }) })).status, 400);
await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
assert.ok(!fake._snapshot().some((m) => m.kind === "evac"), "평상시로 돌아가면 evac 없음");


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
  assert.deepEqual(s.counts, { heat: 0, obstacle: 0, floor: 0 });

  // 안전모 미착용은 단계와 건수에 안 들어감
  apply(st, { kind: "event", id: 1, type: "helmet", checked: false }, T);
  assert.equal(safetySummary(st, T).level, "safe");

  // 통로 차단 · 밟힘 주의 · 과열 → 주의, 건수
  apply(st, { kind: "event", id: 2, type: "obstacle", checked: false }, T);
  apply(st, { kind: "event", id: 3, type: "floor_object", checked: false }, T);
  apply(st, { kind: "event", id: 4, type: "heat", checked: false }, T);
  s = safetySummary(st, T);
  assert.equal(s.level, "caution");
  assert.deepEqual(s.counts, { heat: 1, obstacle: 1, floor: 1 });

  // 해제하면 빠짐
  for (const id of [2, 3, 4]) apply(st, { kind: "event", id, checked: true }, T);
  assert.equal(safetySummary(st, T).level, "safe");

  // 로봇 한 대가 끊기면 주의
  for (const r of ["A", "B", "C"]) apply(st, { kind: "state", robot: r, online: true }, T + 6000);
  s = safetySummary(st, T + 6000);
  assert.equal(s.level, "caution");
  assert.equal(s.offline, 1);

  // 불꽃 감지(flame)는 서버가 아직 확인하는 중이라 주의. 화재는 서버가 대피 모드로 바꿨을 때만
  for (const r of ["A", "B", "C", "D"]) apply(st, { kind: "state", robot: r, online: true }, T + 6000);
  apply(st, { kind: "event", id: 5, type: "flame", checked: false }, T + 6000);
  assert.equal(safetySummary(st, T + 6000).level, "caution");
  assert.equal(safetySummary(st, T + 6000).counts.heat, 1);
  apply(st, { kind: "mode", mode: "evacuate" });
  assert.equal(safetySummary(st, T + 6000).level, "fire");
  apply(st, { kind: "event", id: 5, checked: true });
  assert.equal(safetySummary(st, T + 6000).level, "fire", "경보를 해제해도 대피 중이면 화재");
  apply(st, { kind: "mode", mode: "normal" });
  assert.equal(safetySummary(st, T + 6000).level, "safe");

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

// ---------- 화재 위치와 대피 안내 (서버 evac 메시지) ----------
{
  // 통로 구간: 직선, 꺾이는 곳(줄기), 이어지지 않는 마커
  assert.deepEqual(corridorPoints(5, 6), [MARKER_POS[5], MARKER_POS[6]]);
  assert.deepEqual(corridorPoints(9, 6), [MARKER_POS[9], [MARKER_POS[9][0], MARKER_POS[6][1]], MARKER_POS[6]]);
  assert.equal(corridorPoints(14, 11).length, 3);
  assert.equal(corridorPoints(1, 13), null);
  assert.equal(corridorPoints(1, 99), null);

  // 가짜 서버 대피 경로 (server/dispatch.py plan과 같은 값. python에 dispatch.plan이 있으면 직접 비교)
  const plan7 = evacPlan(7);
  assert.equal(plan7.length, 15);
  assert.deepEqual(plan7.find((a) => a.marker === 7), { marker: 7, dir: "x", exit: null, path: [] }, "불난 마커는 x");
  assert.ok(plan7.every((a) => !a.path.includes(7)), "불난 마커를 지나지 않음");
  assert.deepEqual(plan7.find((a) => a.marker === 15), { marker: 15, dir: "N", exit: 5, path: [15, 14, 11, 10, 5] });
  assert.deepEqual(plan7.find((a) => a.marker === 3), { marker: 3, dir: "E", exit: 8, path: [3, 4, 8] });
  assert.equal(plan7.find((a) => a.marker === 5).dir, "here");
  assert.ok(evacPlan(5).every((a) => a.dir === "x" || a.exit === 8), "5번 출구가 불나면 모두 8번으로");
  assert.ok(evacPlan(null, [15]).every((a) => a.dir === "here" ? a.marker === 15 : a.exit === 15), "출구를 바꿔 넣을 수 있음");
  for (const f of [null, 7]) {
    for (const a of evacPlan(f)) {
      for (let i = 0; i + 1 < a.path.length; i++) assert.ok(corridorPoints(a.path[i], a.path[i + 1]), `${a.path} 지도 통로로 안 이어짐`);
    }
  }
  try {
    const code = `import json; from server import dispatch; print(json.dumps([dispatch.plan(fire=f, exits=${JSON.stringify(EXITS)}) for f in [None, *range(1, 16)]]))`;
    const py = JSON.parse(execFileSync("python", ["-c", code], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }));
    [null, ...Array.from({ length: 15 }, (_, i) => i + 1)].forEach((f, i) => assert.deepEqual(evacPlan(f), py[i], `fire ${f}: dispatch.plan과 다름`));
  } catch (e) {
    if (e instanceof assert.AssertionError) throw e; // dispatch.plan이 없는 서버(PR #23 전)면 건너뜀
  }

  // 화재 층: 마커 둘레, 모르는 마커는 안 그림
  assert.match(fireLayer(7), /class="fire-marker" cx="1220" cy="553"/);
  assert.match(fireLayer(7), /화재/);
  assert.equal(fireLayer(null), "");
  assert.equal(fireLayer(99), "");

  // 대피 층: 화살표는 N·E·S·W만, 같은 통로는 한 번, 출구 표시, 불 아닌 x는 갈 수 없음 표시
  let svg = guideLayer({ fire: 7, exits: EXITS, arrows: plan7 });
  assert.equal((svg.match(/class="guide-arrow"/g) || []).length, 12, "15개 중 불난 7(x)과 출구 5·8(here) 빼고");
  const edges = new Set(plan7.flatMap((a) => a.path.slice(1).map((m, i) => [a.path[i], m].sort((x, y) => x - y).join("-"))));
  assert.equal((svg.match(/class="guide-path"/g) || []).length, edges.size + 2, "같은 통로는 한 번만, 출구 두 곳은 문까지 한 줄씩 더");
  assert.equal((svg.match(/class="guide-exit"/g) || []).length, 2);
  assert.ok(svg.includes("340,553 258,553") && svg.includes("1650,553 1738,553"), "출구에서 비상구 문까지");
  assert.equal(svg.match(/guide-blocked/), null, "불난 마커는 화재 층이 그림");
  svg = guideLayer({ fire: null, exits: [5, 8], arrows: [{ marker: 6, dir: "W", exit: 5, path: [6, 5] }, { marker: 3, dir: "x", exit: null, path: [] }] });
  assert.ok(svg.includes("340,553 258,553"), "5번 출구는 비상구 1 문까지");
  assert.ok(svg.includes("1650,553 1738,553"), "8번 출구는 비상구 2 문까지");
  assert.equal((svg.match(/guide-blocked/g) || []).length, 1, "갈 수 없는 마커");
  svg = guideLayer({ exits: [], arrows: [{ marker: 99, dir: "N" }, { marker: 3, dir: "Q" }, { marker: 2, dir: "here", path: [2, 13] }] });
  assert.equal(svg, "", "없는 마커·모르는 방향·이어지지 않는 길은 안 그림");
  assert.equal(guideLayer(null), "");

  // 화면에 넘길 값: 대피 모드일 때 받은 evac만. 불꽃 감지 경보로는 그리지 않는다
  const st = createStore();
  apply(st, { kind: "event", id: 1, robot: "A", type: "flame", marker: 5, checked: false });
  assert.equal(evacView(st), null);
  apply(st, { kind: "mode", mode: "evacuate" });
  assert.equal(evacView(st), null, "evac이 오기 전");
  apply(st, { kind: "evac", fire: 7, exits: EXITS, arrows: plan7 });
  assert.deepEqual(evacView(st), { fire: 7, exits: EXITS, arrows: plan7 });
  apply(st, { kind: "mode", mode: "normal" });
  assert.equal(evacView(st), null);
  assert.equal(st.evac, null, "평상시로 돌아가면 지움");
  apply(st, { kind: "mode", mode: "evacuate" });
  apply(st, { kind: "evac", fire: null, exits: "?", arrows: null });
  assert.deepEqual(evacView(st), { fire: null, exits: [], arrows: [] }, "이상한 값은 빈 값으로");
}

// ---------- 5) 화면 파일이 오류 없이 돌아가는지 ----------
// 브라우저 대신 아주 작은 가짜 document를 두고 js/app.js를 ?fake로 불러 본다
const realSetInterval = globalThis.setInterval;
const tickWait = (ms = 0) => new Promise((r) => setTimeout(r, ms));

// search: 주소 뒤 ?… 부분. 같은 app.js를 두 번 띄우려고 import 주소에 tag를 붙인다
// login: 관리자 잠금 시험용. app.js가 비밀번호를 기다리는 동안 부른다 (el을 받음)
async function bootApp(search, tag, { login } = {}) {
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
  const loading = import(`./js/app.js?${tag}`);
  if (login) await login(el);
  await loading;
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
  // 기본 화면의 관리자 화면 버튼: ?fake는 두고 admin을 붙인다
  assert.equal(el("view-switch").textContent, "관리자 화면");
  assert.equal(el("view-switch").href, "./?fake&admin");
  assert.equal(el("server .value").textContent, "연결됨");
  assert.equal(el("robots .value").textContent, "4/4대");
  assert.equal(el("robot-list").innerHTML, ""); // 로봇 현황 카드는 관리자 화면에만 (칸은 HTML에서 hidden 그대로)
  assert.equal(el("mode").textContent, "평상시");
  assert.equal(el("evacuate").hidden, true);
  globalThis.fake.fire("A");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("mode").textContent, "대피");
  assert.equal(el("evacuate").hidden, false);
  assert.equal(el("level").textContent, "화재");
  assert.equal(el("count-heat").textContent, "2건"); // 로봇이 보낸 과열 + 불꽃 감지
  assert.match(el("alerts").innerHTML, /불꽃 감지/);
  assert.ok(!/>화재</.test(el("alerts").innerHTML), "경보 목록에 화재라고 쓰지 않음");
  globalThis.fake.tick();
  assert.equal(el("robot-list").innerHTML, "");
  assert.equal(el("map .robots").innerHTML, ""); // 기본 화면에는 로봇 위치 없음
  // 화재 위치·대피 안내: 서버가 대피로 바꾸면 evac도 오고, 기본 화면에도 그린다
  assert.match(el("map .fire").innerHTML, /화재/);
  assert.equal((el("map .guides").innerHTML.match(/class="guide-exit"/g) || []).length, 2);
  const placed = globalThis.fake.fire("B", 7);
  await new Promise((r) => setTimeout(r, 0));
  assert.match(el("map .fire").innerHTML, /cx="1220" cy="553"/);
  assert.equal((el("map .guides").innerHTML.match(/class="guide-arrow"/g) || []).length, 12);
  await globalThis.fake.fetch(`/events/${placed.id}/check`, { method: "POST" });
  await globalThis.fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("map .fire").innerHTML, "");
  assert.equal(el("map .guides").innerHTML, "");
  // 관리자가 직접 대피로 바꾸면 불난 마커는 모름: 대피 안내만
  await globalThis.fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate" }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("map .fire").innerHTML, "");
  assert.equal((el("map .guides").innerHTML.match(/class="guide-arrow"/g) || []).length, 13);
  await globalThis.fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  globalThis.fake.fire("A"); // 아래 지도 확인은 대피 중 그대로
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
  // 로봇 현황 카드 4대
  assert.equal(el("robot-panel").hidden, false);
  assert.equal((el("robot-list").innerHTML.match(/<li /g) || []).length, 4);
  for (const n of ["MD-1", "MD-2", "MD-3", "MD-4"]) assert.ok(el("robot-list").innerHTML.includes(`>${n}</span>`));
  // 로봇 카드 맨 아래 프로그램 버전 (가짜 로봇은 "fake")
  assert.equal((el("robot-list").innerHTML.match(/<div class="version">버전 fake<\/div>/g) || []).length, 4);

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

  // 불꽃 감지: 다른 경보처럼 바로 해제. 화재가 아니라서 대피 모드도 안 됨
  const flame = fake.emit("flame", "C");
  await tickWait();
  assert.match(el("detail").innerHTML, /불꽃 감지/);
  assert.equal(el("evacuate").hidden, true);
  await app.click({ check: String(flame.id) });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === flame.id).checked, true);

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
  assert.equal(a.play("evacuate"), 0); // 켜기 전에는 소리 안 남
  assert.ok(await a.enable());
  assert.equal(a.play("evacuate"), 3);
  assert.equal(a.play("obstacle"), 2);
  assert.equal(a.play("heat"), 1);
  assert.equal(a.play("flame"), 1);
  a.disable();
  assert.equal(a.play("evacuate"), 0);
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

  // 불꽃 감지 → 팝업 없이 깜빡임 + 경보음 1번. 화재가 아님
  const flame = fake.emit("flame", "C");
  await tickWait();
  assert.equal(beeps, 4);
  assert.equal(el("popup").hidden, true);
  assert.equal(el("evacuate").hidden, true);
  assert.match(el("alerts").innerHTML, new RegExp(`class="[^"]*blink[^"]*"><button type="button" class="alert-btn" data-alert="${flame.id}"`));

  // 화재 확정(서버가 대피 모드로 바꿈) → 경보음 3번, 대피 띠에 "경보음 멈춤"
  beeps = 0;
  fake.fire("D");
  await tickWait();
  assert.equal(beeps, 1 + 1 + 3); // 과열, 불꽃 감지, 대피
  assert.equal(el("evacuate").hidden, false);
  assert.equal(el("popup").hidden, true);
  assert.match(el("evacuate-actions").innerHTML, /data-evac-mute="1"/);
  await app.click({ evacMute: "1" });
  assert.ok(!el("evacuate-actions").innerHTML.includes("data-evac-mute"));
  await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  await tickWait();

  // 팝업이 여러 개면 차례로
  fake.emit("obstacle", "A");
  fake.emit("obstacle", "B");
  await tickWait();
  assert.match(el("popup").innerHTML, /대기 중인 팝업 1건/);
  await app.click({ popupClose: "1" });
  assert.match(el("popup").innerHTML, /통로 차단 경보/);
  assert.ok(!el("popup").innerHTML.includes("대기 중인 팝업"));

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
  fake.tick(); // 로봇 상태는 다음 state(1초 뒤)에 바뀌어 온다
  assert.match(el("robot-list").innerHTML, /대피 안내/);
  assert.match(el("robot-list").innerHTML, /충전 스테이션/); // C·D는 충전 스테이션에서 대기
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
  assert.match(el("history").innerHTML, /<a class="photo-btn" href="\/photos\/B_1123_120000.jpg" data-photo="\/photos\/B_1123_120000.jpg"/);
  // 사진 보기: 다른 페이지가 아니라 대시보드 위에 띄우고 닫기로 닫는다
  await app.click({ photo: "/photos/B_1123_120000.jpg" });
  assert.equal(el("viewer").hidden, false);
  assert.match(el("viewer").innerHTML, /<img src="\/photos\/B_1123_120000.jpg"/);
  await app.click({ viewerClose: "1" });
  assert.equal(el("viewer").hidden, true);

  // 경보 이력: 해제한 경보도 남고, 눌러 보면 상세에 버튼 없이 "경보 해제"
  assert.match(el("history").innerHTML, new RegExp(`data-alert="${fire.id}"`));
  assert.match(el("history").innerHTML, /해제 전/);
  assert.match(el("history").innerHTML, /A 경로/);
  await app.click({ check: String(fire.id) });
  await tickWait();
  assert.match(el("history").innerHTML, /<td class="state done">경보 해제 <span>\d\d:\d\d:\d\d<\/span><\/td>/);
  assert.ok(!el("alerts").innerHTML.includes(`data-alert="${fire.id}"`)); // 최근 경보에서는 빠짐
  await app.click({ alert: String(fire.id) });
  const d = el("detail").innerHTML;
  assert.match(d, /<p class="done-text">경보 해제<\/p>/);
  assert.ok(!d.includes("data-check"));
  assert.match(d, /<dt>경로<\/dt><dd>A 경로<\/dd>/);
  app.stop();
}

// ---------- 관리자 잠금 (server/api.py, PR #23) ----------
{
  // 서버에 묻기: 맞음, 틀림, 잠금 없는 예전 서버(404), 그 밖
  const answer = (status) => async () => ({ ok: status === 200, status });
  assert.equal(await checkAdmin(answer(200), "x"), "ok");
  assert.equal(await checkAdmin(answer(401), "x"), "wrong");
  assert.equal(await checkAdmin(answer(404), ""), "ok", "/admin/check가 없는 서버는 잠금 없음");
  assert.equal(await checkAdmin(answer(500), "x"), "error");
  assert.equal(await checkAdmin(async () => { throw new Error("끊김"); }, "x"), "error");
  let sent = null;
  await checkAdmin(async (path, opts) => { sent = { path, opts }; return { ok: true, status: 200 }; }, "pw");
  assert.deepEqual(sent, { path: "/admin/check", opts: { headers: { [ADMIN_HEADER]: "pw" } } });
  assert.deepEqual(withPassword({ method: "POST", headers: { "Content-Type": "application/json" } }, "pw").headers,
    { "Content-Type": "application/json", [ADMIN_HEADER]: "pw" });
  assert.deepEqual(withPassword({ method: "POST" }, ""), { method: "POST" }, "비밀번호가 없으면 헤더 안 붙임");

  // 가짜 서버도 진짜 서버처럼: 바꾸는 요청만 잠그고 보기는 열어 둠
  const locked = new FakeServer({ seed: 1, adminPassword: "pw", defer: (fn) => fn() });
  const ok = { headers: { [ADMIN_HEADER]: "pw" } };
  assert.equal((await locked.fetch("/admin/check")).status, 401);
  assert.equal((await locked.fetch("/admin/check", ok)).status, 200);
  assert.equal((await locked.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate" }) })).status, 401);
  assert.equal(locked.mode, "normal");
  assert.equal((await locked.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate" }), ...ok })).status, 200);
  const e = locked.emit("heat", "A");
  assert.equal((await locked.fetch(`/events/${e.id}/check`, { method: "POST" })).status, 401);
  assert.equal((await locked.fetch(`/events/${e.id}/check`, { method: "POST", ...ok })).status, 200);
  assert.equal((await locked.fetch("/status")).status, 200);
  assert.equal((await new FakeServer({ seed: 1 }).fetch("/admin/check")).status, 200, "비밀번호가 없으면 잠그지 않음");

  // 화면: 잠긴 서버면 비밀번호를 맞힐 때까지 관리자 화면을 열지 않는다
  delete globalThis.document?.title;
  const submit = (el, pw) => {
    el("login-password").value = pw;
    return el("login-form").onsubmit({ preventDefault() {} });
  };
  const app = await bootApp("?fake&admin&lock", "lock", {
    login: async (el) => {
      await tickWait(20);
      assert.equal(el("login").hidden, false, "비밀번호 칸");
      assert.equal(globalThis.document.title, undefined, "관리자 화면은 아직 안 엶");
      assert.equal(el("login-back").href, "./?fake&lock", "기본 화면으로 (admin만 뺌)");
      assert.equal(el("view-switch").textContent, "기본 화면으로");
      await submit(el, "nope");
      assert.equal(el("login-error").textContent, "비밀번호가 맞지 않습니다.");
      assert.equal(el("login-error").hidden, false);
      assert.equal(el("login").hidden, false);
      await submit(el, "1234");
    },
  });
  const { el, fake } = app;
  assert.equal(el("login").hidden, true);
  assert.match(globalThis.document.title, /관리자/);
  assert.equal(el("view-switch").href, "./?fake&lock");

  // 비밀번호를 붙여 보내므로 경보 해제가 됨
  const ob = fake.emit("obstacle", "A");
  await tickWait();
  await app.click({ check: String(ob.id) });
  await tickWait();
  assert.equal(fake.events.find((x) => x.id === ob.id).checked, true);

  // 서버 비밀번호가 바뀌면 401 → 해제 안 됨, 다시 묻는다. 새 비밀번호를 넣으면 다시 됨
  fake.adminPassword = "5678";
  const ob2 = fake.emit("obstacle", "B");
  await tickWait();
  await app.click({ check: String(ob2.id) });
  await tickWait();
  assert.equal(fake.events.find((x) => x.id === ob2.id).checked, false);
  assert.equal(el("login").hidden, false);
  assert.match(el("login-error").textContent, /바뀌었습니다/);
  assert.match(el("detail").innerHTML, /관리자 비밀번호가 맞지 않음/);
  await submit(el, "5678");
  await tickWait();
  assert.equal(el("login").hidden, true);
  await app.click({ check: String(ob2.id) });
  await tickWait();
  assert.equal(fake.events.find((x) => x.id === ob2.id).checked, true);
  const { loadPassword } = await import("./js/auth.js");
  assert.equal(loadPassword(), "5678");
  el("view-switch").onclick();
  assert.equal(loadPassword(), "", "관리자 화면에서 나가면 비밀번호를 지움");
  app.stop();
}

// ---------- 오탐으로 해제 (PR #23: POST /events/{id}/check body {"false_alarm": true}) ----------
{
  // 가짜 서버: 진짜 서버처럼 정탐·오탐 기록, 이미 닫힌 경보는 그대로
  const f = new FakeServer({ seed: 3, defer: (fn) => fn() });
  const a1 = f.emit("obstacle", "A");
  const a2 = f.emit("heat", "B");
  assert.equal(a1.false_alarm, null);
  assert.equal(a1.checked_at, null);
  const post = (id, body) => f.fetch(`/events/${id}/check`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
  const r1 = await (await post(a1.id)).json();
  assert.equal(r1.checked, true);
  assert.equal(r1.false_alarm, false, "body 없으면 정탐");
  assert.equal(typeof r1.checked_at, "number");
  assert.equal((await (await post(a2.id, { false_alarm: true })).json()).false_alarm, true);
  assert.equal((await (await post(a1.id, { false_alarm: true })).json()).false_alarm, false, "이미 닫힌 건 그대로");

  // 화면: 경보 상세에 경보 해제 · 오탐 두 버튼, 오탐을 누르면 서버에 오탐으로 기록되고 이력에 "오탐으로 해제"
  const app = await bootApp("?fake&admin", "false-alarm");
  const { el, fake } = app;
  const ob = fake.emit("obstacle", "C");
  await tickWait();
  await app.click({ alert: String(ob.id) });
  const d = el("detail").innerHTML;
  assert.match(d, new RegExp(`data-check="${ob.id}">경보 해제</button>`));
  assert.match(d, new RegExp(`data-check="${ob.id}" data-false-alarm="1">오탐</button>`));
  await app.click({ check: String(ob.id), falseAlarm: "1" });
  await tickWait();
  const sent = fake.events.find((x) => x.id === ob.id);
  assert.equal(sent.checked, true);
  assert.equal(sent.false_alarm, true);
  assert.match(el("history").innerHTML, /<td class="state done false-alarm">오탐으로 해제 <span>\d\d:\d\d:\d\d<\/span><\/td>/);
  await app.click({ alert: String(ob.id) });
  assert.match(el("detail").innerHTML, /<p class="done-text">오탐으로 해제<\/p>/);
  // 정탐은 그대로 "경보 해제"
  const ob2 = fake.emit("floor_object", "D");
  await tickWait();
  await app.click({ check: String(ob2.id) });
  await tickWait();
  assert.equal(fake.events.find((x) => x.id === ob2.id).false_alarm, false);
  assert.match(el("history").innerHTML, /<td class="state done">경보 해제 <span>\d\d:\d\d:\d\d<\/span><\/td>/);
  app.stop();
}

// ---------- 지난 경보 목록과 정탐률 (PR #23: GET /events) ----------
{
  // 합치기: 지난 경보는 이력에만, 해제 안 된 목록(events)은 그대로. 이미 해제로 받은 것은 되돌리지 않음
  const st = createStore();
  apply(st, { kind: "event", id: 5, robot: "A", type: "heat", checked: false });
  apply(st, { kind: "event", id: 5, robot: "A", type: "heat", checked: true, checked_at: 200, false_alarm: true });
  loadHistory(st, [
    { kind: "event", id: 5, robot: "A", type: "heat", checked: false, checked_at: null, false_alarm: null },
    { kind: "event", id: 4, robot: "B", type: "obstacle", checked: true, checked_at: 100, false_alarm: false },
    { kind: "event", id: 3, robot: "C", type: "obstacle", checked: true, checked_at: 90, false_alarm: true },
    { kind: "event", id: 2, robot: "D", type: "helmet", checked: true, false_alarm: true },
    { kind: "event", id: 1, robot: "A", type: "flame", checked: false, false_alarm: null },
    { id: "x" }, null,
  ]);
  assert.equal(st.events.size, 0, "지난 경보는 해제 안 된 목록에 넣지 않음");
  assert.equal(st.history.get(5).event.checked, true);
  assert.equal(st.history.get(5).event.false_alarm, true);
  assert.equal(st.history.get(4).role, null);
  assert.equal(st.history.size, 5);
  loadHistory(st, "nope");

  // 정탐률: 해제한 것만, 안전모 미착용 빼고
  const { rows, total } = alarmStats(st);
  const ob = rows.find((r) => r.type === "obstacle");
  assert.deepEqual([ob.closed, ob.falseAlarm, ob.real, ob.rate], [2, 1, 1, 0.5]);
  const heat = rows.find((r) => r.type === "heat");
  assert.deepEqual([heat.closed, heat.real, heat.rate], [1, 0, 0]);
  assert.equal(rows.find((r) => r.type === "flame").rate, null, "해제한 게 없으면 null");
  assert.deepEqual([total.closed, total.falseAlarm, total.real], [3, 2, 1]);
  assert.ok(!rows.some((r) => r.type === "helmet"));

  // 가짜 서버 GET /events: 최근 것부터, limit · before · type
  const f = new FakeServer({ seed: 5, past: 30, defer: (fn) => fn() });
  const get = async (q) => (await f.fetch(`/events${q}`)).json();
  const first = await get("?limit=10");
  assert.equal(first.length, 10);
  assert.deepEqual(first.map((e) => e.id), [30, 29, 28, 27, 26, 25, 24, 23, 22, 21]);
  assert.ok(first.every((e) => e.checked && typeof e.false_alarm === "boolean" && e.checked_at > e.ts));
  assert.equal((await get("?limit=10&before=5")).length, 4);
  assert.ok((await get("?type=heat")).every((e) => e.type === "heat"));
  assert.equal((await get("")).length, 30);
  assert.equal(f._openEvents().length, 0, "지난 경보는 붙자마자 받는 묶음에 없음");

  // 화면: 관리자 화면을 열면 지난 경보를 받아 이력과 정탐률을 보여 주고, 50건씩 더 보기
  const app = await bootApp("?fake&admin&past", "past");
  const { el, fake } = app;
  await tickWait(20);
  assert.equal(el("history-sub").textContent, "서버에 기록된 경보, 최근 것부터");
  const rowsShown = () => (el("history").innerHTML.match(/<tr /g) || []).length;
  const want = fake.events.filter((e) => e.type !== "helmet").length;
  assert.equal(rowsShown(), Math.min(50, want));
  assert.equal(el("stats-panel").hidden, false);
  assert.match(el("stats").innerHTML, /<th scope="row">전체<\/th>/);
  assert.match(el("stats").innerHTML, /<th scope="row">통로 차단<\/th>/);
  // 지난 경보가 50건 안 되면 더 보기 없음 (가짜는 40건 중 안전모 빼고)
  if (want <= 50) assert.equal(el("history-more").innerHTML, "");
  // 오탐으로 해제하면 정탐률에 바로 들어감
  const before = alarmStats({ history: new Map(fake.events.map((e) => [e.id, { event: e }])) }).total.closed;
  const ob2 = fake.emit("obstacle", "A");
  await tickWait();
  await app.click({ check: String(ob2.id), falseAlarm: "1" });
  await tickWait();
  const totalText = el("stats").innerHTML.match(/<th scope="row">전체<\/th><td>[^<]*<\/td>\s*<td class="muted">정탐 (\d+) · 오탐 (\d+) \/ (\d+)건/);
  assert.equal(Number(totalText[3]), before + 1);
  assert.equal(Number(totalText[1]) + Number(totalText[2]), Number(totalText[3]), "정탐 + 오탐 = 해제");
  app.stop();

  // 더 보기: 50건 넘으면 버튼, 누르면 50건 더 (가짜 서버가 120건)
  {
    const app2 = await bootApp("?fake&admin", "more");
    const f2 = app2.fake;
    f2._seedPast(120);
    await app2.click({ pastRetry: "1" }); // 다시 받기와 같은 길로 첫 쪽을 다시 받음
    await tickWait(20);
    const n = f2.events.filter((e) => e.type !== "helmet").length;
    assert.equal((app2.el("history").innerHTML.match(/<tr /g) || []).length, 50);
    assert.match(app2.el("history-more").innerHTML, /data-more="1">더 보기/);
    await app2.click({ more: "1" });
    await tickWait(20);
    assert.equal((app2.el("history").innerHTML.match(/<tr /g) || []).length, Math.min(100, n));
    app2.stop();
  }

  // GET /events가 없는 예전 서버(404): 이 화면을 연 뒤 받은 경보만, 안내 글
  {
    const app3 = await bootApp("?fake&admin", "old");
    const f3 = app3.fake;
    const orig = f3.fetch.bind(f3);
    f3.fetch = async (path, opts) => (/^\/events(\?|$)/.test(path) ? { ok: false, status: 404, json: async () => ({}) } : orig(path, opts));
    await app3.click({ pastRetry: "1" });
    await tickWait(20);
    assert.match(app3.el("history-sub").textContent, /서버가 지난 경보를 주지 않음/);
    assert.equal(app3.el("history-more").innerHTML, "");
    app3.stop();
  }
}

// 경보 이력: 해제된 경보도 남는다. 해제 시각은 두지 않는다
{
  const st = createStore();
  apply(st, { kind: "event", id: 1, robot: "A", type: "heat", checked: false }, 1000);
  apply(st, { kind: "event", id: 1, robot: "A", type: "heat", checked: true }, 5000);
  const { alertHistory } = await import("./js/store.js");
  const h = alertHistory(st);
  assert.equal(h.length, 1);
  assert.equal(h[0].event.checked, true);
  assert.ok(!("checkedAt" in h[0]));
}

console.log("dashboard selfcheck OK");
