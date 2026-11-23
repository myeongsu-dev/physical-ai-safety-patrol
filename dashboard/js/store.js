// 화면 상태. 서버에서 받은 메시지를 모아 두고, 화면에 보여 줄 값을 계산한다.
// 화면(DOM)은 건드리지 않는다. 그래서 selfcheck.mjs에서 node로 바로 시험할 수 있다.

// 팀 로봇 4대 (Pi의 ROBOT_ID). 서버가 아직 한 번도 못 본 로봇도 "끊김"으로 센다
export const ROBOTS = ["A", "B", "C", "D"];

// 이 시간 안에 state가 와야 연결된 것으로 본다. server/fleet.py의 ONLINE_SEC과 같게 둔다
export const ONLINE_SEC = 5;

export function createStore() {
  return {
    server: "connecting", // connecting · open · closed
    serverSince: Date.now(), // 지금 server 상태가 된 시각 (ms)
    robots: {}, // 로봇 이름 → 마지막 state 메시지
    seen: {}, // 로봇 이름 → state를 받은 시각 (ms). 서버가 끊겨 있다고 알려 주면 null
    mode: "normal",
    events: new Map(), // id → 처리 완료 안 된 경보
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
    // 서버는 로봇 state가 올 때마다 online:true로 보낸다.
    // 화면을 새로 열 때 받는 첫 묶음에는 이미 끊긴 로봇이 online:false로 온다.
    store.seen[msg.robot] = msg.online === false ? null : now;
  } else if (msg.kind === "mode") {
    store.mode = msg.mode;
  } else if (msg.kind === "event") {
    if (msg.checked) store.events.delete(msg.id);
    else store.events.set(msg.id, msg);
  }
}

// 로봇 한 대가 지금 서버와 연결돼 있는지.
// 서버는 로봇이 끊겨도 따로 알려 주지 않고 그 로봇의 state를 더 보내지 않는다.
// 그래서 마지막으로 받은 시각으로 판단한다.
export function robotOnline(store, name, now = Date.now()) {
  const t = store.seen[name];
  return t != null && now - t <= ONLINE_SEC * 1000;
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
