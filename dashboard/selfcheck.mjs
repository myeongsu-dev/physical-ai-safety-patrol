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
import { createStore, apply, setServer, robotOnline, connectionSummary, safetySummary, openAlerts, robotPlace, robotLabel, evacView, loadHistory, alarmStats, robotStop, robotWaiting, helmetLog, ONLINE_SEC } from "./js/store.js";
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
  apply(st, { kind: "state", robot: "A", marker: DOCK_MARKER, online: true }, T);
  apply(st, { kind: "state", robot: "B", marker: DOCK_MARKER, online: true }, T);
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

  // 불꽃 감지(flame)는 관리자가 화재로 확정하기 전이라 주의. 화재는 대피 모드가 됐을 때만
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
assert.equal(robotPlace({ marker: DOCK_MARKER, status: "idle", role: null }), "충전 스테이션");
assert.equal(robotPlace({ marker: 14, status: "dock" }), "마커 14");
assert.equal(robotPlace(undefined), "-");
// 화면 이름: A~D → MD-1~MD-4, 모르는 이름은 그대로
assert.deepEqual(["A", "B", "C", "D", "E"].map(robotLabel), ["MD-1", "MD-2", "MD-3", "MD-4", "E"]);
assert.deepEqual(Object.keys(MARKER_POS).map(Number), Object.keys(MARKERS).map(Number)); // 지도 마커 번호 = map_config 번호
{
  const svg = robotLayer([
    { name: "A", marker: 8, online: true },
    { name: "B", marker: DOCK_MARKER, online: true },
    { name: "D", marker: DOCK_MARKER, online: false },
    { name: "X", marker: 99, online: true }, // 없는 마커는 안 그림
  ]);
  assert.equal((svg.match(/class="robot"/g) || []).length, 3);
  assert.ok(svg.includes(">A</text>") && svg.includes(">D</text>") && !svg.includes(">X</text>"));
  assert.ok(svg.includes("stroke-dasharray"), "끊긴 로봇은 점선");
  // 같은 마커(충전 스테이션)에 두 대면 x가 다르다
  const xs = [...svg.matchAll(/<rect x="([\d.-]+)" y="[\d.-]+" width="120"/g)].map((m) => Number(m[1]));
  assert.equal(new Set(xs).size, 3);
}

// ---------- 화재 위치와 대피 안내 (서버 evac 메시지) ----------
{
  // 통로 구간: 직선, 꺾이는 곳(줄기), 이어지지 않는 마커
  assert.deepEqual(corridorPoints(5, 6), [MARKER_POS[5], MARKER_POS[6]]);
  assert.deepEqual(corridorPoints(1, 5), [MARKER_POS[1], MARKER_POS[5]], "세로 직선");
  assert.equal(corridorPoints(1, 3), null, "가운데 2번을 지나면 이웃 아님");
  assert.equal(corridorPoints(1, 99), null);
  assert.equal(corridorPoints(1, 1), null);
  // 순찰 경로의 이웃한 마커는 모두 지도 통로로 이어진다 (지도 그림과 순찰 경로가 맞는지)
  for (const [role, r] of Object.entries(ROUTES)) {
    const seqs = [[...r.entry, r.loop[0]], [...r.loop, r.loop[0]], [RETURN_POINT[role], ...r.back]];
    for (const seq of seqs) {
      for (let i = 0; i + 1 < seq.length; i++) {
        if (seq[i] !== seq[i + 1]) assert.ok(corridorPoints(seq[i], seq[i + 1]), `${role} 경로 ${seq[i]}–${seq[i + 1]}가 지도 통로로 안 이어짐`);
      }
    }
  }

  // 가짜 서버 대피 경로 (server/dispatch.py plan과 같은 값. python에 dispatch.plan이 있으면 직접 비교)
  const plan7 = evacPlan(7);
  const N = Object.keys(MARKERS).length;
  assert.equal(plan7.length, N);
  assert.deepEqual(plan7.find((a) => a.marker === 7), { marker: 7, dir: "x", exit: null, path: [] }, "불난 마커는 x");
  assert.ok(plan7.every((a) => !a.path.includes(7)), "불난 마커를 지나지 않음");
  for (const a of plan7) {
    if (a.dir === "x" || a.dir === "here") continue;
    assert.ok(a.path[0] === a.marker && a.path.at(-1) === a.exit && EXITS.includes(a.exit), `${a.marker}번 길이 출구로 안 감`);
  }
  for (const e of EXITS) assert.equal(plan7.find((a) => a.marker === e).dir, "here");
  const [e1, e2] = EXITS;
  assert.ok(evacPlan(e1).every((a) => a.dir === "x" || a.exit === e2), `${e1}번 출구가 불나면 모두 ${e2}번으로`);
  assert.ok(evacPlan(null, [DOCK_MARKER]).every((a) => a.dir === "here" ? a.marker === DOCK_MARKER : a.exit === DOCK_MARKER), "출구를 바꿔 넣을 수 있음");
  for (const f of [null, 7]) {
    for (const a of evacPlan(f)) {
      for (let i = 0; i + 1 < a.path.length; i++) assert.ok(corridorPoints(a.path[i], a.path[i + 1]), `${a.path} 지도 통로로 안 이어짐`);
    }
  }
  try {
    const code = `import json; from server import dispatch; print(json.dumps([dispatch.plan(fire=f, exits=${JSON.stringify(EXITS)}) for f in [None, *range(1, ${N + 1})]]))`;
    const py = JSON.parse(execFileSync("python", ["-c", code], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }));
    [null, ...Array.from({ length: N }, (_, i) => i + 1)].forEach((f, i) => assert.deepEqual(evacPlan(f), py[i], `fire ${f}: dispatch.plan과 다름`));
  } catch (e) {
    if (e instanceof assert.AssertionError) throw e; // dispatch.plan이 없는 서버(PR #23 전)면 건너뜀
  }

  // 화재 층: 마커 둘레, 모르는 마커는 안 그림
  assert.ok(fireLayer(7).includes(`class="fire-marker" cx="${MARKER_POS[7][0]}" cy="${MARKER_POS[7][1]}"`));
  assert.match(fireLayer(7), /화재/);
  assert.equal(fireLayer(null), "");
  assert.equal(fireLayer(99), "");

  // 대피 층: 화살표는 N·E·S·W만, 같은 통로는 한 번, 출구 표시, 불 아닌 x는 갈 수 없음 표시
  let svg = guideLayer({ fire: 7, exits: EXITS, arrows: plan7 });
  assert.equal((svg.match(/class="guide-arrow"/g) || []).length, N - 1 - EXITS.length, "불난 7(x)과 출구(here) 빼고 마커마다");
  const edges = new Set(plan7.flatMap((a) => a.path.slice(1).map((m, i) => [a.path[i], m].sort((x, y) => x - y).join("-"))));
  assert.equal((svg.match(/class="guide-path"/g) || []).length, edges.size + 2, "같은 통로는 한 번만, 출구 두 곳은 문까지 한 줄씩 더");
  assert.equal((svg.match(/class="guide-exit"/g) || []).length, 2);
  assert.ok(svg.includes("340,553 258,553") && svg.includes("1650,553 1738,553"), "출구에서 비상구 문까지");
  assert.equal(svg.match(/guide-blocked/), null, "불난 마커는 화재 층이 그림");
  svg = guideLayer({ fire: null, exits: EXITS, arrows: [{ marker: 6, dir: "W", exit: 5, path: [6, 5] }, { marker: 3, dir: "x", exit: null, path: [] }] });
  assert.ok(svg.includes("340,553 258,553"), "비상구 1 옆 출구는 문까지");
  assert.ok(svg.includes("1650,553 1738,553"), "비상구 2 옆 출구는 문까지");
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
  assert.equal(el("view-switch").textContent, "관리자모드");
  assert.equal(el("view-switch").href, "./?fake&admin");
  // 다크 모드 버튼: 누르면 해 아이콘 · "밝은 화면으로", 다시 누르면 달 · "다크 모드"
  assert.equal(el("theme").title, "다크 모드");
  await app.click({ themeToggle: "1" });
  assert.equal(el("theme").title, "밝은 화면으로");
  assert.equal(el("theme")["aria-pressed"], "true");
  assert.match(el("theme").innerHTML, /<circle/);
  await app.click({ themeToggle: "1" });
  assert.equal(el("theme").title, "다크 모드");
  assert.equal(el("theme")["aria-pressed"], "false");
  assert.equal(el("conn-status .value").textContent, "연결 정상 · 로봇 4/4");
  assert.equal(el("robot-list").innerHTML, ""); // 로봇 현황 카드는 관리자 화면에만 (칸은 HTML에서 hidden 그대로)
  assert.equal(el("mode").textContent, "평상시");
  assert.equal(el("evacuate").hidden, true);
  globalThis.fake.fire("A");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("mode").textContent, "대피");
  assert.equal(el("evacuate").hidden, false);
  assert.equal(el("level").textContent, "화재");
  // 요약 줄: 기본 화면은 경보 4칸만 (로봇 칸 없음). 로봇이 보낸 과열 + 불꽃 감지
  assert.match(el("kpis").innerHTML, /<span class="kpi-label">과열<\/span><b>1<\/b>/);
  assert.match(el("kpis").innerHTML, /<span class="kpi-label">불꽃 감지<\/span><b>1<\/b>/);
  assert.match(el("kpis").innerHTML, /<span class="kpi-label">통로 차단<\/span><b>0<\/b>/);
  assert.ok(!el("kpis").innerHTML.includes("순찰 중"));
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
  assert.ok(el("map .fire").innerHTML.includes(`cx="${MARKER_POS[7][0]}" cy="${MARKER_POS[7][1]}"`));
  assert.equal((el("map .guides").innerHTML.match(/class="guide-arrow"/g) || []).length, Object.keys(MARKERS).length - 1 - EXITS.length);
  await globalThis.fake.fetch(`/events/${placed.id}/check`, { method: "POST" });
  await globalThis.fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("map .fire").innerHTML, "");
  assert.equal(el("map .guides").innerHTML, "");
  // 관리자가 직접 대피로 바꾸면 불난 마커는 모름: 대피 안내만
  await globalThis.fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate" }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(el("map .fire").innerHTML, "");
  assert.equal((el("map .guides").innerHTML.match(/class="guide-arrow"/g) || []).length, Object.keys(MARKERS).length - EXITS.length);
  await globalThis.fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  globalThis.fake.fire("A"); // 아래 지도 확인은 대피 중 그대로
  // 지도: 마커 전부, 구역 5개, 경보·로봇 표시는 없음
  const svg = el("map").innerHTML;
  assert.equal(el("map").viewBox, "40 40 1920 1330");
  for (const n of Object.keys(MARKERS)) assert.ok(svg.includes(`>${n}</text>`), `지도에 마커 ${n} 없음`);
  assert.deepEqual(Object.keys(MARKER_POS), Object.keys(MARKERS), "지도 그림(map.js)과 지도 데이터(mapdata.js)의 마커가 같음");
  for (const z of ["A1", "A2", "A3", "B1", "B2"]) assert.ok(svg.includes(`>${z}</text>`), `지도에 구역 ${z} 없음`);
  assert.ok(svg.includes("비상구 1") && svg.includes("비상구 2") && svg.includes("충전 스테이션"));
  assert.ok(!/화재|통로 차단|밟힘/.test(svg));
  assert.match(el("time").textContent, /^\d\d:\d\d:\d\d$/);
  // 기본 화면: 새 경보에 팝업이 저절로 뜨지 않음. 최근 경보 · 요약 칸을 누르면 위치만 보이는 팝업 (사진 · 버튼 없음)
  assert.equal(el("popup").hidden, true);
  const firstId = el("alerts").innerHTML.match(/data-alert="(\d+)"/)[1];
  await app.click({ alert: firstId });
  assert.equal(el("popup").hidden, false);
  assert.match(el("popup").innerHTML, /popup-body where-only/);
  assert.match(el("popup").innerHTML, /mini-map/);
  assert.ok(!/data-check|class="photo|<dl class="fields"/.test(el("popup").innerHTML), "사진 · 버튼 · 값 없음");
  assert.match(el("popup").innerHTML, />닫기<\/button>/);
  await app.click({ popupClose: "1" });
  assert.equal(el("popup").hidden, true);
  // 요약 칸: 과열이 여러 건이라 목록에서 골라 팝업 (기본 화면도 같음)
  await app.click({ kpi: "heat" });
  assert.equal(el("kpi-pick").hidden, false);
  await app.click({ pick: el("kpi-pick").innerHTML.match(/data-pick="(\d+)"/)[1] });
  assert.match(el("popup").innerHTML, /과열 경보/);
  assert.ok(!el("popup").innerHTML.includes("data-check"));
  await app.click({ popupClose: "1" });
  app.stop();
}

// ---------- 6) 관리자 화면: 탭, 요약 줄, 눌러서 팝업으로 보고 경보 해제 ----------
{
  const app = await bootApp("?fake&admin", "admin");
  const { el, fake } = app;
  assert.match(el("alerts").innerHTML, /남은 경보가 없습니다/);
  // 로봇 현황 카드 4대
  assert.equal(el("robot-panel").hidden, false);
  assert.equal((el("robot-list").innerHTML.match(/<li /g) || []).length, 4);
  for (const n of ["MD-1", "MD-2", "MD-3", "MD-4"]) assert.ok(el("robot-list").innerHTML.includes(`>${n}</span>`));
  // 로봇 카드 맨 아래 프로그램 버전 (가짜 로봇은 "fake")
  assert.equal((el("robot-list").innerHTML.match(/<div class="version">버전 fake<\/div>/g) || []).length, 4);

  // 탭: 처음은 실시간 관제, 기록 탭으로 바꿀 수 있음
  assert.equal(el("tabs").hidden, false);
  assert.equal(el("records-side").hidden, false);
  assert.equal(el("shell").className, "shell has-tabs tab-live");
  await app.click({ tab: "records" });
  assert.equal(el("shell").className, "shell has-tabs tab-records");
  await app.click({ tab: "live" });
  assert.equal(el("shell").className, "shell has-tabs tab-live");

  // 요약 줄: 로봇 3칸(4대를 나눠 담음) + 경보 4칸
  const kpi = (label) => Number(el("kpis").innerHTML.match(new RegExp(`<span class="kpi-label">${label}</span><b>(\\d+)</b>`))[1]);
  assert.equal(kpi("순찰 중") + kpi("충전 스테이션") + kpi("확인 필요"), 4);
  assert.equal(kpi("충전 스테이션"), 2); // C · D는 처음에 충전 스테이션에서 대기
  assert.equal(kpi("통로 차단"), 0);
  assert.ok(!el("kpis").innerHTML.includes("data-kpi"), "0건이면 누를 수 없음");

  // 경보 (화면을 연 직후라 저절로 뜨지 않음) → 요약 칸 · 실시간 관제 탭 숫자, 눌러서 팝업
  const ob = fake.emit("obstacle", "A");
  await tickWait();
  assert.equal(el("popup").hidden, true);
  assert.equal(kpi("통로 차단"), 1);
  assert.match(el("kpis").innerHTML, /class="kpi warn kpi-btn" data-k="obstacle" data-kpi="obstacle"/);
  assert.equal(el("tab-badge").textContent, 1);
  assert.equal(el("tab-badge").hidden, false);
  await app.click({ kpi: "obstacle" });
  let d = el("popup").innerHTML;
  assert.equal(el("popup").hidden, false);
  assert.match(d, /통로 차단 경보/);
  assert.match(d, /사진 없음/); // 가짜 로봇은 사진이 없다
  assert.match(d, new RegExp(`data-check="${ob.id}"`));
  assert.match(d, /<dt>사람<\/dt>/);
  assert.match(d, /<dt>로봇<\/dt><dd>MD-1<\/dd>/);
  assert.match(d, /닫기 \(위 요약 칸이나 최근 경보를 누르면 다시 볼 수 있음\)/);
  await app.click({ popupClose: "1" });
  assert.equal(el("popup").hidden, true);

  // 최근 경보를 눌러도 팝업. 과열은 온도, 신뢰도는 안 보여 줌
  const heat = fake.emit("heat", "B");
  await tickWait();
  await app.click({ alert: String(heat.id) });
  d = el("popup").innerHTML;
  assert.match(d, /과열 경보/);
  assert.match(d, /<dt>온도<\/dt>/);
  assert.ok(!d.includes("신뢰도"));
  // 경보 해제 → 서버에 보내고, 눌러서 연 팝업도 닫히고, 목록에서 빠짐
  await app.click({ check: String(heat.id) });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === heat.id).checked, true);
  assert.equal(el("popup").hidden, true);
  assert.ok(!el("alerts").innerHTML.includes(`data-alert="${heat.id}"`));

  // 같은 종류가 여러 건이면 칸 아래 목록에서 골라 팝업. 오탐으로 해제해도 닫힘
  const ob2 = fake.emit("obstacle", "B");
  await tickWait();
  await app.click({ kpi: "obstacle" });
  assert.equal(el("popup").hidden, true);
  assert.equal(el("kpi-pick").hidden, false);
  assert.match(el("kpi-pick").innerHTML, /통로 차단 2건/);
  assert.match(el("kpi-pick").innerHTML, new RegExp(`data-pick="${ob.id}"`));
  await app.click({ pick: String(ob2.id) });
  assert.equal(el("kpi-pick").hidden, true);
  assert.match(el("popup").innerHTML, new RegExp(`data-check="${ob2.id}"`));
  await app.click({ check: String(ob2.id), falseAlarm: "1" });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === ob2.id).false_alarm, true);
  assert.equal(el("popup").hidden, true);

  // 불꽃 감지: 다른 경보처럼 바로 해제. 관리자가 화재 확정을 누르지 않으면 대피 모드는 안 됨
  const flame = fake.emit("flame", "C");
  await tickWait();
  await app.click({ kpi: "flame" });
  assert.match(el("popup").innerHTML, /불꽃 감지 경보/);
  assert.equal(el("evacuate").hidden, true);
  await app.click({ check: String(flame.id) });
  await tickWait();
  assert.equal(fake.events.find((e) => e.id === flame.id).checked, true);

  // 경보 해제를 못 보내면 팝업에 알려 준다 (없는 경보 번호 → 404)
  const obId = ob.id;
  fake.events.find((e) => e.id === obId).id = 9999; // 서버 쪽에서만 번호가 바뀐 것처럼
  await app.click({ alert: String(obId) });
  await app.click({ check: String(obId) });
  await tickWait();
  assert.match(el("popup").innerHTML, /경보 해제를 보내지 못했습니다 \(서버 응답 404\)/);
  await app.click({ popupClose: "1" });

  // 지도에 로봇 위치 (화면 이름으로)
  assert.equal((el("map .robots").innerHTML.match(/class="robot"/g) || []).length, 4);
  assert.ok(el("map .robots").innerHTML.includes(">MD-1</text>"));
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
  assert.equal(a.play("flame"), 2); // 불꽃 감지는 과열보다 높게 2번
  assert.equal(a.play("floor_object"), 1);
  a.disable();
  assert.equal(a.play("evacuate"), 0);
  assert.equal(a.play("flame", { force: true }), 2, "꺼져 있어도 force면 소리");
  const b = createAlarm(FakeAudioContext);
  assert.equal(b.play("flame", { force: true }), 0, "화면을 한 번도 안 눌렀으면 소리 장치가 없음");
  b.unlock();
  assert.equal(b.enabled, false, "unlock은 경보음을 켜지 않음");
  assert.equal(b.play("flame", { force: true }), 2);
  assert.equal(b.play("obstacle"), 0, "다른 경보는 꺼져 있으면 소리 없음");
  assert.equal(createAlarm(undefined).supported, false);
}

// ---------- 8) 관리자 화면: 경보 팝업 (4종 모두), 경보음 ----------
{
  globalThis.AudioContext = FakeAudioContext;
  const app = await bootApp("?fake&admin", "popup");
  const { el, fake } = app;
  await tickWait(550); // 화면을 연 직후의 첫 묶음 시간이 지나야 새 경보로 친다

  // 경보음 켜기 버튼
  assert.ok(el("sound").title.startsWith("경보음 켜기") && el("sound").innerHTML.includes("M3 3l18 18"), "꺼져 있으면 사선 아이콘");
  await app.click({ sound: "1" });
  assert.ok(el("sound").title === "경보음 끄기" && !el("sound").innerHTML.includes("M3 3l18 18"));
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

  // 과열 → 경보음 1번, 팝업은 통로 차단 다음으로 대기
  fake.emit("heat", "B");
  await tickWait();
  assert.equal(beeps, 3);
  assert.match(el("popup").innerHTML, /통로 차단 경보/);
  assert.match(el("popup").innerHTML, /대기 중인 팝업 1건/);
  await app.click({ popupClose: "1" });
  assert.match(el("popup").innerHTML, /과열 경보/);
  await app.click({ popupClose: "1" });
  assert.equal(el("popup").hidden, true);

  // 불꽃 감지 → 팝업 + 높은 경보음 2번. 대피 모드는 아님
  fake.emit("flame", "C");
  await tickWait();
  assert.equal(beeps, 5);
  assert.match(el("popup").innerHTML, /불꽃 감지 경보/);
  assert.equal(el("evacuate").hidden, true);
  // 불꽃 감지 팝업에만 화재 확정 버튼
  const flameId = el("popup").innerHTML.match(/data-fire="(\d+)"/)[1];
  assert.ok(!el("popup").innerHTML.includes("data-sure"), "처음에는 묻기 전");
  await app.click({ fire: flameId });
  assert.match(el("popup").innerHTML, /불을 확인했나요/);
  await app.click({ fireCancel: "1" });
  assert.ok(!el("popup").innerHTML.includes("불을 확인했나요"));
  await app.click({ popupClose: "1" });

  // 밟힘 주의 → 팝업 + 경보음 1번
  fake.emit("floor_object", "D");
  await tickWait();
  assert.equal(beeps, 6);
  assert.match(el("popup").innerHTML, /밟힘 주의 경보/);
  await app.click({ popupClose: "1" });
  assert.ok(!el("alerts").innerHTML.includes("blink"), "깜빡임 없음");

  // 기록 탭을 보고 있어도 팝업 · 경보음
  await app.click({ tab: "records" });
  fake.emit("obstacle", "B");
  await tickWait();
  assert.equal(beeps, 8);
  assert.match(el("popup").innerHTML, /통로 차단 경보/);
  await app.click({ popupClose: "1" });
  await app.click({ tab: "live" });

  // 화재 확정(서버가 대피 모드로 바꿈) → 과열 1 + 불꽃 감지 2 + 대피 3, 대피 띠에 "경보음 멈춤"
  beeps = 0;
  fake.fire("D");
  await tickWait();
  assert.equal(beeps, 1 + 2 + 3);
  assert.equal(el("evacuate").hidden, false);
  assert.match(el("evacuate-actions").innerHTML, /data-evac-mute="1"/);
  await app.click({ evacMute: "1" });
  assert.ok(!el("evacuate-actions").innerHTML.includes("data-evac-mute"));
  await app.click({ popupClose: "1" });
  await app.click({ popupClose: "1" });
  assert.equal(el("popup").hidden, true);
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
  await app.click({ popupClose: "1" });

  // 경보음 끄기 → 소리 안 남. 불꽃 감지와 대피는 꺼져 있어도 남
  await app.click({ sound: "1" });
  beeps = 0;
  fake.emit("obstacle", "D");
  await tickWait();
  assert.equal(beeps, 0);
  fake.emit("flame", "A");
  await tickWait();
  assert.equal(beeps, 2, "경보음을 꺼도 불꽃 감지 소리는 남");
  beeps = 0;
  await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate" }) });
  await tickWait();
  assert.equal(beeps, 3, "경보음을 꺼도 대피 소리는 남");
  await app.click({ evacMute: "1" });
  await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  await tickWait();

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

  // 경보 이력: 해제한 경보도 남고, 눌러 보면 팝업에 버튼 없이 "경보 해제"
  assert.match(el("history").innerHTML, new RegExp(`data-alert="${fire.id}"`));
  assert.match(el("history").innerHTML, /해제 전/);
  assert.match(el("history").innerHTML, /A 경로/);
  await app.click({ check: String(fire.id) });
  await tickWait();
  assert.match(el("history").innerHTML, /<td class="state done">경보 해제 <span>\d\d:\d\d:\d\d<\/span><\/td>/);
  assert.ok(!el("alerts").innerHTML.includes(`data-alert="${fire.id}"`)); // 최근 경보에서는 빠짐
  await app.click({ alert: String(fire.id) });
  const d = el("popup").innerHTML;
  assert.match(d, /<p class="done-text">경보 해제<\/p>/);
  assert.ok(!d.includes("data-check"));
  assert.match(d, /<dt>경로<\/dt><dd>A 경로<\/dd>/);
  await app.click({ popupClose: "1" });
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
      assert.equal(el("login-back").href, "./?fake&lock", "기본모드 (admin만 뺌)");
      assert.equal(el("view-switch").textContent, "기본모드");
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
  await app.click({ alert: String(ob2.id) });
  await app.click({ check: String(ob2.id) });
  await tickWait();
  assert.equal(fake.events.find((x) => x.id === ob2.id).checked, false);
  assert.equal(el("login").hidden, false);
  assert.match(el("login-error").textContent, /바뀌었습니다/);
  assert.match(el("popup").innerHTML, /관리자 비밀번호가 맞지 않음/);
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

  // 화면: 팝업에 경보 해제 · 오탐 두 버튼, 오탐을 누르면 서버에 오탐으로 기록되고 이력에 "오탐으로 해제"
  const app = await bootApp("?fake&admin", "false-alarm");
  const { el, fake } = app;
  const ob = fake.emit("obstacle", "C");
  await tickWait();
  await app.click({ alert: String(ob.id) });
  const d = el("popup").innerHTML;
  assert.match(d, new RegExp(`data-check="${ob.id}">경보 해제</button>`));
  assert.match(d, new RegExp(`data-check="${ob.id}" data-false-alarm="1">오탐</button>`));
  await app.click({ check: String(ob.id), falseAlarm: "1" });
  await tickWait();
  const sent = fake.events.find((x) => x.id === ob.id);
  assert.equal(sent.checked, true);
  assert.equal(sent.false_alarm, true);
  assert.match(el("history").innerHTML, /<td class="state done false-alarm">오탐으로 해제 <span>\d\d:\d\d:\d\d<\/span><\/td>/);
  await app.click({ alert: String(ob.id) });
  assert.match(el("popup").innerHTML, /<p class="done-text">오탐으로 해제<\/p>/);
  await app.click({ popupClose: "1" });
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

  // 화면: 관리자 화면을 열면 지난 경보를 받아 이력과 정탐률을 보여 주고, 10건씩 쪽 번호
  const app = await bootApp("?fake&admin&past", "past");
  const { el, fake } = app;
  await tickWait(20);
  assert.equal(el("history-sub").textContent, "서버에 기록된 경보, 최근 것부터");
  const rowsShown = () => (el("history").innerHTML.match(/<tr>/g) || []).length;
  const want = fake.events.filter((e) => e.type !== "helmet").length;
  assert.equal(rowsShown(), Math.min(10, want));
  assert.equal(el("stats-panel").hidden, false);
  assert.match(el("stats").innerHTML, /<th scope="row">전체<\/th>/);
  assert.match(el("stats").innerHTML, /<th scope="row">통로 차단<\/th>/);
  // 서버에 더 없으면 "이전 기록 더 받기" 없음 (가짜는 40건 중 안전모 빼고)
  assert.ok(!el("history-more").innerHTML.includes("data-more"));
  assert.match(el("history-tools").innerHTML, new RegExp(`data-htype="all" aria-pressed="true">전체 <span>${want}</span>`));
  // 다음 쪽
  const firstRow = el("history").innerHTML.match(/data-alert="(\d+)"/)[1];
  await app.click({ hpage: "1" });
  assert.notEqual(el("history").innerHTML.match(/data-alert="(\d+)"/)[1], firstRow);
  assert.match(el("history-more").innerHTML, /class="pg on" data-hpage="1" aria-current="page">2</);
  // 종류 거르기: 그 종류만, 첫 쪽으로
  await app.click({ htype: "heat" });
  assert.ok(!/>통로 차단</.test(el("history").innerHTML) && !/>밟힘 주의</.test(el("history").innerHTML));
  assert.match(el("history-more").innerHTML, /data-hpage="0" aria-current="page"/);
  await app.click({ htype: "all" });
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

  // 쪽 번호: 120건이면 첫 쪽 10건, 끝 쪽 번호, 범위 밖 쪽은 끝 쪽으로
  {
    const app2 = await bootApp("?fake&admin", "more");
    const f2 = app2.fake;
    f2._seedPast(120);
    await app2.click({ pastRetry: "1" }); // 다시 받기와 같은 길로 첫 쪽을 다시 받음
    await tickWait(20);
    const n = f2.events.filter((e) => e.type !== "helmet").length;
    const pages = Math.ceil(n / 10);
    assert.equal((app2.el("history").innerHTML.match(/<tr>/g) || []).length, 10);
    assert.match(app2.el("history-more").innerHTML, new RegExp(`data-hpage="${pages - 1}">${pages}<`));
    assert.match(app2.el("history-more").innerHTML, new RegExp(`${n}건 중 1–10`));
    await app2.click({ hpage: String(pages + 5) });
    assert.match(app2.el("history-more").innerHTML, new RegExp(`data-hpage="${pages - 1}" aria-current="page"`));
    assert.equal((app2.el("history").innerHTML.match(/<tr>/g) || []).length, n - (pages - 1) * 10);
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

// ---------- 길 중간에 멈춘 로봇 (이슈 #22) ----------
{
  assert.equal(robotStop({ status: "idle", marker: DOCK_MARKER }), null, "충전 스테이션에서 쉬는 건 멈춤 아님");
  assert.equal(robotStop({ status: "idle", marker: DOCK_MARKER, hold: null }), null);
  assert.deepEqual(robotStop({ status: "idle", marker: DOCK_MARKER, hold: "stop" }), { hold: "stop", reason: "관리자 정지" },
    "충전 스테이션이어도 hold가 있으면 멈춤 (서버가 자동 출발시키지 않음)");
  assert.equal(robotStop({ status: "guide", marker: 7, hold: "guide" }), null, "대피 안내는 따로 있음");
  assert.equal(robotStop({ status: "patrol", marker: 7 }), null);
  assert.equal(robotStop({ status: "blocked", marker: 7 }), null, "막힘 정지는 따로 있음");
  assert.equal(robotStop({ status: "idle", marker: null }), null);
  assert.equal(robotStop(undefined), null);
  assert.deepEqual(robotStop({ status: "idle", marker: 7 }), { hold: null, reason: null });
  assert.deepEqual(robotStop({ status: "idle", marker: 7, hold: "line" }), { hold: "line", reason: "안전 정지: 선을 놓침" });
  // 서버 대기(wait: 교통 정리 · 교차 확인)는 멈춤이 아님. 서버가 다시 출발시킨다
  assert.equal(robotStop({ status: "idle", marker: 7, hold: "wait" }), null);
  assert.equal(robotWaiting({ status: "idle", marker: 7, hold: "wait" }), true);
  assert.equal(robotWaiting({ status: "idle", marker: 7, hold: "stop" }), false);
  assert.equal(robotWaiting({ status: "patrol", marker: 7, hold: null }), false);
  // edge/agent.py가 쓰는 hold 값은 모두 문구가 있음
  const { HOLD_TEXT } = await import("./js/store.js");
  const agent = (await import("node:fs")).readFileSync(new URL("../edge/agent.py", import.meta.url), "utf8");
  const holdPats = [/_safe_halt\("(\w+)"\)/g, /self\.hold = (?:self\.hold or )?"(\w+)"/g, /self\.hold, [^=\n]*= "idle", "(\w+)"/g];
  const holds = new Set(holdPats.flatMap((re) => [...agent.matchAll(re)].map((m) => m[1])));
  assert.ok(holds.size >= 10, `edge/agent.py에서 hold 값을 못 찾음: ${[...holds]}`);
  for (const h of holds) assert.ok(h in HOLD_TEXT, `HOLD_TEXT에 ${h} 문구 없음 (edge/agent.py)`);
  assert.deepEqual(robotStop({ status: "idle", marker: 7, hold: "new_one" }), { hold: "new_one", reason: "new_one" }, "모르는 이유는 그대로");
  // 이슈 #39 · 엣지 PR #47이 더하는 값
  assert.equal(robotStop({ status: "idle", marker: 1, hold: "server" }).reason, "서버 연결 끊김으로 정지");
  assert.match(robotStop({ status: "idle", marker: 1, hold: "battery" }).reason, /^배터리 부족으로 정지/);

  // 지도: 멈춘 로봇은 주황 테두리
  assert.ok(robotLayer([{ name: "MD-1", marker: 7, online: true, stopped: true }]).includes("#ff9f1a"));
  assert.ok(!robotLayer([{ name: "MD-1", marker: 7, online: true, stopped: false }]).includes("#ff9f1a"));

  // 화면: 로봇 카드 "멈춤" + 현장 확인 필요, hold가 오면 이유. 안전 현황은 그대로
  const app = await bootApp("?fake&admin", "halt");
  const { el, fake } = app;
  await tickWait(20);
  const levelBefore = el("level").textContent;
  fake.robots.A.marker = 7;
  fake.halt("A");
  await tickWait();
  const card = () => el("robot-list").innerHTML.split("<li").find((x) => x.includes(">MD-1<"));
  assert.match(card(), /class="ok stopped"/);
  assert.match(card(), /<span class="state">멈춤<\/span>/);
  assert.match(card(), /현장 확인 필요/);
  assert.ok(el("map .robots").innerHTML.includes("#ff9f1a"));
  assert.equal(el("level").textContent, levelBefore, "안전 현황 단계는 올리지 않음");
  fake.halt("A", "no_ack");
  await tickWait();
  assert.match(card(), /안전 정지: 보드가 명령에 답 없음/);
  fake.resume("A");
  await tickWait();
  assert.ok(!/멈춤/.test(card()));
  assert.ok(!el("map .robots").innerHTML.includes("#ff9f1a"));

  // 충전 스테이션에 있어도 hold가 있으면 멈춤 (서버가 자동 출발시키지 않음), 요약 줄은 "확인 필요"로
  const kpiN = (label) => Number(el("kpis").innerHTML.match(new RegExp(`<span class="kpi-label">${label}</span><b>(\\d+)</b>`))[1]);
  const cardD = () => el("robot-list").innerHTML.split("<li").find((x) => x.includes(">MD-4<"));
  const dockBefore = kpiN("충전 스테이션");
  const checkBefore = kpiN("확인 필요");
  fake.halt("D", "stop");
  await tickWait();
  assert.match(cardD(), /<span class="state">멈춤<\/span>/);
  assert.match(cardD(), /관리자 정지/);
  assert.match(cardD(), /충전 스테이션/);
  assert.equal(kpiN("충전 스테이션"), dockBefore - 1);
  assert.equal(kpiN("확인 필요"), checkBefore + 1);
  fake.resume("D");
  await tickWait();
  assert.ok(!/멈춤/.test(cardD()), "hold가 풀리면 다시 대기");
  assert.equal(kpiN("충전 스테이션"), dockBefore);

  // 다시 출발: 멈춘 로봇 카드에 버튼, 누르면 서버에 resume (관리자 요청), 로봇이 풀림
  assert.ok(!el("robot-list").innerHTML.includes("data-resume"), "멈춘 로봇이 없으면 버튼 없음");
  fake.robots.A.marker = 6;
  fake.robots.A.role = "A";
  fake.halt("A", "line");
  await tickWait();
  assert.match(card(), /data-resume="A">다시 출발<\/button>/);
  await app.click({ resume: "A" });
  await tickWait();
  assert.equal(fake.robots.A.hold, null);
  assert.equal(fake.robots.A.status, "patrol");
  assert.ok(!/멈춤|data-resume/.test(card()));
  // 서버 대기(wait: 교통 정리 · 교차 확인)는 멈춤이 아니라 버튼 없음, 주황 테두리 없음
  fake.robots.B.marker = 7;
  fake.halt("B", "wait");
  await tickWait();
  const cardB = el("robot-list").innerHTML.split("<li").find((x) => x.includes(">MD-2<"));
  assert.match(cardB, /class="state">서버 대기</);
  assert.match(cardB, /길이 비면 서버가 다시 출발시킴/);
  assert.ok(!cardB.includes("data-resume") && !cardB.includes("stopped") && !cardB.includes("멈춤"));
  // 못 보내면 카드에 알림 (가짜 서버가 모르는 로봇 → 404)
  fake.robots.C.marker = 9;
  fake.halt("C", "stop");
  await tickWait();
  const realC = fake.robots.C;
  delete fake.robots.C;
  await app.click({ resume: "C" });
  await tickWait();
  fake.robots.C = realC;
  assert.match(el("robot-list").innerHTML, /다시 출발을 보내지 못했습니다 \(서버 응답 404\)/);
  app.stop();
}

// ---------- 안전모 미착용 기록 (관리자 화면 따로 칸, 사진 없음) ----------
{
  const NOW = new Date(2026, 10, 26, 15, 0, 0).getTime();
  const sec = (d, h) => Math.floor(new Date(2026, 10, d, h, 0, 0).getTime() / 1000);
  const st = createStore();
  apply(st, { kind: "event", id: 1, robot: "A", type: "helmet", ts: sec(25, 10), marker: 7, photo: "/photos/1.jpg", checked: false });
  apply(st, { kind: "event", id: 2, robot: "B", type: "helmet", ts: sec(26, 9), marker: 7, checked: false });
  apply(st, { kind: "event", id: 3, robot: "C", type: "helmet", ts: sec(26, 11), marker: 3, checked: false });
  apply(st, { kind: "event", id: 4, robot: "C", type: "obstacle", ts: sec(26, 12), marker: 3, checked: false });
  loadHistory(st, [{ kind: "event", id: 0, robot: "D", type: "helmet", ts: sec(24, 8), marker: 7, checked: true, false_alarm: false }]);
  const h = helmetLog(st, NOW);
  assert.equal(h.today, 2);
  assert.equal(h.total, 4, "지난 경보에서 받아 온 것도 셈");
  assert.deepEqual(h.byMarker, [{ marker: 7, count: 3 }, { marker: 3, count: 1 }]);
  assert.deepEqual(h.recent.map((e) => e.id), [3, 2, 1, 0]);

  // 화면: 기록 탭 안전모 칸에 오늘 건수 · 위치 · 최근 기록. 새 안전모 경보에 팝업 · 경보음은 없고,
  // 최근 경보 · 경보 이력 · 안전 현황에도 안 나옴. 칸의 줄을 누르면 사진 팝업 (해제 버튼 없음)
  const app = await bootApp("?fake&admin", "helmet");
  const { el, fake } = app;
  await tickWait(550);
  assert.equal(el("helmet-panel").hidden, false);
  assert.match(el("helmet").innerHTML, /기록이 없습니다/);
  const levelBefore = el("level").textContent;
  fake.robots.A.marker = 7;
  const orig = fake.robots.A.eventFields.bind(fake.robots.A);
  fake.robots.A.eventFields = (t) => ({ ...orig(t), photo: "/photos/A_1127_100000.jpg" });
  const hm = fake.emit("helmet", "A");
  await tickWait();
  assert.match(el("helmet").innerHTML, /<b>오늘 1건<\/b>/);
  assert.match(el("helmet").innerHTML, /마커 7 1건/);
  assert.match(el("helmet").innerHTML, />MD-1</);
  assert.match(el("helmet").innerHTML, new RegExp(`data-alert="${hm.id}"`));
  assert.match(el("helmet").innerHTML, /<span class="helmet-photo">사진<\/span>/);
  assert.ok(!el("alerts").innerHTML.includes(`data-alert="${hm.id}"`));
  assert.ok(!el("history").innerHTML.includes(`data-alert="${hm.id}"`));
  // 안전모 기록도 10건씩 쪽 번호
  assert.ok(!el("helmet").innerHTML.includes("data-mpage"), "10건 이하면 쪽 번호 없음");
  for (let i = 0; i < 11; i++) fake.emit("helmet", "B");
  await tickWait();
  assert.equal((el("helmet").innerHTML.match(/class="helmet-btn"/g) || []).length, 10);
  assert.match(el("helmet").innerHTML, /data-mpage="1">2</);
  await app.click({ mpage: "1" });
  assert.equal((el("helmet").innerHTML.match(/class="helmet-btn"/g) || []).length, 2);
  assert.match(el("helmet").innerHTML, new RegExp(`data-alert="${hm.id}"`), "가장 오래된 것은 둘째 쪽");
  await app.click({ mpage: "0" });
  assert.equal(el("popup").hidden, true, "새 안전모 경보에 팝업 안 뜸");
  assert.equal(el("level").textContent, levelBefore);
  // 누르면 사진 팝업, 해제 · 오탐 버튼 없음
  await app.click({ alert: String(hm.id) });
  const d = el("popup").innerHTML;
  assert.equal(el("popup").hidden, false);
  assert.match(d, /안전모 미착용 경보/);
  assert.match(d, /<img src="\/photos\/A_1127_100000.jpg"/);
  assert.match(d, /<dt>위치<\/dt><dd>마커 7<\/dd>/);
  assert.ok(!d.includes("data-check"), "해제 버튼 없음");
  assert.match(d, /현장에서 음성으로 안내함/);
  await app.click({ popupClose: "1" });
  assert.equal(el("popup").hidden, true);
  app.stop();
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

// ---------- 불꽃 감지 팝업에서 화재 확정 → 대피 모드, 불난 곳은 그 경보의 마커 ----------
{
  const app = await bootApp("?fake&admin", "fireconfirm");
  const { el, fake } = app;
  await tickWait(550);
  fake.robots.A.marker = 6;
  const fl = fake.emit("flame", "A");
  await tickWait();
  await app.click({ fire: String(fl.id) });
  await app.click({ fire: String(fl.id), sure: "1" });
  await tickWait();
  assert.equal(fake.mode, "evacuate");
  assert.equal(fake.evac.fire, 6, "불꽃 감지 마커가 불난 곳");
  assert.equal(el("evacuate").hidden, false);
  assert.match(el("popup").innerHTML, /대피 모드입니다/);
  assert.ok(!el("popup").innerHTML.includes("data-fire="), "대피 중에는 화재 확정 버튼 없음");
  assert.match(el("evacuate-actions").innerHTML, /data-evac-mute="1"/, "경보음을 안 켰어도 대피 소리는 나므로 멈춤 버튼");
  // 마커가 지도에 없는 값이면(보드가 두 마커를 같이 읽은 "12+15" 등) fire 없이 대피
  await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "normal" }) });
  assert.equal((await fake.fetch("/mode", { method: "POST", body: JSON.stringify({ mode: "evacuate", fire: 99 }) })).status, 400, "가짜 서버도 없는 마커는 400");
  await tickWait();
  const odd = fake._addEvent(fake.robots.B, "flame", { marker: "12+15" });
  await app.click({ alert: String(odd.id) });
  await app.click({ fire: String(odd.id), sure: "1" });
  await tickWait();
  assert.equal(fake.mode, "evacuate", "마커가 이상해도 대피는 됨");
  assert.equal(fake.evac.fire, null);
  app.stop();
}

// ---------- 누르는 처리가 있는 data-… 는 모두 click의 closest 목록에 있어야 함 ----------
{
  const src = (await import("node:fs")).readFileSync(new URL("./js/app.js", import.meta.url), "utf8");
  const list = src.match(/closest\?\.\(\s*"([^"]+)"/)[1];
  const kebab = (k) => k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
  const used = new Set([...src.matchAll(/if \(t\.dataset\.(\w+)\)/g)].map((m) => m[1]));
  assert.ok(used.has("themeToggle") && used.size > 10);
  for (const k of used) assert.ok(list.includes(`[data-${kebab(k)}]`), `closest 목록에 [data-${kebab(k)}] 없음`);
}

console.log("dashboard selfcheck OK");
