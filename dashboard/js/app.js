// 기본 화면. 서버·로봇 연결 상태, 현재 모드, 안전 현황, 로봇 현황, 최근 경보를 보여 준다.
// 지도는 아직 바탕(구역, 통로, 마커, 비상구, 충전 스테이션)만 그린다.

import { createStore, apply, setServer, connectionSummary, safetySummary, openAlerts } from "./store.js";
import { connect } from "./conn.js";
import { VIEWBOX, baseMap, legendHtml } from "./map.js";

const store = createStore();
const params = new URLSearchParams(location.search);

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
    notice.textContent = "로봇 연결 끊김: " + off.map((r) => r.name).join(", ");
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }

  renderSafety(serverOpen, now);
  renderRobots(s, serverOpen, now);
  renderAlerts();
}

// 현재 모드와 안전 현황. 서버가 끊겨 있으면 모를 값은 "-"
function renderSafety(serverOpen, now) {
  const safe = safetySummary(store, now);
  // 대피 표시는 서버가 끊겨도 내리지 않는다. 마지막으로 받은 모드가 대피면 계속 보여 준다
  const evacuate = safe.mode === "evacuate";
  document.getElementById("evacuate").hidden = !evacuate;
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

      let state = "-";
      if (msg) {
        state = ROBOT_STATUS_TEXT[msg.status] ?? msg.status ?? "-";
        if (msg.status === "patrol" && msg.role) state += ` (${msg.role} 경로)`;
      }

      const meta = [];
      if (msg && msg.battery != null) {
        const b = msg.battery;
        const bcls = b <= BATTERY_EMERGENCY ? "empty" : b <= BATTERY_RETURN ? "low" : "";
        meta.push(`<span class="battery ${bcls}">배터리 ${esc(b)}%</span>`);
      }
      if (msg && msg.marker != null) meta.push(`마커 ${esc(msg.marker)}`);
      if (serverOpen && !r.online) {
        if (r.lastSeen) meta.push(`마지막 수신 ${ago(r.lastSeen, now)} 전`);
        else if (!r.known) meta.push("수신 기록 없음");
        // 화면을 열 때 서버가 이미 끊긴 로봇이라고 알려 준 경우. 서버가 마지막 수신 시각은 보내지 않는다
        else meta.push("화면을 열기 전부터 끊김");
      }

      return `<li class="${cls}">
        <div class="top"><span class="dot"></span><span class="name">${esc(r.name)}</span>
          <span class="conn-text">${connText}</span></div>
        <div class="state">${esc(state)}</div>
        <div class="meta">${meta.join(" · ")}</div>
      </li>`;
    })
    .join("");
}

// 최근 경보 (처리 완료 안 된 것, 새것부터)
function renderAlerts() {
  const list = openAlerts(store);
  document.getElementById("alerts").innerHTML = list.length
    ? list
        .map((e) => {
          const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
          const where = [e.marker != null ? `마커 ${esc(e.marker)}` : "", `로봇 ${esc(e.robot)}`]
            .filter(Boolean)
            .join(" · ");
          return `<li><span class="type ${cls}">${esc(label)}</span>
            <span class="at">${e.ts ? clock(e.ts * 1000) : ""}</span>
            <span class="where">${where}</span></li>`;
        })
        .join("")
    : `<li class="empty">처리할 경보가 없습니다</li>`;
}

function renderClock(now) {
  const d = new Date(now);
  const days = ["일", "월", "화", "수", "목", "금", "토"];
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("date").textContent =
    `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} (${days[d.getDay()]})`;
  document.getElementById("time").textContent = clock(now);
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
mapEl.innerHTML = baseMap();
document.getElementById("legend").innerHTML = legendHtml();

// 위의 함수와 값이 모두 준비된 뒤에 연결을 시작한다 (연결 중에도 바로 render를 부르기 때문)
const conn = connect({
  fake: params.has("fake"),
  onMessage: (msg) => {
    apply(store, msg);
    render();
  },
  onStatus: (status) => {
    setServer(store, status);
    render();
  },
});
if (conn.fake) window.fake = conn.fake;

// 로봇 state가 안 오는 것도 시간이 지나야 알 수 있으니 1초마다 다시 그린다
setInterval(render, 1000);
