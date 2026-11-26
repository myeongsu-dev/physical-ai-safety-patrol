// 화면 상태. 서버에서 받은 메시지를 모아 두고, 화면에 보여 줄 값을 계산한다.
// 화면(DOM)은 건드리지 않는다. 그래서 selfcheck.mjs에서 node로 바로 시험할 수 있다.

import { DOCK_MARKER } from "./mapdata.js";

// 팀 로봇 4대 (Pi의 ROBOT_ID). 서버가 아직 한 번도 못 본 로봇도 "끊김"으로 센다
export const ROBOTS = ["A", "B", "C", "D"];

// 화면에 보이는 로봇 이름. 서버·Pi와 주고받는 이름(ROBOT_ID)은 A~D 그대로 두고 화면에서만 바꾼다.
// 팀 공통 이름으로 정함 (README "화면 용어")
export const ROBOT_LABEL = { A: "MD-1", B: "MD-2", C: "MD-3", D: "MD-4" };

export function robotLabel(name) {
  return ROBOT_LABEL[name] ?? String(name);
}

// 이 시간 안에 state가 와야 연결된 것으로 본다. server/fleet.py의 ONLINE_SEC과 같게 둔다
export const ONLINE_SEC = 5;

export function createStore() {
  return {
    server: "connecting", // connecting · open · closed
    serverSince: Date.now(), // 지금 server 상태가 된 시각 (ms)
    robots: {}, // 로봇 이름 → 마지막 state 메시지
    seen: {}, // 로봇 이름 → 마지막으로 연락된 시각 (ms). 모르면 null
    offline: {}, // 로봇 이름 → 서버가 끊겼다고 알려 줌 (online:false)
    mode: "normal",
    // 대피 안내 { fire, exits, arrows }. 서버가 대피 모드로 바꾸면 mode 메시지 다음에 보내는 evac 메시지 (server/dispatch.py).
    // fire: 불난 마커(관리자가 직접 대피로 바꾸면 null), exits: 출구 마커, arrows: [{ marker, dir, exit, path }] 마커마다
    evac: null,
    events: new Map(), // id → 해제 안 된 경보
    // 경보 이력: id → { event, role }. 해제된 경보도 남긴다 (event.checked).
    // 이 화면을 연 뒤에 받은 경보 + 서버에서 꺼내 온 지난 경보(GET /events, loadHistory)
    history: new Map(),
  };
}

export function setServer(store, status, now = Date.now()) {
  if (store.server === status) return;
  store.server = status;
  store.serverSince = now;
}

// 서버 메시지 하나를 반영한다
export function apply(store, msg, now = Date.now()) {
  if (msg.kind === "state") {
    store.robots[msg.robot] = msg;
    if (msg.online === false) {
      // 서버가 끊겼다고 알려 준 경우: 화면을 새로 열 때의 첫 묶음, 또는 5초 넘게 state가 안 와서
      // 서버가 한 번 보내 주는 알림 (server/link-status). 이 state의 다른 값은 마지막으로 받은 것
      store.offline[msg.robot] = true;
      // 마지막 연락 시각: seen_ago(초)가 있으면 그걸로, 없으면 이 화면이 마지막으로 받은 시각 그대로
      if (typeof msg.seen_ago === "number") store.seen[msg.robot] = now - msg.seen_ago * 1000;
      else store.seen[msg.robot] ??= null;
    } else {
      store.offline[msg.robot] = false;
      store.seen[msg.robot] = now;
    }
  } else if (msg.kind === "mode") {
    store.mode = msg.mode;
    if (msg.mode !== "evacuate") store.evac = null; // 평상시로 돌아가면 대피 안내도 끝 (서버는 따로 알리지 않음)
  } else if (msg.kind === "evac") {
    store.evac = {
      fire: Number.isInteger(msg.fire) ? msg.fire : null,
      exits: Array.isArray(msg.exits) ? msg.exits : [],
      arrows: Array.isArray(msg.arrows) ? msg.arrows : [],
    };
  } else if (msg.kind === "event") {
    if (msg.checked) store.events.delete(msg.id);
    else store.events.set(msg.id, msg);
    remember(store, msg);
  }
}

export const HISTORY_MAX = 2000; // 화면이 들고 있는 경보 이력 최대 건수 (넘으면 오래된 것부터 뺌)

function remember(store, msg) {
  const prev = store.history.get(msg.id);
  store.history.set(msg.id, {
    event: { ...(prev?.event ?? {}), ...msg },
    // 경보에는 경로가 없어서, 받은 순간 그 로봇이 맡고 있던 경로를 같이 적어 둔다
    role: prev?.role ?? store.robots[msg.robot]?.role ?? null,
  });
  while (store.history.size > HISTORY_MAX) {
    store.history.delete(Math.min(...store.history.keys()));
  }
}

// 서버에서 꺼내 온 지난 경보(GET /events)를 경보 이력에 합친다. 해제 안 된 경보 목록(events)은 건드리지 않는다
// (WebSocket이 붙자마자 보내 줌). 지난 경보는 받은 때의 경로를 모르니 role은 null.
// 이미 해제로 받은 경보를 예전 값(해제 전)으로 되돌리지 않는다
export function loadHistory(store, list) {
  if (!Array.isArray(list)) return;
  for (const e of list) {
    if (!e || typeof e.id !== "number") continue;
    const prev = store.history.get(e.id);
    const keep = prev?.event.checked && !e.checked;
    store.history.set(e.id, {
      event: keep ? { ...e, ...pick(prev.event, ["checked", "checked_at", "false_alarm"]) } : { ...(prev?.event ?? {}), ...e },
      role: prev?.role ?? null,
    });
  }
  while (store.history.size > HISTORY_MAX) {
    store.history.delete(Math.min(...store.history.keys()));
  }
}

function pick(o, keys) {
  return Object.fromEntries(keys.filter((k) => k in o).map((k) => [k, o[k]]));
}

// 정탐률: 해제한 경보 중 실제였던(오탐이 아닌) 비율. 경보 종류별과 전체.
// 로봇이 놓친 위험은 경보가 없어서 셀 수 없다. 그래서 "정확도"가 아니라 정탐률이다.
// 화면이 가진 경보 이력(서버에서 꺼내 온 것 포함)만 센다. 안전모 미착용은 뺀다 (현장 음성 안내만, 대시보드에 안 보임)
export const STAT_TYPES = ["obstacle", "floor_object", "heat", "flame"];
export function alarmStats(store) {
  const rows = Object.fromEntries(STAT_TYPES.map((t) => [t, { type: t, closed: 0, falseAlarm: 0 }]));
  for (const { event: e } of store.history.values()) {
    if (!e.checked || !rows[e.type]) continue;
    rows[e.type].closed += 1;
    if (e.false_alarm === true) rows[e.type].falseAlarm += 1;
  }
  const list = STAT_TYPES.map((t) => withRate(rows[t]));
  const total = withRate(list.reduce((a, r) => ({ ...a, closed: a.closed + r.closed, falseAlarm: a.falseAlarm + r.falseAlarm }),
    { type: "all", closed: 0, falseAlarm: 0 }));
  return { rows: list, total };
}

function withRate(r) {
  const real = r.closed - r.falseAlarm;
  return { ...r, real, rate: r.closed ? real / r.closed : null };
}

// 안전모 미착용 기록 (관리자 화면 따로 칸, 사진 없음). 현장에서는 음성 안내로 처리하고, 대시보드는 기록만 본다.
// 화면이 가진 경보 이력(이 화면을 연 뒤 받은 것 + 서버에서 꺼내 온 것)만 센다.
//   today: 오늘 건수, total: 전체 건수, byMarker: 위치별 건수(많은 순), recent: 최근 기록(새것부터)
export function helmetLog(store, now = Date.now(), recentMax = 5) {
  const list = [...store.history.values()].map((h) => h.event).filter((e) => e.type === "helmet").sort((a, b) => b.id - a.id);
  const day = new Date(now).toDateString();
  const today = list.filter((e) => e.ts && new Date(e.ts * 1000).toDateString() === day).length;
  const counts = new Map();
  for (const e of list) if (e.marker != null) counts.set(e.marker, (counts.get(e.marker) ?? 0) + 1);
  const byMarker = [...counts].map(([marker, count]) => ({ marker, count })).sort((a, b) => b.count - a.count || a.marker - b.marker);
  return { today, total: list.length, byMarker, recent: list.slice(0, recentMax) };
}

// 경보 이력, 새것부터. 안전모 미착용은 뺀다 (openAlerts와 같은 기준)
export function alertHistory(store, limit = 50) {
  return [...store.history.values()]
    .filter((h) => h.event.type !== "helmet")
    .sort((a, b) => b.event.id - a.event.id)
    .slice(0, limit);
}

// 로봇 한 대가 지금 서버와 연결돼 있는지.
// 서버는 로봇이 끊겨도 따로 알려 주지 않고 그 로봇의 state를 더 보내지 않는다.
// 그래서 마지막으로 받은 시각으로 판단한다.
export function robotOnline(store, name, now = Date.now()) {
  if (store.offline[name]) return false; // 서버가 끊겼다고 알려 줌
  const t = store.seen[name];
  return t != null && now - t <= ONLINE_SEC * 1000;
}

// 안전 현황 (README "안전 현황")
//   화재: 대피 모드. 화재는 서버가 교차 확인(server/events.py)으로 확정하고 대피 모드로 바꾼다.
//         화면이 서버보다 먼저 화재라고 하지 않는다
//   주의: 해제 안 된 통로 차단 · 밟힘 주의 · 과열 · 불꽃 감지 경보가 있거나, 끊긴 로봇이 있음.
//         불꽃 감지(flame)는 로봇 한 대가 불꽃을 본 것이라 서버가 아직 확인하는 중이다
//   안전: 그 밖
// 안전모 미착용(helmet)은 현장 음성 안내만 하고 대시보드에는 보여 주지 않는다 (팀 결정). 서버는 보내지만 화면이 거른다.
// 서버가 끊겨 있으면 지금 상태를 알 수 없으므로 level은 "unknown"
export function safetySummary(store, now = Date.now()) {
  const counts = { heat: 0, obstacle: 0, floor: 0 }; // 과열·불꽃 감지, 통로 차단, 밟힘 주의
  for (const e of store.events.values()) {
    if (e.type === "flame" || e.type === "heat") counts.heat += 1;
    else if (e.type === "obstacle") counts.obstacle += 1;
    else if (e.type === "floor_object") counts.floor += 1;
  }
  const offline = connectionSummary(store, now).robots.filter((r) => !r.online).length;

  let level = "safe";
  if (store.server !== "open") level = "unknown";
  else if (store.mode === "evacuate") level = "fire";
  else if (counts.heat || counts.obstacle || counts.floor || offline) level = "caution";

  return { level, mode: store.mode, counts, offline };
}

// 최근 경보: 해제 안 된 경보, 새것부터.
// 안전모 미착용은 대시보드에 보여 주지 않는다 (safetySummary와 같은 기준)
export function openAlerts(store, limit = 5) {
  return [...store.events.values()]
    .filter((e) => e.type !== "helmet")
    .sort((a, b) => b.id - a.id)
    .slice(0, limit);
}

// 지도에 그릴 대피 안내: 대피 모드일 때 서버가 보낸 evac 메시지. 아니면 null.
// 불꽃 감지(flame) 경보만으로는 그리지 않는다. 화재는 서버가 확정한 것만 보여 준다
export function evacView(store) {
  return store.mode === "evacuate" ? store.evac : null;
}

// 길 중간에 멈춘 로봇 (이슈 #22). state에 멈춘 이유가 아직 없어서, 충전 스테이션이 아닌 곳에서 "대기"면 멈춤으로 본다.
// 충전 스테이션에서 쉬는 로봇과 통로 한가운데 선 로봇을 구분하려는 것. 안전 현황 단계는 올리지 않는다.
// 엣지가 state에 hold(edge/agent.py의 멈춘 이유)를 실어 보내면 reason에 넣는다. 없으면 null (현장 확인 필요)
export const HOLD_TEXT = {
  wait: "교차 확인 대기",
  stop: "관리자 정지",
  guide: "대피 안내 중",
  link: "안전 정지: 보드 연결 끊김",
  stream: "안전 정지: 보드 센서 값 끊김",
  no_ack: "안전 정지: 보드가 명령에 답 없음",
  board: "안전 정지: 보드 동작 오류",
  send: "안전 정지: 보드에 명령 못 보냄",
  line: "안전 정지: 선을 놓침",
  not_ready: "출발 못 함: 보드·센서 준비 안 됨",
  exit: "Pi 프로그램 꺼짐",
};
export function robotStop(msg) {
  if (!msg || msg.status !== "idle" || msg.marker == null || msg.marker === DOCK_MARKER) return null;
  const hold = typeof msg.hold === "string" && msg.hold ? msg.hold : null;
  return { hold, reason: hold ? HOLD_TEXT[hold] ?? hold : null };
}

// 로봇 위치를 글자로. 예: "A 경로 · 마커 8", "충전 스테이션", "마커 14"
// 충전 중인지는 서버가 알려 주지 않아서 짐작하지 않는다
export function robotPlace(msg) {
  if (!msg || msg.marker == null) return "-";
  if (msg.marker === DOCK_MARKER) return "충전 스테이션";
  if (msg.status === "patrol" && msg.role) return `${msg.role} 경로 · 마커 ${msg.marker}`;
  return `마커 ${msg.marker}`;
}

// 화면에 보여 줄 로봇 연결 요약
export function connectionSummary(store, now = Date.now()) {
  const names = [...new Set([...ROBOTS, ...Object.keys(store.robots)])];
  const robots = names.map((name) => ({
    name,
    online: robotOnline(store, name, now),
    lastSeen: store.seen[name] ?? null,
    known: name in store.robots,
  }));
  return {
    server: store.server,
    serverSince: store.serverSince,
    online: robots.filter((r) => r.online).length,
    total: robots.length,
    robots,
  };
}
