// 기본 화면. 서버·로봇 연결 상태, 현재 모드, 안전 현황, 최근 경보를 보여 준다. ?admin이면 관리자 화면.
// 지도: 바탕(구역, 통로, 마커, 비상구, 충전 스테이션) 위에 화재 위치, 대피 화살표, (관리자 화면) 로봇 위치를 얹는다.

import {
  createStore, apply, setServer, connectionSummary, safetySummary, openAlerts, robotPlace, alertHistory, robotLabel, firePlaces,
} from "./store.js";
import { connect } from "./conn.js";
import { VIEWBOX, baseMap, legendHtml, robotLayer, fireLayer, guideLayer, MARKER_POS } from "./map.js";
import { createAlarm } from "./sound.js";

const store = createStore();
const params = new URLSearchParams(location.search);
// 주소에 ?admin을 붙이면 관리자 화면. 잠금은 서버와 정한 뒤에 붙인다 (README "화면")
const admin = params.has("admin");

const STATUS_TEXT = { connecting: "연결 중", open: "연결됨", closed: "끊김" };
const MODE_TEXT = { normal: "평상시", evacuate: "대피" };
const LEVEL_TEXT = { safe: "안전", caution: "주의", fire: "화재", unknown: "확인 불가" };

// 로봇 상태 (README "로봇 현황"). 서버 status 값 → 화면 말
const ROBOT_STATUS_TEXT = {
  idle: "대기",
  patrol: "순찰",
  blocked: "막힘 정지",
  heat_check: "과열 확인",
  go: "출동",
  guide: "대피 안내",
  dock: "복귀",
};

// 경보 종류 → 화면 말과 색.
// flame은 로봇 한 대가 과열을 다시 재다가 불꽃까지 본 것(edge/agent.py). 화재는 서버가 교차 확인으로
// 확정하고 대피 모드로 바꿀 때만 띄우므로, flame 경보는 "불꽃 감지"로 보여 준다
const ALERT_TEXT = {
  flame: ["불꽃 감지", "fire"],
  heat: ["과열", "fire"],
  obstacle: ["통로 차단", "warn"],
  floor_object: ["밟힘 주의", "warn"],
};

// 경보 알림 방식 (README "경보", 관리자 화면)
//   통로 차단: 팝업 + 경보음
//   과열, 불꽃 감지, 밟힘 주의: 팝업 없이 최근 경보에서 깜빡임 + 경보음
//   화재(대피 모드): 맨 위 빨간 띠 + 경보음 반복. 관리자가 "경보음 멈춤"을 누르거나 대피가 풀리면 멈춤
const POPUP_TYPES = ["obstacle"];
const BLINK_TYPES = ["heat", "flame", "floor_object"];
const FIRE_REPEAT_MS = 3000;
// 화면을 연 직후 서버가 보내는 첫 묶음(이미 있던 경보)은 새 경보로 치지 않는다
const SNAPSHOT_MS = 500;

// 배터리: 25% 이하면 충전 스테이션으로 복귀, 15% 이하면 그 자리에 멈춤 (server/map_config.py)
const BATTERY_RETURN = 25;
const BATTERY_EMERGENCY = 15;

function render() {
  const now = Date.now();
  const s = connectionSummary(store, now);
  const serverOpen = s.server === "open";

  renderClock(now);

  // 서버
  setItem("server", serverOpen ? "ok" : s.server === "closed" ? "bad" : "", STATUS_TEXT[s.server]);

  // 로봇 "3/4대". 서버가 끊겨 있으면 로봇 상태를 알 수 없다
  if (serverOpen) {
    setItem("robots", s.online === s.total ? "ok" : "bad", `${s.online}/${s.total}대`);
  } else {
    setItem("robots", "", `-/${s.total}대`);
  }

  // 알림 줄
  const notice = document.getElementById("notice");
  const off = s.robots.filter((r) => !r.online);
  if (s.server === "closed") {
    notice.textContent = `서버와 연결이 끊겼습니다. 다시 연결하는 중 (${ago(s.serverSince, now)}째)`;
    notice.hidden = false;
  } else if (serverOpen && off.length) {
    notice.textContent = "로봇 연결 끊김: " + off.map((r) => robotLabel(r.name)).join(", ");
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }

  renderSafety(serverOpen, now);
  if (admin) renderRobots(s, serverOpen, now); // 로봇 현황 카드는 관리자 화면에만 (README "화면별 항목")
  renderAlerts();
  renderFireLayers();
  if (admin) renderRobotLayer(s);
}

// 화재 위치와 대피 화살표 (두 화면 모두). 서버가 값을 보내지 않으면 빈 층
// 대피 표시는 서버가 끊겨도 내리지 않는 것처럼, 화살표도 마지막으로 받은 값을 그대로 둔다
let fireKey = "";
function renderFireLayers() {
  const fires = firePlaces(store);
  const guides = store.mode === "evacuate" ? store.guides : [];
  const key = JSON.stringify([fires, guides]);
  if (key === fireKey) return;
  fireKey = key;
  const map = document.getElementById("map");
  map.querySelector(".fire").innerHTML = fireLayer(fires);
  map.querySelector(".guides").innerHTML = guideLayer(guides);
}

// 현재 모드와 안전 현황. 서버가 끊겨 있으면 모를 값은 "-"
function renderSafety(serverOpen, now) {
  const safe = safetySummary(store, now);
  // 대피 표시는 서버가 끊겨도 내리지 않는다. 마지막으로 받은 모드가 대피면 계속 보여 준다
  const evacuate = safe.mode === "evacuate";
  document.getElementById("evacuate").hidden = !evacuate;
  if (admin) renderEvacActions(evacuate);
  const modeEl = document.getElementById("mode");
  modeEl.textContent = serverOpen || evacuate ? MODE_TEXT[safe.mode] ?? safe.mode : "-";
  modeEl.className = evacuate ? "evacuate-text" : "";
  const levelEl = document.getElementById("level");
  levelEl.textContent = LEVEL_TEXT[safe.level];
  levelEl.className = "level " + safe.level;
  for (const key of ["heat", "obstacle", "floor"]) {
    const el = document.getElementById("count-" + key);
    const n = safe.counts[key];
    el.textContent = serverOpen ? `${n}건` : "-";
    el.className = "count" + (serverOpen && n > 0 ? " has" : "");
  }
}

// 관리자 화면 카드 맨 아래: 로봇 Pi가 돌리는 프로그램의 git 커밋 (state의 version).
// 끊긴 로봇은 마지막으로 받은 값을 그대로 보여 준다
function versionLine(msg) {
  if (!admin || !msg || !msg.version) return "";
  return `<div class="version">버전 ${esc(msg.version)}</div>`;
}

// 로봇 현황 카드
function renderRobots(s, serverOpen, now) {
  document.getElementById("robot-count").textContent = `(${s.total}대)`;
  document.getElementById("robot-list").innerHTML = s.robots
    .map((r) => {
      const msg = store.robots[r.name];
      let cls = "", connText = "확인 불가";
      if (serverOpen) {
        cls = r.online ? "ok" : "bad";
        connText = r.online ? "연결" : "끊김";
      }

      // 오른쪽 위: 연결돼 있으면 상태, 아니면 끊김·확인 불가
      let state = msg ? ROBOT_STATUS_TEXT[msg.status] ?? msg.status ?? "-" : "-";
      if (!r.online) state = connText;

      const meta = [];
      if (msg && msg.battery != null) {
        const b = msg.battery;
        const bcls = b <= BATTERY_EMERGENCY ? "empty" : b <= BATTERY_RETURN ? "low" : "";
        meta.push(`<span class="battery ${bcls}">배터리 ${esc(b)}%</span>`);
      }
      if (serverOpen && !r.online) {
        // 항목마다 span: 카드가 좁으면 "·" 사이에서만 줄이 바뀐다 (css .meta > span)
        if (r.lastSeen) meta.push(`<span>마지막 수신 ${ago(r.lastSeen, now)} 전</span>`);
        else if (!r.known) meta.push("<span>수신 기록 없음</span>");
        // 화면을 열 때 서버가 이미 끊긴 로봇이라고 알려 준 경우. 서버가 마지막 수신 시각은 보내지 않는다
        else meta.push("<span>화면을 열기 전부터 끊김</span>");
      }

      return `<li class="${cls}">
        <div class="top"><span class="dot"></span><span class="name">${esc(robotLabel(r.name))}</span>
          <span class="state">${esc(state)}</span></div>
        <div class="place">${esc(robotPlace(msg))}</div>
        <div class="meta">${meta.join(" · ")}</div>
        ${versionLine(msg)}
      </li>`;
    })
    .join("");
}

// 지도 위 로봇 위치 (관리자 화면만)
function renderRobotLayer(s) {
  const list = s.robots
    .filter((r) => store.robots[r.name]?.marker != null)
    .map((r) => ({ name: robotLabel(r.name), marker: store.robots[r.name].marker, online: s.server === "open" && r.online }));
  document.getElementById("map").querySelector(".robots").innerHTML = robotLayer(list);
}

// ---------- 최근 경보와 경보 상세 ----------
// 관리자 화면에서는 최근 경보를 눌러 오른쪽 경보 상세 칸에서 보고 경보 해제한다.
// 고른 경보가 없으면 가장 새 경보를 보여 준다.
const detail = {
  selected: null, // 고른 경보 id
  pending: null, // 경보 해제를 보내는 중인 id
  error: null, // { id, text }
};
let alertsKey = "";
let detailKey = "";

function shownAlert(list) {
  const open = list.find((e) => e.id === detail.selected);
  if (open) return open;
  // 경보 이력에서 고른 해제된 경보
  const past = store.history.get(detail.selected);
  if (past) return past.event;
  return list[0] ?? null;
}

function renderAlerts() {
  const list = openAlerts(store);
  const shown = admin ? shownAlert(list) : null;
  // 1초마다 다시 그리면 사진이 다시 불러와지고 버튼 포커스가 풀리므로, 바뀐 것이 있을 때만 그린다
  const key = list.map((e) => e.id + (alertState.blink.has(e.id) ? "b" : "")).join(",") + "|" + (shown?.id ?? "");
  if (key !== alertsKey) {
    alertsKey = key;
    document.getElementById("alerts").innerHTML = list.length
      ? list.map((e) => alertItem(e, shown && e.id === shown.id)).join("")
      : `<li class="empty">남은 경보가 없습니다</li>`;
  }
  if (admin) {
    renderDetail(shown);
    renderPopup();
    renderHistory(shown);
  }
}

// ---------- 경보 이력 (관리자 화면) ----------
let historyKey = "";

function renderHistory(shown) {
  const list = alertHistory(store);
  const key = list.map((h) => `${h.event.id}${h.event.checked ? "c" : ""}`).join(",") + "|" + (shown?.id ?? "");
  if (key === historyKey) return;
  historyKey = key;
  document.getElementById("history").innerHTML = list.length
    ? list.map((h) => historyRow(h, shown && h.event.id === shown.id)).join("")
    : `<tr><td colspan="7" class="empty">아직 받은 경보가 없습니다</td></tr>`;
}

function historyRow(h, selected) {
  const e = h.event;
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  const state = h.event.checked ? "경보 해제" : "해제 전";
  return `<tr class="${selected ? "selected" : ""}">
    <td><button type="button" class="row-btn" data-alert="${e.id}" aria-pressed="${selected}">${e.ts ? clock(e.ts * 1000) : "-"}</button></td>
    <td class="type ${cls}">${esc(label)}</td>
    <td>${esc(robotLabel(e.robot))}</td>
    <td>${h.role ? `${esc(h.role)} 경로` : "-"}</td>
    <td>${e.marker != null ? `마커 ${esc(e.marker)}` : "-"}</td>
    <td class="state ${h.event.checked ? "done" : "open"}">${state}</td>
    <td>${e.photo
      ? `<a class="photo-btn" href="${esc(e.photo)}" data-photo="${esc(e.photo)}">사진 보기</a>`
      : `<span class="no-photo">사진 없음</span>`}</td>
  </tr>`;
}

// ---------- 대피 해제 (관리자 화면) ----------
const evac = { confirming: false, pending: false, error: null };
let evacKey = "";

function renderEvacActions(evacuate) {
  const ringing = evacuate && evacAlarm.ringing && alarm.enabled;
  const key = JSON.stringify([evacuate, evac, ringing]);
  if (key === evacKey) return;
  evacKey = key;
  const el = document.getElementById("evacuate-actions");
  if (!evacuate) {
    el.innerHTML = "";
    evac.confirming = false;
    evac.error = null;
    return;
  }
  let html;
  if (evac.pending) {
    html = `<button type="button" class="evac-btn" disabled>보내는 중</button>`;
  } else if (evac.confirming) {
    // 누르기 전에 화면 안에서 한 번 더 확인한다
    html = `<span class="evac-confirm">현장 안전을 확인했나요? 대피를 해제하면 평상시로 돌아갑니다.</span>
      <button type="button" class="evac-btn sure" data-evac="1" data-sure="1">대피 해제</button>
      <button type="button" class="evac-btn" data-evac-cancel="1">취소</button>`;
  } else {
    html = `<button type="button" class="evac-btn" data-evac="1">대피 해제</button>`;
  }
  if (ringing) html += `<button type="button" class="evac-btn" data-evac-mute="1">경보음 멈춤</button>`;
  if (evac.error) html += `<span class="evac-error">${esc(evac.error)}</span>`;
  el.innerHTML = html;
}

async function releaseEvac(sure) {
  if (evac.pending) return;
  if (!sure) {
    evac.confirming = true;
    render();
    return;
  }
  evac.confirming = false;
  evac.pending = true;
  evac.error = null;
  render();
  try {
    const res = await conn.request("/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "normal" }),
    });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    // 서버가 WebSocket으로도 mode를 다시 보내지만, 받은 답으로 바로 반영한다
    apply(store, { kind: "mode", mode: (await res.json()).mode });
  } catch (err) {
    evac.error = `대피 해제를 보내지 못했습니다 (${err.message})`;
  } finally {
    evac.pending = false;
    render();
  }
}

function alertItem(e, selected) {
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  const where = [e.marker != null ? `마커 ${esc(e.marker)}` : "", esc(robotLabel(e.robot))].filter(Boolean).join(" · ");
  const body = `<span class="type ${cls}">${esc(label)}</span>
    <span class="at">${e.ts ? clock(e.ts * 1000) : ""}</span>
    <span class="where">${where}</span>`;
  // 기본 화면에서는 누를 수 없다 (경보 해제는 관리자 화면에서만)
  if (!admin) return `<li>${body}</li>`;
  const liCls = [selected ? "selected" : "", alertState.blink.has(e.id) ? "blink" : ""].join(" ").trim();
  return `<li class="${liCls}"><button type="button" class="alert-btn" data-alert="${e.id}"
    aria-pressed="${selected}">${body}</button></li>`;
}

function renderDetail(e) {
  const key = JSON.stringify([e?.id, e?.checked, detail.pending, detail.error]);
  if (key === detailKey) return;
  detailKey = key;
  const el = document.getElementById("detail");
  if (!e) {
    el.innerHTML = `<p class="empty">남은 경보가 없습니다</p>`;
    return;
  }
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];

  const photo = e.photo
    ? `<a class="photo" href="${esc(e.photo)}" data-photo="${esc(e.photo)}" title="누르면 크게 봅니다">
        <img src="${esc(e.photo)}" alt="${esc(label)} 경보 사진"></a>`
    : `<div class="photo none">사진 없음</div>`;

  // 경보 이력에서 고른 해제된 경보는 버튼 없이 "경보 해제"만
  const past = store.history.get(e.id);
  const action = e.checked
    ? `<p class="done-text">경보 해제</p>`
    : actionHtml(e);
  const err = e.checked ? "" : errorHtml(e);

  const rows = [["시각", e.ts ? `${dateText(e.ts * 1000)} ${clock(e.ts * 1000)}` : "-"], ["로봇", robotLabel(e.robot)]];
  if (past?.role) rows.push(["경로", `${past.role} 경로`]);
  rows.push(["위치", e.marker != null ? `마커 ${e.marker}` : "-"]);
  // 과열은 온도로 판단하는 경보라 로봇이 신뢰도를 0으로 보낸다. 0%로 보이면 오해하니 뺀다
  if (e.conf != null && e.type !== "heat") rows.push(["신뢰도", `${Math.round(e.conf * 100)}%`]);
  if ((e.type === "flame" || e.type === "heat") && e.temp_c != null) rows.push(["온도", `${e.temp_c}°C`]);
  if (e.type === "obstacle" && e.person != null) rows.push(["사람", e.person ? "있음" : "없음"]);

  el.innerHTML = `<div class="detail-head"><span class="type ${cls}">${esc(label)}</span>
      <span class="muted">경보 ${esc(e.id)}번</span></div>
    ${photo}
    ${action}
    ${err}
    <dl class="fields">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`;
}

// 경보 해제 버튼 (경보 상세 칸과 팝업이 같이 쓴다)
function actionHtml(e) {
  if (detail.pending === e.id) {
    return `<button type="button" class="check" disabled>보내는 중</button>`;
  }
  return `<button type="button" class="check" data-check="${e.id}">경보 해제</button>`;
}

function errorHtml(e) {
  return detail.error && detail.error.id === e.id ? `<p class="error">${esc(detail.error.text)}</p>` : "";
}

// ---------- 경보 팝업, 깜빡임, 경보음 (관리자 화면) ----------
const alarm = createAlarm();
const alertState = {
  seen: new Set(), // 이미 본 경보 id (다시 붙어도 또 띄우지 않으려고)
  queue: [], // 팝업으로 띄울 경보 id, 먼저 온 것부터
  blink: new Set(), // 깜빡이는 경보 id (누르면 멈춤)
  openedAt: null, // 첫 연결이 열린 시각
};
let popupKey = "";
// 대피 경보음: 서버가 대피 모드로 바꾸면(화재 확정) 3초마다 반복. 관리자가 멈추거나 대피가 풀리면 멈춤
const evacAlarm = { ringing: false, timer: null };

// 서버 메시지가 올 때마다 부른다. 새 경보면 팝업·깜빡임·경보음
function noticeEvent(msg, now = Date.now()) {
  if (!admin || msg.kind !== "event" || msg.checked || alertState.seen.has(msg.id)) return;
  alertState.seen.add(msg.id);
  if (msg.type === "helmet") return; // 안전모 미착용은 화면 표시가 미정
  const first = alertState.openedAt == null || now - alertState.openedAt < SNAPSHOT_MS;
  if (first) return; // 화면을 열 때 이미 있던 경보
  if (POPUP_TYPES.includes(msg.type)) alertState.queue.push(msg.id);
  if (BLINK_TYPES.includes(msg.type)) alertState.blink.add(msg.id);
  alarm.play(msg.type);
}

// 서버 메시지가 올 때마다 부른다. 평상시 → 대피로 바뀌면 대피 경보음
function noticeMode(msg, prevMode, now = Date.now()) {
  if (!admin || msg.kind !== "mode") return;
  if (msg.mode !== "evacuate") {
    evacAlarm.ringing = false;
    return;
  }
  if (prevMode === "evacuate") return;
  const first = alertState.openedAt == null || now - alertState.openedAt < SNAPSHOT_MS;
  if (first) return; // 화면을 열 때 이미 대피 중이었음
  evacAlarm.ringing = true;
  alarm.play("evacuate");
}

function currentPopup() {
  // 해제됐거나 사라진 경보는 건너뛴다
  while (alertState.queue.length && !store.events.has(alertState.queue[0])) alertState.queue.shift();
  return alertState.queue.length ? store.events.get(alertState.queue[0]) : null;
}

function renderPopup() {
  const e = currentPopup();
  const wrap = document.getElementById("popup");
  const key = JSON.stringify([e?.id, alertState.queue.length, detail.pending, detail.error]);
  syncEvacRepeat();
  if (key === popupKey) return;
  popupKey = key;
  if (!e) {
    wrap.hidden = true;
    wrap.innerHTML = "";
    return;
  }
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  const more = alertState.queue.length - 1;
  const photo = e.photo
    ? `<a class="photo" href="${esc(e.photo)}" data-photo="${esc(e.photo)}" title="누르면 크게 봅니다">
        <img src="${esc(e.photo)}" alt="${esc(label)} 경보 사진"></a>`
    : `<div class="photo none">사진 없음</div>`;
  const pos = MARKER_POS[e.marker];
  const mark = pos
    ? `<circle cx="${pos[0]}" cy="${pos[1]}" r="70" fill="none" stroke="#ff4d4d" stroke-width="12"/>`
    : "";
  const where = [e.marker != null ? `마커 ${esc(e.marker)}` : "", esc(robotLabel(e.robot))].filter(Boolean).join(" · ");

  wrap.innerHTML = `<div class="popup ${cls}" role="alertdialog" aria-modal="true" aria-labelledby="popup-title">
    <div class="popup-head">
      <h2 id="popup-title">${esc(label)} 경보</h2>
      <span class="muted">${e.ts ? clock(e.ts * 1000) : ""}${more > 0 ? ` · 대기 중인 팝업 ${more}건` : ""}</span>
    </div>
    <div class="popup-body">
      ${photo}
      <div class="popup-side">
        <svg class="mini-map" viewBox="${VIEWBOX}" role="img" aria-label="경보 위치: ${where}">${baseMap()}${mark}</svg>
        <p class="popup-where">${where}</p>
      </div>
    </div>
    ${actionHtml(e)}
    ${errorHtml(e)}
    <button type="button" class="popup-close" data-popup-close="1">닫기 (경보 상세에서 다시 볼 수 있음)</button>
  </div>`;
  wrap.hidden = false;
  // 좁은 화면에서 팝업이 버튼 쪽으로 내려가 제목·사진이 가려지지 않게, 초점만 주고 스크롤은 하지 않는다
  wrap.querySelector?.(".check:not([disabled])")?.focus?.({ preventScroll: true });
}

// 대피 중이고 관리자가 멈추지 않았으면 경보음을 반복한다
function syncEvacRepeat() {
  if (store.mode !== "evacuate") evacAlarm.ringing = false;
  const want = evacAlarm.ringing && alarm.enabled;
  if (want && !evacAlarm.timer) evacAlarm.timer = setInterval(() => alarm.play("evacuate"), FIRE_REPEAT_MS);
  if (!want && evacAlarm.timer) {
    clearInterval(evacAlarm.timer);
    evacAlarm.timer = null;
  }
}

function closePopup() {
  const id = alertState.queue.shift();
  if (id != null) detail.selected = id; // 닫은 경보를 오른쪽 경보 상세에 띄워 둔다
  render();
}

function renderSoundButton() {
  const b = document.getElementById("sound");
  if (!alarm.supported) {
    b.textContent = "경보음 지원 안 됨";
    b.disabled = true;
    return;
  }
  if (alarm.blocked) {
    // 켰는데도 브라우저가 소리를 막고 있음. 다시 누르면 끄기, 한 번 더 누르면 다시 시도
    b.textContent = "경보음 막힘 (브라우저 설정 확인)";
    b.className = "sound-btn off";
    b.setAttribute("aria-pressed", "true");
    return;
  }
  b.textContent = alarm.enabled ? "경보음 끄기" : "경보음 켜기";
  b.className = "sound-btn" + (alarm.enabled ? "" : " off");
  b.setAttribute("aria-pressed", String(alarm.enabled));
}

async function checkAlert(id) {
  const e = store.events.get(id);
  if (!e || detail.pending) return;
  detail.pending = id;
  detail.error = null;
  render();
  try {
    const res = await conn.request(`/events/${id}/check`, { method: "POST" });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    // 서버가 WebSocket으로도 checked:true를 다시 보내지만, 받은 답으로 바로 반영한다
    apply(store, await res.json());
    if (detail.selected === id) detail.selected = null;
  } catch (err) {
    detail.error = { id, text: `경보 해제를 보내지 못했습니다 (${err.message})` };
  } finally {
    detail.pending = null;
    render();
  }
}

// ---------- 사진 크게 보기 ----------
// 다른 페이지로 열면 탭이 없는 브라우저(전체 화면, VS Code 안 브라우저)에서 대시보드로 돌아오기 어렵다.
// 그래서 대시보드 위에 띄우고 닫기·Esc로 닫는다
function openViewer(url) {
  const el = document.getElementById("viewer");
  el.innerHTML = `<div class="viewer" role="dialog" aria-modal="true" aria-label="경보 사진">
    <img src="${esc(url)}" alt="경보 사진">
    <button type="button" class="viewer-close" data-viewer-close="1">닫기</button>
  </div>`;
  el.hidden = false;
  el.querySelector?.(".viewer-close")?.focus?.();
}

function closeViewer() {
  const el = document.getElementById("viewer");
  el.hidden = true;
  el.innerHTML = "";
}

function onClick(ev) {
  const t = ev.target.closest?.(
    "[data-alert], [data-check], [data-popup-close], [data-sound], [data-evac], [data-evac-cancel], [data-evac-mute], [data-photo], [data-viewer-close]",
  );
  if (!t) return;
  if (t.dataset.photo) {
    ev.preventDefault?.(); // 링크로 다른 페이지를 열지 않는다
    openViewer(t.dataset.photo);
  } else if (t.dataset.viewerClose) {
    closeViewer();
  } else if (t.dataset.alert) {
    detail.selected = Number(t.dataset.alert);
    alertState.blink.delete(detail.selected); // 눌러서 봤으면 깜빡임을 멈춘다
    render();
  } else if (t.dataset.popupClose) {
    closePopup();
  } else if (t.dataset.evac) {
    releaseEvac(t.dataset.sure === "1");
  } else if (t.dataset.evacCancel) {
    evac.confirming = false;
    render();
  } else if (t.dataset.evacMute) {
    evacAlarm.ringing = false;
    render();
  } else if (t.dataset.sound) {
    if (alarm.enabled) {
      alarm.disable();
      renderSoundButton();
      render();
    } else {
      // 소리 장치가 깨어난 뒤에 켜졌는지 들리게 한 번 울린다
      alarm.enable().then(() => {
        alarm.play("heat");
        renderSoundButton();
        render();
      });
    }
  } else if (t.dataset.check) {
    checkAlert(Number(t.dataset.check));
  }
}

function renderClock(now) {
  document.getElementById("date").textContent = dateText(now);
  document.getElementById("time").textContent = clock(now);
}

function dateText(t) {
  const d = new Date(t);
  const days = ["일", "월", "화", "수", "목", "금", "토"];
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} (${days[d.getDay()]})`;
}

function clock(t) {
  const d = new Date(t);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function setItem(id, cls, text) {
  const el = document.getElementById(id);
  el.className = "conn-item " + cls;
  el.querySelector(".value").textContent = text;
}

function ago(t, now) {
  const sec = Math.max(0, Math.round((now - t) / 1000));
  if (sec < 60) return `${sec}초`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}분`;
  return `${Math.floor(min / 60)}시간 ${min % 60}분`;
}

function esc(x) {
  return String(x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// 지도 바탕은 한 번만 그린다
const mapEl = document.getElementById("map");
mapEl.setAttribute("viewBox", VIEWBOX);
mapEl.innerHTML = baseMap('<g class="fire"></g><g class="guides"></g>') + '<g class="robots"></g>';
document.getElementById("legend").innerHTML = legendHtml({ robots: admin });
if (admin) {
  document.title = "안전 순찰 관제 (관리자)";
  document.getElementById("view-name").hidden = false;
  document.getElementById("robot-panel").hidden = false;
  document.getElementById("detail-panel").hidden = false;
  document.getElementById("history-panel").hidden = false;
  document.getElementById("sound").hidden = false;
  renderSoundButton();
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    // 사진 보기가 떠 있으면 그것부터 닫는다
    if (!document.getElementById("viewer").hidden) closeViewer();
    else if (!document.getElementById("popup").hidden) closePopup();
  });
}

// 위의 함수와 값이 모두 준비된 뒤에 연결을 시작한다 (연결 중에도 바로 render를 부르기 때문)
const conn = connect({
  fake: params.has("fake"),
  onMessage: (msg) => {
    const prevMode = store.mode;
    apply(store, msg);
    noticeEvent(msg);
    noticeMode(msg, prevMode);
    render();
  },
  onStatus: (status) => {
    setServer(store, status);
    if (status === "open" && alertState.openedAt == null) alertState.openedAt = Date.now();
    render();
  },
});
if (conn.fake) window.fake = conn.fake;

// 로봇 state가 안 오는 것도 시간이 지나야 알 수 있으니 1초마다 다시 그린다
setInterval(render, 1000);
