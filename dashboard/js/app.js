// 기본 화면. 서버·로봇 연결 상태, 현재 모드, 안전 현황, 로봇 현황, 최근 경보를 보여 준다.
// 지도는 아직 바탕(구역, 통로, 마커, 비상구, 충전 스테이션)만 그린다.

import {
  createStore, apply, setServer, connectionSummary, safetySummary, openAlerts, robotPlace, alertHistory, robotLabel,
} from "./store.js";
import { connect } from "./conn.js";
import { VIEWBOX, baseMap, legendHtml, robotLayer, MARKER_POS } from "./map.js";
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

// 경보 종류 → 화면 말과 색
const ALERT_TEXT = {
  flame: ["화재", "fire"],
  heat: ["과열", "fire"],
  obstacle: ["통로 차단", "warn"],
  floor_object: ["밟힘 주의", "warn"],
};

// 경보 알림 방식 (README "경보", 관리자 화면)
//   화재, 통로 차단: 팝업 + 경보음 (화재는 팝업이 떠 있는 동안 경보음 반복)
//   과열, 밟힘 주의: 팝업 없이 최근 경보에서 깜빡임 + 경보음
const POPUP_TYPES = ["flame", "obstacle"];
const BLINK_TYPES = ["heat", "floor_object"];
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
  renderRobots(s, serverOpen, now);
  renderAlerts();
  if (admin) renderRobotLayer(s);
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
  for (const key of ["fire", "obstacle", "floor"]) {
    const el = document.getElementById("count-" + key);
    const n = safe.counts[key];
    el.textContent = serverOpen ? `${n}건` : "-";
    el.className = "count" + (serverOpen && n > 0 ? " has" : "");
  }
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
        if (r.lastSeen) meta.push(`마지막 수신 ${ago(r.lastSeen, now)} 전`);
        else if (!r.known) meta.push("수신 기록 없음");
        // 화면을 열 때 서버가 이미 끊긴 로봇이라고 알려 준 경우. 서버가 마지막 수신 시각은 보내지 않는다
        else meta.push("화면을 열기 전부터 끊김");
      }

      return `<li class="${cls}">
        <div class="top"><span class="dot"></span><span class="name">${esc(robotLabel(r.name))}</span>
          <span class="state">${esc(state)}</span></div>
        <div class="place">${esc(robotPlace(msg))}</div>
        <div class="meta">${meta.join(" · ")}</div>
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
  confirming: null, // 화재: 한 번 더 확인을 기다리는 id
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
  const key = list.map((h) => `${h.event.id}${h.checkedAt ? "c" : ""}`).join(",") + "|" + (shown?.id ?? "");
  if (key === historyKey) return;
  historyKey = key;
  document.getElementById("history").innerHTML = list.length
    ? list.map((h) => historyRow(h, shown && h.event.id === shown.id)).join("")
    : `<tr><td colspan="7" class="empty">아직 받은 경보가 없습니다</td></tr>`;
}

function historyRow(h, selected) {
  const e = h.event;
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  const state = h.checkedAt ? `경보 해제 ${clock(h.checkedAt)}` : "해제 전";
  return `<tr class="${selected ? "selected" : ""}">
    <td><button type="button" class="row-btn" data-alert="${e.id}" aria-pressed="${selected}">${e.ts ? clock(e.ts * 1000) : "-"}</button></td>
    <td class="type ${cls}">${esc(label)}</td>
    <td>${esc(robotLabel(e.robot))}</td>
    <td>${h.role ? `${esc(h.role)} 경로` : "-"}</td>
    <td>${e.marker != null ? `마커 ${esc(e.marker)}` : "-"}</td>
    <td class="${h.checkedAt ? "done" : "open"}">${state}</td>
    <td>${e.photo
      ? `<a class="photo-btn" href="${esc(e.photo)}" target="_blank" rel="noopener">사진 보기</a>`
      : `<span class="no-photo">사진 없음</span>`}</td>
  </tr>`;
}

// ---------- 대피 해제 (관리자 화면) ----------
const evac = { confirming: false, pending: false, error: null };
let evacKey = "";

function renderEvacActions(evacuate) {
  const key = JSON.stringify([evacuate, evac]);
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
  const key = JSON.stringify([e?.id, e?.checked, detail.pending, detail.confirming, detail.error]);
  if (key === detailKey) return;
  detailKey = key;
  const el = document.getElementById("detail");
  if (!e) {
    el.innerHTML = `<p class="empty">남은 경보가 없습니다</p>`;
    return;
  }
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];

  const photo = e.photo
    ? `<a class="photo" href="${esc(e.photo)}" target="_blank" rel="noopener" title="누르면 크게 봅니다">
        <img src="${esc(e.photo)}" alt="${esc(label)} 경보 사진"></a>`
    : `<div class="photo none">사진 없음</div>`;

  // 경보 이력에서 고른 해제된 경보는 버튼 없이 해제 시각만
  const past = store.history.get(e.id);
  const action = e.checked
    ? `<p class="done-text">경보 해제 ${past?.checkedAt ? clock(past.checkedAt) : ""}</p>`
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
  if (detail.confirming === e.id) {
    // 화재는 경보 해제 전에 화면 안에서 한 번 더 확인한다. 대피 해제는 따로 한다
    return `<p class="confirm-text">화재 경보를 해제할까요? 대피 모드는 따로 해제해야 합니다.</p>
      <div class="actions"><button type="button" class="check danger" data-check="${e.id}" data-sure="1">경보 해제</button>
      <button type="button" class="cancel" data-cancel="1">취소</button></div>`;
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
let fireTimer = null;

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

function currentPopup() {
  // 해제됐거나 사라진 경보는 건너뛴다
  while (alertState.queue.length && !store.events.has(alertState.queue[0])) alertState.queue.shift();
  return alertState.queue.length ? store.events.get(alertState.queue[0]) : null;
}

function renderPopup() {
  const e = currentPopup();
  const wrap = document.getElementById("popup");
  const key = JSON.stringify([e?.id, alertState.queue.length, detail.pending, detail.confirming, detail.error]);
  syncFireRepeat(e);
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
    ? `<a class="photo" href="${esc(e.photo)}" target="_blank" rel="noopener" title="누르면 크게 봅니다">
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
    <button type="button" class="popup-close" data-popup-close="1">닫기 (오른쪽 경보 상세에서 다시 볼 수 있음)</button>
  </div>`;
  wrap.hidden = false;
  wrap.querySelector?.(".check:not([disabled])")?.focus?.();
}

// 화재 팝업이 떠 있는 동안 경보음을 반복한다
function syncFireRepeat(e) {
  const want = e && e.type === "flame" && alarm.enabled;
  if (want && !fireTimer) fireTimer = setInterval(() => alarm.play("flame"), FIRE_REPEAT_MS);
  if (!want && fireTimer) {
    clearInterval(fireTimer);
    fireTimer = null;
  }
}

function closePopup() {
  const id = alertState.queue.shift();
  if (id != null) detail.selected = id; // 닫은 경보를 오른쪽 경보 상세에 띄워 둔다
  detail.confirming = null;
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

async function checkAlert(id, sure) {
  const e = store.events.get(id);
  if (!e || detail.pending) return;
  if (e.type === "flame" && !sure) {
    detail.confirming = id;
    render();
    return;
  }
  detail.confirming = null;
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

function onClick(ev) {
  const t = ev.target.closest?.(
    "[data-alert], [data-check], [data-cancel], [data-popup-close], [data-sound], [data-evac], [data-evac-cancel]",
  );
  if (!t) return;
  if (t.dataset.alert) {
    detail.selected = Number(t.dataset.alert);
    detail.confirming = null;
    alertState.blink.delete(detail.selected); // 눌러서 봤으면 깜빡임을 멈춘다
    render();
  } else if (t.dataset.popupClose) {
    closePopup();
  } else if (t.dataset.evac) {
    releaseEvac(t.dataset.sure === "1");
  } else if (t.dataset.evacCancel) {
    evac.confirming = false;
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
    checkAlert(Number(t.dataset.check), t.dataset.sure === "1");
  } else if (t.dataset.cancel) {
    detail.confirming = null;
    render();
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
mapEl.innerHTML = baseMap() + '<g class="robots"></g>';
document.getElementById("legend").innerHTML = legendHtml({ robots: admin });
if (admin) {
  document.title = "안전 순찰 관제 (관리자)";
  document.getElementById("view-name").hidden = false;
  document.getElementById("detail-panel").hidden = false;
  document.getElementById("history-panel").hidden = false;
  document.getElementById("sound").hidden = false;
  renderSoundButton();
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !document.getElementById("popup").hidden) closePopup();
  });
}

// 위의 함수와 값이 모두 준비된 뒤에 연결을 시작한다 (연결 중에도 바로 render를 부르기 때문)
const conn = connect({
  fake: params.has("fake"),
  onMessage: (msg) => {
    apply(store, msg);
    noticeEvent(msg);
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
