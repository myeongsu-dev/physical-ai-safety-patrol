// 기본 화면. 서버·로봇 연결 상태, 현재 모드, 안전 현황, 최근 경보를 보여 준다. ?admin이면 관리자 화면.
// 지도: 바탕(구역, 통로, 마커, 비상구, 충전 스테이션) 위에 화재 위치, 대피 화살표, (관리자 화면) 로봇 위치를 얹는다.

import {
  createStore, apply, setServer, connectionSummary, safetySummary, openAlerts, robotPlace, alertHistory, robotLabel, evacView, loadHistory, alarmStats, robotStop, robotWaiting, HOLD_TEXT, helmetLog,
} from "./store.js";
import { connect, request } from "./conn.js";
import { FakeServer } from "./fake.js";
import { checkAdmin, withPassword, loadPassword, savePassword } from "./auth.js";
import { VIEWBOX, baseMap, legendHtml, robotLayer, fireLayer, guideLayer, MARKER_POS } from "./map.js";
import { createAlarm } from "./sound.js";
import { DOCK_MARKER, MARKERS } from "./mapdata.js";

const store = createStore();
const params = new URLSearchParams(location.search);
// 안전모 미착용 기록 칸 (관리자 화면, 사진 없음). 팀이 빼기로 하면 false로 바꾸면 된다.
// 꺼도 안전모 경보는 지금처럼 팝업 · 경보음 · 최근 경보 · 경보 이력 · 안전 현황에 나오지 않는다
const SHOW_HELMET = true;
// 주소에 ?admin을 붙이면 관리자 화면. 서버가 잠가 두었으면 비밀번호를 물은 뒤에 연다 (README "관리자 잠금")
const admin = params.has("admin");
// ?fake면 가짜 서버. ?fake&lock이면 가짜 서버도 비밀번호(FAKE_PASSWORD)로 잠그고, ?fake&past면 지난 경보 40건을 미리 넣는다
const FAKE_PASSWORD = "1234";
const fakeServer = params.has("fake")
  ? new FakeServer({ adminPassword: params.has("lock") ? FAKE_PASSWORD : null, past: params.has("past") ? 40 : 0 }).start()
  : null;

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
// flame은 로봇이 카메라로 불꽃을 본 것(edge/agent.py). 화면에는 "불꽃 감지"로 보여 준다.
// 화재는 관리자가 사진이나 현장을 보고 불꽃 감지 팝업의 "화재 확정" 버튼으로 정한다 (대피 모드로 바꿈)
const ALERT_TEXT = {
  flame: ["불꽃 감지", "fire"],
  heat: ["과열", "fire"],
  obstacle: ["통로 차단", "warn"],
  floor_object: ["밟힘 주의", "warn"],
  helmet: ["안전모 미착용", "warn"], // 기록 탭 안전모 칸에서 눌러 볼 때만 (팝업 · 경보음 · 경보 목록에는 안 나옴)
};

// 경보 알림 방식 (README "경보", 관리자 화면)
//   통로 차단 · 밟힘 주의 · 과열 · 불꽃 감지: 팝업 + 경보음 (종류별 소리, js/sound.js)
//   불꽃 감지 소리와 대피 소리는 "경보음 끄기" 상태여도 난다 (FORCE_SOUND)
//   화재(대피 모드): 맨 위 빨간 띠 + 경보음 반복. 관리자가 "경보음 멈춤"을 누르거나 대피가 풀리면 멈춤
// 기본 화면은 새 경보에 팝업 · 경보음이 없고, 요약 칸이나 최근 경보를 누를 때만 위치 팝업
const POPUP_TYPES = ["obstacle", "floor_object", "heat", "flame"];
const FORCE_SOUND = ["flame", "evacuate"];
function playAlarm(type) {
  return alarm.play(type, { force: FORCE_SOUND.includes(type) });
}
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

  // 서버 · 로봇 연결 한 줄. 모두 정상이면 "연결 정상 · 로봇 4/4", 아니면 무엇이 문제인지 빨강
  // 서버가 끊겨 있으면 로봇 상태를 알 수 없다
  if (serverOpen) {
    const allOn = s.online === s.total;
    setItem("conn-status", allOn ? "ok" : "bad", allOn ? `연결 정상 · 로봇 ${s.online}/${s.total}` : `로봇 끊김 · ${s.online}/${s.total}`);
  } else {
    setItem("conn-status", s.server === "closed" ? "bad" : "", s.server === "closed" ? "서버 끊김" : "서버 연결 중");
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
  renderKpis(s, serverOpen, now);
  if (admin) renderTabBadge();
  if (admin) renderRobots(s, serverOpen, now); // 로봇 현황 카드는 관리자 화면에만 (README "화면별 항목")
  renderAlerts();
  renderFireLayers();
  if (admin) renderRobotLayer(s);
}

// 화재 위치와 대피 안내 (두 화면 모두). 서버가 evac 메시지를 보내지 않으면 빈 층
// 대피 표시는 서버가 끊겨도 내리지 않는 것처럼, 대피 안내도 마지막으로 받은 값을 그대로 둔다
let fireKey = "";
function renderFireLayers() {
  const evac = evacView(store);
  const key = JSON.stringify(evac);
  if (key === fireKey) return;
  fireKey = key;
  const map = document.getElementById("map");
  map.querySelector(".fire").innerHTML = fireLayer(evac?.fire);
  map.querySelector(".guides").innerHTML = guideLayer(evac);
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
}

// ---------- 요약 줄 (지도 위) ----------
// 관리자 화면: 로봇 3칸(순찰 중 · 충전 스테이션 · 확인 필요) + 경보 4칸. 기본 화면: 경보 4칸.
// 로봇 3칸은 4대를 나눠 담는다: 충전 스테이션에서 대기, 확인 필요(길 중간에 멈춤 · 연결 끊김), 나머지는 순찰 중
// (순찰 · 출동 · 복귀 · 과열 확인 · 막힘 정지 · 대피 안내). 충전 중인지는 서버가 알려 주지 않아 "충전 스테이션"으로 센다.
// 경보 칸은 해제 안 된 경보 수. 1건 이상이면 색이 바뀌고, 누르면 팝업(여러 건이면 목록에서 고름)
const KPI_ALERTS = [
  ["obstacle", "warn"],
  ["floor_object", "warn"],
  ["heat", "bad"],
  ["flame", "bad"],
];
let kpiKey = "";

function renderKpis(s, serverOpen, now) {
  const tiles = [];
  if (admin) {
    let dock = 0, check = 0;
    for (const r of s.robots) {
      const msg = store.robots[r.name];
      if (!r.online || robotStop(msg)) check += 1;
      else if (msg?.status === "idle" && msg.marker === DOCK_MARKER && !robotWaiting(msg)) dock += 1; // 서버 대기는 순찰 중으로
    }
    tiles.push({ label: "순찰 중", n: s.robots.length - dock - check, unit: "대", tone: "ok" });
    tiles.push({ label: "충전 스테이션", n: dock, unit: "대", tone: "" });
    tiles.push({ label: "확인 필요", n: check, unit: "대", tone: check ? "warn" : "", last: true });
  }
  const open = openAlerts(store, Infinity);
  for (const [type, tone] of KPI_ALERTS) {
    const n = open.filter((e) => e.type === type).length;
    tiles.push({ label: ALERT_TEXT[type][0], n, unit: "건", tone: n ? tone : "", type });
  }
  const key = JSON.stringify([serverOpen, tiles]);
  if (key === kpiKey) return;
  kpiKey = key;
  document.getElementById("kpis").innerHTML = tiles.map((t) => {
    const cls = ["kpi", t.type ? "" : "robot-tile", t.tone, t.last ? "group-end" : ""].filter(Boolean).join(" ");
    // data-k: 칸마다 아이콘 (theme.css. 다크 모드에서 보임)
    const k = t.type ?? { "순찰 중": "patrol", "충전 스테이션": "dock", "확인 필요": "check" }[t.label];
    const inner = `<span class="kpi-label">${esc(t.label)}</span><b>${serverOpen ? t.n : "-"}</b><span class="kpi-unit">${t.unit}</span>`;
    return t.type && serverOpen && t.n > 0
      ? `<button type="button" class="${cls} kpi-btn" data-k="${k}" data-kpi="${t.type}" title="눌러서 경보 보기">${inner}</button>`
      : `<div class="${cls}" data-k="${k}">${inner}</div>`;
  }).join("");
}

// 경보 칸을 눌렀을 때: 1건이면 바로 팝업, 여러 건이면 칸 아래 목록에서 고른다
function pickKpi(type, btn) {
  const list = openAlerts(store, Infinity).filter((e) => e.type === type);
  if (!list.length) return;
  if (list.length === 1) {
    openPopup(list[0].id);
    return;
  }
  const el = document.getElementById("kpi-pick");
  el.innerHTML = `<p class="kpi-pick-title">${esc(ALERT_TEXT[type][0])} ${list.length}건, 볼 경보를 고르세요</p>
    <ul>${list.map((e) => `<li><button type="button" data-pick="${e.id}">
      <span>${e.ts ? clock(e.ts * 1000) : "-"}</span><span>${e.marker != null ? `마커 ${esc(e.marker)}` : "-"}</span>
      <span class="muted">${esc(robotLabel(e.robot))}</span></button></li>`).join("")}</ul>`;
  // 누른 칸 바로 아래. 화면 오른쪽 끝을 넘지 않게
  const r = btn.getBoundingClientRect?.();
  if (r) {
    el.style.left = `${Math.max(8, Math.min(r.left, globalThis.innerWidth - 300))}px`;
    el.style.top = `${r.bottom + (globalThis.scrollY ?? 0) + 6}px`;
  }
  el.hidden = false;
}

function closePick() {
  document.getElementById("kpi-pick").hidden = true;
}

// ---------- 탭 (관리자 화면) ----------
// 실시간 관제: 요약 줄, 지도, 안전 현황, 로봇 현황, 최근 경보
// 기록: 요약 줄, 경보 이력, 안전모 미착용, 정탐률
// 주소 끝 #records면 기록 탭으로 연다 (새로고침해도 그 탭)
let tab = "live";

function setTab(name) {
  tab = name === "records" ? "records" : "live";
  document.getElementById("shell").className = `shell has-tabs tab-${tab}`;
  for (const b of document.querySelectorAll?.("[data-tab]") ?? []) {
    const on = b.dataset.tab === tab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  }
  try {
    globalThis.history?.replaceState?.(null, "", tab === "records" ? "#records" : location.pathname + location.search);
  } catch {
    // 주소를 못 바꿔도 탭은 바뀐다
  }
}

// 실시간 관제 탭 옆 숫자: 해제 안 된 경보 수 (기록 탭을 보는 동안에도 보이게)
let badgeKey = "";
function renderTabBadge() {
  const n = store.server === "open" ? openAlerts(store, Infinity).length : 0;
  const key = String(n);
  if (key === badgeKey) return;
  badgeKey = key;
  const el = document.getElementById("tab-badge");
  el.textContent = n;
  el.hidden = n === 0;
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

      // 오른쪽 위: 연결돼 있으면 상태, 아니면 끊김·확인 불가. 길 중간에 멈췄으면 "멈춤"
      let state = msg ? ROBOT_STATUS_TEXT[msg.status] ?? msg.status ?? "-" : "-";
      const stop = r.online ? robotStop(msg) : null;
      if (stop) {
        state = "멈춤";
        cls += " stopped";
      }
      const waiting = r.online && robotWaiting(msg);
      if (waiting) state = "서버 대기";
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
        ${waiting ? `<div class="wait-reason muted">${esc(HOLD_TEXT.wait)}</div>` : ""}
        ${stop ? `<div class="stop-reason">${esc(stop.reason ?? "현장 확인 필요")}</div>${resumeHtml(r.name, stop)}` : ""}
        <div class="meta">${meta.join(" · ")}</div>
        ${versionLine(msg)}
      </li>`;
    })
    .join("");
}

// ---------- 다시 출발 (관리자 화면) ----------
// 멈춘 로봇(관리자 stop, 안전 정지 ...)은 서버가 자동 출발시키지 않으므로 관리자가 현장을 확인하고 누른다.
// POST /robots/{로봇}/cmd {"cmd": "resume"} (관리자 비밀번호 헤더). Pi는 hold를 풀고 하던 일로 돌아간다.
// 서버 대기(wait)는 멈춤으로 보지 않아 버튼도 없다. 대피 안내(guide)는 대피가 끝나면 서버가 푼다 (PR #31)
const resume = { pending: new Set(), error: null }; // error: { robot, text }

function resumeHtml(robot, stop) {
  // 배터리 부족 정지는 다시 출발을 보내도 Pi가 받지 않는다 (충전이 먼저)
  if (stop.hold === "wait" || stop.hold === "battery") return "";
  const err = resume.error?.robot === robot ? `<p class="error">${esc(resume.error.text)}</p>` : "";
  if (resume.pending.has(robot)) return `<button type="button" class="resume-btn" disabled>보내는 중</button>${err}`;
  return `<button type="button" class="resume-btn" data-resume="${esc(robot)}">다시 출발</button>${err}`;
}

async function resumeRobot(robot) {
  if (resume.pending.has(robot)) return;
  resume.pending.add(robot);
  resume.error = null;
  render();
  try {
    const res = await adminRequest(`/robots/${encodeURIComponent(robot)}/cmd`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cmd: "resume" }),
    });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    // 로봇이 실제로 풀렸는지는 다음 state(hold)로 보인다
  } catch (err) {
    resume.error = { robot, text: `다시 출발을 보내지 못했습니다 (${err.message})` };
  } finally {
    resume.pending.delete(robot);
    render();
  }
}

// 지도 위 로봇 위치 (관리자 화면만)
function renderRobotLayer(s) {
  const list = s.robots
    .filter((r) => store.robots[r.name]?.marker != null)
    .map((r) => {
      const online = s.server === "open" && r.online;
      return { name: robotLabel(r.name), marker: store.robots[r.name].marker, online, stopped: online && !!robotStop(store.robots[r.name]) };
    });
  document.getElementById("map").querySelector(".robots").innerHTML = robotLayer(list);
}

// ---------- 최근 경보 ----------
// 해제 안 된 경보, 새것부터. 누르면 팝업으로 다시 본다 (관리자 화면: 사진 · 해제 버튼, 기본 화면: 위치만)
// release: 팝업의 경보 해제 · 오탐을 보내는 중인 id와 실패 글
const release = {
  pending: null,
  error: null, // { id, text }
};
let alertsKey = null; // 처음 한 번은 꼭 그린다 (경보가 없을 때 "남은 경보가 없습니다")

function renderAlerts() {
  const list = openAlerts(store);
  // 1초마다 다시 그리면 버튼 포커스가 풀리므로, 바뀐 것이 있을 때만 그린다
  const key = list.map((e) => e.id).join(",");
  if (key !== alertsKey) {
    alertsKey = key;
    document.getElementById("alerts").innerHTML = list.length
      ? list.map(alertItem).join("")
      : `<li class="empty">남은 경보가 없습니다</li>`;
  }
  renderPopup();
  if (admin) {
    renderHistory();
    // 정탐률 · 안전모 기록은 각자 바뀐 게 있을 때만 다시 그린다 (안전모 경보는 경보 이력 표에 안 나와서 따로)
    renderStats();
    if (SHOW_HELMET) renderHelmet(Date.now());
  }
}

// ---------- 경보 이력 (관리자 화면) ----------
// 지난 경보는 서버에서 꺼내 온다 (GET /events, PR #23). 처음에 PAST_PAGE건을 받아 정탐률을 세고,
// 표는 종류 버튼으로 거르고 PER_PAGE건씩 쪽 번호로 넘긴다. 받은 것의 마지막 쪽에서 서버에 더 있으면
// "이전 기록 더 받기"로 그 앞의 PAST_PAGE건을 받는다
const PAST_PAGE = 500; // 서버가 한 번에 주는 최대 건수
const PER_PAGE = 10; // 경보 이력 · 안전모 기록 한 쪽에 보이는 건수
// status: idle · loading · done · none(GET /events가 없는 예전 서버) · error
const past = { status: "idle", more: false, error: null };
const hist = { type: "all", page: 0 }; // 경보 이력: 거른 종류, 보고 있는 쪽
const HIST_TYPES = [["all", "전체"], ["obstacle", "통로 차단"], ["floor_object", "밟힘 주의"], ["heat", "과열"], ["flame", "불꽃 감지"]];
let historyKey = "";

// 쪽 번호 (경보 이력 · 안전모 기록). attr: 누를 때 쓰는 data 이름. 첫 쪽, 끝 쪽, 지금 쪽 앞뒤만 번호, 나머지는 …
function pagerHtml(total, page, attr) {
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));
  const nums = [];
  for (let i = 0; i < pages; i++) {
    if (i === 0 || i === pages - 1 || Math.abs(i - page) <= 1) nums.push(i);
    else if (nums[nums.length - 1] !== "gap") nums.push("gap");
  }
  const btns = nums.map((n) => n === "gap"
    ? `<span class="pg-gap">…</span>`
    : `<button type="button" class="pg${n === page ? " on" : ""}" data-${attr}="${n}"${n === page ? ' aria-current="page"' : ""}>${n + 1}</button>`).join("");
  const from = total ? page * PER_PAGE + 1 : 0;
  return `<nav class="pager" aria-label="쪽">
    <button type="button" class="pg" data-${attr}="${page - 1}"${page === 0 ? " disabled" : ""}>이전</button>${btns}
    <button type="button" class="pg" data-${attr}="${page + 1}"${page >= pages - 1 ? " disabled" : ""}>다음</button>
    <span class="pg-info muted">${total}건 중 ${from}–${Math.min(total, (page + 1) * PER_PAGE)}</span>
  </nav>`;
}

// 쪽 번호가 범위를 넘지 않게
function clampPage(page, total) {
  return Math.min(Math.max(0, page), Math.max(0, Math.ceil(total / PER_PAGE) - 1));
}

async function loadPast(before = null) {
  if (past.status === "loading") return;
  past.status = "loading";
  past.error = null;
  render();
  try {
    const q = new URLSearchParams({ limit: String(PAST_PAGE) });
    if (before != null) q.set("before", String(before));
    const res = await conn.request(`/events?${q}`);
    if (res.status === 404 || res.status === 405) {
      past.status = "none";
      return;
    }
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    const list = await res.json();
    loadHistory(store, list);
    past.more = Array.isArray(list) && list.length === PAST_PAGE;
    past.status = "done";
  } catch (err) {
    past.status = "error";
    past.error = `지난 경보를 받지 못했습니다 (${err.message})`;
  } finally {
    render();
  }
}

// 받은 것보다 앞의 지난 경보를 서버에서 더 받는다
function loadOlder() {
  const all = alertHistory(store, Infinity);
  if (past.more && all.length) loadPast(Math.min(...all.map((h) => h.event.id)));
}

function renderHistory() {
  const all = alertHistory(store, Infinity);
  const rows = hist.type === "all" ? all : all.filter((h) => h.event.type === hist.type);
  hist.page = clampPage(hist.page, rows.length);
  const list = rows.slice(hist.page * PER_PAGE, (hist.page + 1) * PER_PAGE);
  const lastPage = (hist.page + 1) * PER_PAGE >= rows.length;
  const counts = HIST_TYPES.map(([t]) => (t === "all" ? all.length : all.filter((h) => h.event.type === t).length));
  const key = list.map((h) => `${h.event.id}${h.event.checked ? (h.event.false_alarm ? "f" : "c") : ""}`).join(",") +
    `|${hist.type}|${hist.page}|${rows.length}|${counts}|${past.status}|${past.more}|${past.error ?? ""}`;
  if (key === historyKey) return;
  historyKey = key;
  document.getElementById("history-tools").innerHTML = HIST_TYPES.map(([t, label], i) =>
    `<button type="button" class="chip${hist.type === t ? " on" : ""}" data-htype="${t}" aria-pressed="${hist.type === t}">${label} <span>${counts[i]}</span></button>`).join("");
  document.getElementById("history").innerHTML = list.length
    ? list.map(historyRow).join("")
    : `<tr><td colspan="7" class="empty">${past.status === "loading" ? "지난 경보를 받는 중" : all.length ? "이 종류의 경보가 없습니다" : "아직 받은 경보가 없습니다"}</td></tr>`;
  document.getElementById("history-sub").textContent = past.status === "none"
    ? "이 화면을 연 뒤에 받은 경보 (서버가 지난 경보를 주지 않음)"
    : "서버에 기록된 경보, 최근 것부터";
  let more = rows.length > 0 ? pagerHtml(rows.length, hist.page, "hpage") : "";
  if (past.error) more += `<p class="error">${esc(past.error)}</p> <button type="button" class="more-btn" data-past-retry="1">다시 받기</button>`;
  else if (past.status === "loading" && all.length) more += `<span class="muted">받는 중</span>`;
  else if (past.more && lastPage) more += `<button type="button" class="more-btn" data-more="1">이전 기록 더 받기</button>`;
  document.getElementById("history-more").innerHTML = more;
}

// 정탐률 (닫은 경보 중 실제였던 비율). 화면이 가진 경보 이력만 센다
let statsKey = "";
function renderStats() {
  const st = alarmStats(store);
  const key = JSON.stringify(st);
  if (key === statsKey) return;
  statsKey = key;
  const pct = (r) => (r.rate == null ? "-" : `${Math.round(r.rate * 100)}%`);
  const row = (label, r) => `<tr><th scope="row">${esc(label)}</th><td>${pct(r)}</td>
    <td class="muted">정탐 ${r.real} · 오탐 ${r.falseAlarm} / ${r.closed}건</td></tr>`;
  document.getElementById("stats").innerHTML = `<table class="stat-table"><tbody>
    ${row("전체", st.total)}${st.rows.map((r) => row(ALERT_TEXT[r.type]?.[0] ?? r.type, r)).join("")}
  </tbody></table>
  <p class="stat-note">정탐은 "경보 해제", 오탐은 "오탐"으로 해제한 것. 해제 전 경보와 로봇이 놓친 위험은 세지 않습니다. 경보 이력에 받아 온 경보 기준</p>`;
}

// 안전모 미착용 기록: 오늘 · 전체 건수, 자주 나온 위치, 기록 10건씩 쪽 번호. 줄을 누르면 사진과 위치
let helmetKey = "";
let helmetPage = 0;
function renderHelmet(now) {
  const h = helmetLog(store, now, Infinity);
  helmetPage = clampPage(helmetPage, h.recent.length);
  const key = JSON.stringify([h, helmetPage, new Date(now).toDateString()]);
  if (key === helmetKey) return;
  helmetKey = key;
  const el = document.getElementById("helmet");
  if (!h.total) {
    el.innerHTML = `<p class="empty">기록이 없습니다</p>`;
    return;
  }
  const top = h.byMarker.slice(0, 3).map((m) => `마커 ${esc(m.marker)} ${m.count}건`).join(", ");
  el.innerHTML = `<p class="helmet-count"><b>오늘 ${h.today}건</b> <span class="muted">받아 온 기록 ${h.total}건</span></p>
    ${top ? `<p class="helmet-where"><span class="muted">자주 나온 위치</span> ${top}</p>` : ""}
    <p class="helmet-hint muted">최근 것부터. 누르면 사진과 위치</p>
    <ul class="helmet-list">${h.recent.slice(helmetPage * PER_PAGE, (helmetPage + 1) * PER_PAGE).map((e) => `<li><button type="button" class="helmet-btn" data-alert="${e.id}">
      <span>${e.ts ? whenText(e.ts) : "-"}</span><span>${e.marker != null ? `마커 ${esc(e.marker)}` : "-"}</span>
      <span class="muted">${esc(robotLabel(e.robot))}</span><span class="helmet-photo${e.photo ? "" : " none"}">${e.photo ? "사진" : "사진 없음"}</span>
    </button></li>`).join("")}</ul>
    ${h.recent.length > PER_PAGE ? pagerHtml(h.recent.length, helmetPage, "mpage") : ""}`;
}

// 경보 시각: 오늘이면 시:분:초, 아니면 월.일 시:분
function whenText(ts) {
  const d = new Date(ts * 1000);
  if (d.toDateString() === new Date().toDateString()) return clock(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 시각을 누르면 팝업으로 (해제된 경보는 버튼 없이 "경보 해제" / "오탐으로 해제")
function historyRow(h) {
  const e = h.event;
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  // 해제 시각은 서버가 알려 줄 때만 (checked_at, PR #23)
  const state = h.event.checked
    ? closedText(e) + (typeof e.checked_at === "number" ? ` <span>${whenText(e.checked_at)}</span>` : "")
    : "해제 전";
  return `<tr>
    <td><button type="button" class="row-btn" data-alert="${e.id}">${e.ts ? whenText(e.ts) : "-"}</button></td>
    <td class="type ${cls}">${esc(label)}</td>
    <td>${esc(robotLabel(e.robot))}</td>
    <td>${h.role ? `${esc(h.role)} 경로` : "-"}</td>
    <td>${e.marker != null ? `마커 ${esc(e.marker)}` : "-"}</td>
    <td class="state ${h.event.checked ? (e.false_alarm ? "done false-alarm" : "done") : "open"}">${state}</td>
    <td>${e.photo
      ? `<a class="photo-btn" href="${esc(e.photo)}" data-photo="${esc(e.photo)}">사진 보기</a>`
      : `<span class="no-photo">사진 없음</span>`}</td>
  </tr>`;
}

// ---------- 대피 해제 (관리자 화면) ----------
const evac = { confirming: false, pending: false, error: null };
let evacKey = "";

function renderEvacActions(evacuate) {
  const ringing = evacuate && evacAlarm.ringing;
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
    const res = await adminRequest("/mode", {
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

function alertItem(e) {
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  const where = [e.marker != null ? `마커 ${esc(e.marker)}` : "", esc(robotLabel(e.robot))].filter(Boolean).join(" · ");
  return `<li><button type="button" class="alert-btn" data-alert="${e.id}">
    <span class="type ${cls}">${esc(label)}</span>
    <span class="at">${e.ts ? clock(e.ts * 1000) : ""}</span>
    <span class="where">${where}</span></button></li>`;
}

// 해제한 경보의 상태 글자. 오탐이면 "오탐으로 해제" (false_alarm은 PR #23 서버부터. 없으면 경보 해제)
function closedText(e) {
  return e.false_alarm ? "오탐으로 해제" : "경보 해제";
}

// 팝업의 경보 해제 버튼 두 개 (관리자 화면)
//   경보 해제: 실제로 일어난 일이고 현장 조치를 확인함 (정탐)
//   오탐: 로봇이 잘못 본 것. 서버에 오탐으로 기록해 나중에 모델을 고칠 때 쓴다
// 불꽃 감지 팝업에만: 관리자가 사진이나 현장을 보고 화재로 확정하면 대피 모드로 바꾼다.
// POST /mode {"mode": "evacuate", "fire": 마커}. 누르기 전에 팝업 안에서 한 번 더 묻는다.
// fire는 서버가 지도에 불난 곳을 그리고 대피 길에서 빼는 데 쓴다 (서버가 받도록 부탁함. 안 받아도 대피 모드는 됨)
const fireConfirm = { asking: null, pending: false, error: null }; // asking: 묻고 있는 경보 id

function fireHtml(e) {
  if (e.type !== "flame") return "";
  if (store.mode === "evacuate") return `<p class="fire-done">대피 모드입니다 (화재 확정됨)</p>`;
  const err = fireConfirm.error ? `<p class="error">${esc(fireConfirm.error)}</p>` : "";
  if (fireConfirm.pending) return `<div class="fire-row"><button type="button" class="fire-btn" disabled>보내는 중</button></div>${err}`;
  if (fireConfirm.asking === e.id) {
    return `<div class="fire-row asking">
      <p class="fire-ask">사진이나 현장에서 불을 확인했나요? 화재로 확정하면 대피 모드가 되어 모든 화면에 대피 안내가 나가고, 로봇이 대피 안내를 합니다.</p>
      <button type="button" class="fire-btn" data-fire="${e.id}" data-sure="1">화재 확정 (대피 모드)</button>
      <button type="button" class="check" data-fire-cancel="1">취소</button>
    </div>${err}`;
  }
  return `<div class="fire-row"><button type="button" class="fire-btn" data-fire="${e.id}">화재 확정 (대피 모드)</button></div>${err}`;
}

async function confirmFire(id, sure) {
  if (fireConfirm.pending) return;
  if (!sure) {
    fireConfirm.asking = id;
    fireConfirm.error = null;
    render();
    return;
  }
  const e = store.history.get(id)?.event;
  fireConfirm.pending = true;
  fireConfirm.error = null;
  render();
  try {
    // 서버는 지도에 없는 마커를 fire로 받으면 400으로 거절한다. 대피는 꼭 되어야 하므로
    // 지도에 있는 마커 번호일 때만 fire를 싣고, 그래도 400이면 fire 없이 한 번 더 보낸다
    const send = (body) => adminRequest("/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const fire = Number.isInteger(e?.marker) && e.marker in MARKERS ? e.marker : null;
    let res = await send(fire != null ? { mode: "evacuate", fire } : { mode: "evacuate" });
    if (res.status === 400 && fire != null) res = await send({ mode: "evacuate" });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    apply(store, { kind: "mode", mode: (await res.json()).mode });
    fireConfirm.asking = null;
  } catch (err) {
    fireConfirm.error = `화재 확정을 보내지 못했습니다 (${err.message})`;
  } finally {
    fireConfirm.pending = false;
    render();
  }
}

function actionHtml(e) {
  if (release.pending === e.id) {
    return `<div class="check-row"><button type="button" class="check" disabled>보내는 중</button></div>`;
  }
  return `<div class="check-row">
    <button type="button" class="check" data-check="${e.id}">경보 해제</button>
    <button type="button" class="check false-alarm" data-check="${e.id}" data-false-alarm="1">오탐</button>
  </div>`;
}

function errorHtml(e) {
  return release.error && release.error.id === e.id ? `<p class="error">${esc(release.error.text)}</p>` : "";
}

// ---------- 경보 팝업, 경보음 ----------
const alarm = createAlarm();
const alertState = {
  seen: new Set(), // 이미 본 경보 id (다시 붙어도 또 띄우지 않으려고)
  queue: [], // 새 경보라서 저절로 띄울 팝업 id, 먼저 온 것부터 (관리자 화면)
  view: null, // 눌러서 연 경보 id (요약 칸, 최근 경보, 경보 이력). 저절로 뜬 팝업보다 먼저 보인다
  openedAt: null, // 첫 연결이 열린 시각
};
let popupKey = "";
// 대피 경보음: 서버가 대피 모드로 바꾸면(화재 확정) 3초마다 반복. 관리자가 멈추거나 대피가 풀리면 멈춤
const evacAlarm = { ringing: false, timer: null };

// 서버 메시지가 올 때마다 부른다. 새 경보면 팝업 · 경보음 (관리자 화면)
function noticeEvent(msg, now = Date.now()) {
  if (!admin || msg.kind !== "event" || msg.checked || alertState.seen.has(msg.id)) return;
  alertState.seen.add(msg.id);
  if (msg.type === "helmet") return; // 안전모 미착용은 현장 음성 안내만 (대시보드에 안 보임)
  const first = alertState.openedAt == null || now - alertState.openedAt < SNAPSHOT_MS;
  if (first) return; // 화면을 열 때 이미 있던 경보
  if (POPUP_TYPES.includes(msg.type)) alertState.queue.push(msg.id);
  playAlarm(msg.type);
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
  playAlarm("evacuate");
}

function currentPopup() {
  // 눌러서 연 경보가 먼저. 해제된 경보(경보 이력)도 열 수 있다
  if (alertState.view != null) {
    const v = store.events.get(alertState.view) ?? store.history.get(alertState.view)?.event;
    if (v) return v;
    alertState.view = null;
  }
  // 해제됐거나 사라진 경보는 건너뛴다
  while (alertState.queue.length && !store.events.has(alertState.queue[0])) alertState.queue.shift();
  return alertState.queue.length ? store.events.get(alertState.queue[0]) : null;
}

// 누른 경보를 팝업으로
function openPopup(id) {
  alertState.view = id;
  closePick();
  render();
}

// 팝업에 보여 줄 값 (관리자 화면). 예전 경보 상세 칸에 있던 것
function popupFields(e) {
  const rows = [["시각", e.ts ? `${dateText(e.ts * 1000)} ${clock(e.ts * 1000)}` : "-"], ["로봇", robotLabel(e.robot)]];
  const role = store.history.get(e.id)?.role;
  if (role) rows.push(["경로", `${role} 경로`]);
  rows.push(["위치", e.marker != null ? `마커 ${e.marker}` : "-"]);
  // 과열은 온도로 판단하는 경보라 로봇이 신뢰도를 0으로 보낸다. 0%로 보이면 오해하니 뺀다
  if (e.conf != null && e.type !== "heat") rows.push(["신뢰도", `${Math.round(e.conf * 100)}%`]);
  if ((e.type === "flame" || e.type === "heat") && e.temp_c != null) rows.push(["온도", `${e.temp_c}°C`]);
  if (e.type === "obstacle" && e.person != null) rows.push(["사람", e.person ? "있음" : "없음"]);
  return `<dl class="fields">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`;
}

// 관리자 화면: 사진, 위치 지도, 값, 경보 해제 · 오탐 (해제된 경보는 "경보 해제" / "오탐으로 해제"만)
// 기본 화면: 위치 지도와 위치 글만 (사진 · 버튼 없음)
function renderPopup() {
  const e = currentPopup();
  const wrap = document.getElementById("popup");
  const key = JSON.stringify([fireConfirm, store.mode, e?.id, e?.checked, e?.false_alarm, alertState.view, alertState.queue.length, release.pending, release.error]);
  syncEvacRepeat();
  if (key === popupKey) return;
  popupKey = key;
  if (!e) {
    wrap.hidden = true;
    wrap.innerHTML = "";
    return;
  }
  const [label, cls] = ALERT_TEXT[e.type] ?? [e.type, ""];
  const more = alertState.view == null ? alertState.queue.length - 1 : 0;
  const pos = MARKER_POS[e.marker];
  const mark = pos
    ? `<circle cx="${pos[0]}" cy="${pos[1]}" r="70" fill="none" stroke="#ff4d4d" stroke-width="12"/>`
    : "";
  const where = [e.marker != null ? `마커 ${esc(e.marker)}` : "", esc(robotLabel(e.robot))].filter(Boolean).join(" · ");
  const mapSide = `<svg class="mini-map" viewBox="${VIEWBOX}" role="img" aria-label="경보 위치: ${where}">${baseMap()}${mark}</svg>
        <p class="popup-where">${where}</p>`;

  let body, actions = "";
  if (admin) {
    const photo = e.photo
      ? `<a class="photo" href="${esc(e.photo)}" data-photo="${esc(e.photo)}" title="누르면 크게 봅니다">
        <img src="${esc(e.photo)}" alt="${esc(label)} 경보 사진"></a>`
      : `<div class="photo none">사진 없음</div>`;
    // 값은 사진 · 지도 아래 한 줄로 (오른쪽 좁은 칸에 넣으면 지도가 작아짐)
    body = `<div class="popup-body">${photo}<div class="popup-side">${mapSide}</div></div>${popupFields(e)}`;
    if (e.type === "helmet") actions = `<p class="done-text">현장에서 음성으로 안내함 (경보 해제 없음)</p>`;
    else actions = fireHtml(e) + (e.checked ? `<p class="done-text">${closedText(e)}</p>` : actionHtml(e) + errorHtml(e));
  } else {
    body = `<div class="popup-body where-only"><div class="popup-side">${mapSide}</div></div>`;
  }

  wrap.innerHTML = `<div class="popup ${cls}" role="alertdialog" aria-modal="true" aria-labelledby="popup-title">
    <div class="popup-head">
      <h2 id="popup-title">${esc(label)} 경보</h2>
      <span class="muted">${e.ts ? clock(e.ts * 1000) : ""}${more > 0 ? ` · 대기 중인 팝업 ${more}건` : ""}</span>
    </div>
    ${body}
    ${actions}
    <button type="button" class="popup-close" data-popup-close="1">${admin ? "닫기 (위 요약 칸이나 최근 경보를 누르면 다시 볼 수 있음)" : "닫기"}</button>
  </div>`;
  wrap.hidden = false;
  // 좁은 화면에서 팝업이 버튼 쪽으로 내려가 제목·사진이 가려지지 않게, 초점만 주고 스크롤은 하지 않는다
  wrap.querySelector?.(".check:not([disabled])")?.focus?.({ preventScroll: true });
}

// 대피 중이고 관리자가 멈추지 않았으면 경보음을 반복한다
function syncEvacRepeat() {
  if (store.mode !== "evacuate") evacAlarm.ringing = false;
  const want = evacAlarm.ringing; // 경보음을 꺼 둬도 대피 소리는 반복 ("경보음 멈춤"으로만 멈춤)
  if (want && !evacAlarm.timer) evacAlarm.timer = setInterval(() => playAlarm("evacuate"), FIRE_REPEAT_MS);
  if (!want && evacAlarm.timer) {
    clearInterval(evacAlarm.timer);
    evacAlarm.timer = null;
  }
}

// 눌러서 연 팝업이면 그것만 닫고, 저절로 뜬 팝업이면 다음 대기 팝업으로
function closePopup() {
  if (alertState.view != null) alertState.view = null;
  else alertState.queue.shift();
  render();
}

// 경보음 버튼: 스피커 아이콘. 꺼져 있으면 사선. 글(켜기 · 끄기)은 화면 읽기와 마우스를 올렸을 때
const SPEAKER = '<path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor"/>';
const WAVES = '<path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>';
const SLASH = '<path d="M3 3l18 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>';
function setSoundButton(b, text, on) {
  b.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">${SPEAKER}${on ? WAVES : SLASH}</svg><span class="sr-only">${text}</span>`;
  b.title = text;
  b.className = "icon-btn sound-btn" + (on ? "" : " off");
}
// 다크 모드: html에 data-theme="dark"를 붙이면 theme.css의 다크 색이 쓰인다. 고른 것은 이 브라우저에 기억
// (index.html 머리의 짧은 스크립트가 화면을 그리기 전에 먼저 켠다)
const THEME_KEY = "dash-theme";
const MOON = '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>';
const SUN = '<circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>';
function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light"; } catch { return "light"; }
}
let theme = savedTheme();
function renderTheme() {
  const root = document.documentElement;
  if (root) {
    if (theme === "dark") root.dataset.theme = "dark";
    else delete root.dataset.theme;
  }
  const b = document.getElementById("theme");
  const text = theme === "dark" ? "밝은 화면으로" : "다크 모드";
  b.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">${theme === "dark" ? SUN : MOON}</svg><span class="sr-only">${text}</span>`;
  b.title = text;
  b.setAttribute("aria-label", text);
  b.setAttribute("aria-pressed", String(theme === "dark"));
}
function toggleTheme() {
  theme = theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* 저장이 막혀 있으면 이번 화면에서만 */ }
  renderTheme();
}
renderTheme();

function renderSoundButton() {
  const b = document.getElementById("sound");
  if (!alarm.supported) {
    setSoundButton(b, "경보음 지원 안 됨", false);
    b.disabled = true;
    return;
  }
  if (alarm.blocked) {
    // 켰는데도 브라우저가 소리를 막고 있음. 다시 누르면 끄기, 한 번 더 누르면 다시 시도
    setSoundButton(b, "경보음 막힘 (브라우저 설정 확인)", false);
    b.setAttribute("aria-pressed", "true");
    return;
  }
  setSoundButton(b, alarm.enabled ? "경보음 끄기" : "경보음 켜기 (불꽃 감지 · 대피 소리는 꺼져 있어도 남)", alarm.enabled);
  b.setAttribute("aria-pressed", String(alarm.enabled));
}

// falseAlarm: 오탐으로 해제. 서버에 {"false_alarm": true}를 보낸다 (body가 없으면 정탐)
async function checkAlert(id, falseAlarm = false) {
  const e = store.events.get(id);
  if (!e || release.pending) return;
  release.pending = id;
  release.error = null;
  render();
  try {
    const opts = falseAlarm
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ false_alarm: true }) }
      : { method: "POST" };
    const res = await adminRequest(`/events/${id}/check`, opts);
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    // 서버가 WebSocket으로도 checked:true를 다시 보내지만, 받은 답으로 바로 반영한다
    apply(store, await res.json());
    // 눌러서 연 팝업도 해제하면 닫는다 (저절로 뜬 팝업은 해제된 경보를 건너뛰어 닫힘)
    if (alertState.view === id) alertState.view = null;
  } catch (err) {
    release.error = { id, text: `경보 해제를 보내지 못했습니다 (${err.message})` };
  } finally {
    release.pending = null;
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
    "[data-alert], [data-check], [data-popup-close], [data-sound], [data-evac], [data-evac-cancel], [data-evac-mute], [data-photo], [data-viewer-close], [data-more], [data-past-retry], [data-hpage], [data-htype], [data-mpage], [data-kpi], [data-pick], [data-tab], [data-resume], [data-fire], [data-fire-cancel], [data-theme-toggle]",
  );
  // 경보 칸 목록 밖을 누르면 목록을 닫는다
  if (!t?.dataset.kpi && !ev.target.closest?.("#kpi-pick")) closePick();
  if (!t) return;
  if (t.dataset.kpi) {
    pickKpi(t.dataset.kpi, t);
  } else if (t.dataset.pick) {
    openPopup(Number(t.dataset.pick));
  } else if (t.dataset.tab) {
    setTab(t.dataset.tab);
  } else if (t.dataset.fire) {
    confirmFire(Number(t.dataset.fire), t.dataset.sure === "1");
  } else if (t.dataset.fireCancel) {
    fireConfirm.asking = null;
    render();
  } else if (t.dataset.resume) {
    resumeRobot(t.dataset.resume);
  } else if (t.dataset.photo) {
    ev.preventDefault?.(); // 링크로 다른 페이지를 열지 않는다
    openViewer(t.dataset.photo);
  } else if (t.dataset.viewerClose) {
    closeViewer();
  } else if (t.dataset.hpage != null) {
    hist.page = Number(t.dataset.hpage);
    render();
  } else if (t.dataset.htype) {
    hist.type = t.dataset.htype;
    hist.page = 0;
    render();
  } else if (t.dataset.mpage != null) {
    helmetPage = Number(t.dataset.mpage);
    render();
  } else if (t.dataset.more) {
    loadOlder();
  } else if (t.dataset.pastRetry) {
    const all = alertHistory(store, Infinity);
    loadPast(past.more && all.length ? Math.min(...all.map((h) => h.event.id)) : null);
  } else if (t.dataset.alert) {
    openPopup(Number(t.dataset.alert));
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
  } else if (t.dataset.themeToggle) {
    toggleTheme();
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
    checkAlert(Number(t.dataset.check), t.dataset.falseAlarm === "1");
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

// ---------- 관리자 잠금 ----------
// 서버 .env에 ADMIN_PASSWORD가 있으면 경보 해제 · 대피 해제는 비밀번호 헤더가 맞아야 받는다 (server/api.py)

// 관리자 요청. 비밀번호를 붙여 보내고, 401이면 비밀번호가 바뀐 것이라 다시 묻는다
async function adminRequest(path, opts) {
  const res = await conn.request(path, withPassword(opts, loadPassword()));
  if (res.status === 401) {
    savePassword("");
    unlockAdmin("관리자 비밀번호가 바뀌었습니다. 다시 입력하세요.");
    throw new Error("관리자 비밀번호가 맞지 않음");
  }
  return res;
}

// 비밀번호가 맞을 때까지 비밀번호 칸을 띄운다. 이미 떠 있으면 그 약속을 같이 기다린다.
// message가 없으면 먼저 저장된 비밀번호(없으면 빈 값)로 물어보고, 맞으면 칸을 띄우지 않는다
let unlocking = null;
function unlockAdmin(message = "") {
  unlocking ??= askPassword(message).finally(() => (unlocking = null));
  return unlocking;
}

async function askPassword(message) {
  const ask = (path, opts) => request(fakeServer, path, opts);
  if (!message) {
    const saved = loadPassword();
    const r = await checkAdmin(ask, saved);
    if (r === "ok") return;
    if (r === "error") message = "서버에 연결할 수 없습니다. 잠시 뒤 다시 누르세요.";
    else if (saved) message = "관리자 비밀번호가 바뀌었습니다. 다시 입력하세요.";
  }
  const wrap = document.getElementById("login");
  const form = document.getElementById("login-form");
  const input = document.getElementById("login-password");
  const errEl = document.getElementById("login-error");
  const btn = document.getElementById("login-btn");
  // 기본모드: 주소에서 admin만 뺀다 (?fake 등은 그대로)
  const q = new URLSearchParams(location.search);
  q.delete("admin");
  const qs = q.toString().replace(/=(&|$)/g, "$1");
  document.getElementById("login-back").href = "./" + (qs ? "?" + qs : "");
  const showError = (text) => {
    errEl.textContent = text;
    errEl.hidden = !text;
  };
  showError(message);
  input.value = "";
  wrap.hidden = false;
  input.focus?.();
  await new Promise((resolve) => {
    form.onsubmit = async (ev) => {
      ev?.preventDefault?.();
      if (btn.disabled) return;
      btn.disabled = true;
      const password = input.value;
      const r = await checkAdmin(ask, password);
      btn.disabled = false;
      if (r === "ok") {
        savePassword(password);
        resolve();
        return;
      }
      showError(r === "wrong" ? "비밀번호가 맞지 않습니다." : "서버에 연결할 수 없습니다. 잠시 뒤 다시 누르세요.");
      input.select?.();
    };
  });
  form.onsubmit = null;
  wrap.hidden = true;
}

// 지도 바탕은 한 번만 그린다
const mapEl = document.getElementById("map");
mapEl.setAttribute("viewBox", VIEWBOX);
mapEl.innerHTML = baseMap('<g class="fire"></g><g class="guides"></g>') + '<g class="robots"></g>';
document.getElementById("legend").innerHTML = legendHtml({ robots: admin });
// 위 막대의 화면 바꾸기 버튼: 기본 화면이면 "관리자모드", 관리자 화면이면 "기본모드".
// 주소의 다른 값(?fake 등)은 그대로 둔다. 관리자 화면에서 나가면 비밀번호도 지워 다음에 다시 묻는다
{
  const q = new URLSearchParams(location.search);
  if (admin) q.delete("admin");
  else q.set("admin", "");
  const qs = q.toString().replace(/=(&|$)/g, "$1");
  const sw = document.getElementById("view-switch");
  sw.href = "./" + (qs ? "?" + qs : "");
  sw.textContent = admin ? "기본모드" : "관리자모드";
  if (admin) sw.onclick = () => savePassword("");
}
// 관리자 화면은 비밀번호가 맞을 때까지 열지 않는다 (서버가 잠그지 않았으면 바로 열림)
if (admin) await unlockAdmin();
if (admin) {
  document.title = "안전 순찰 관제 (관리자)";
  document.getElementById("view-name").hidden = false;
  document.getElementById("robot-panel").hidden = false;
  document.getElementById("history-panel").hidden = false;
  document.getElementById("records-side").hidden = false;
  document.getElementById("helmet-panel").hidden = !SHOW_HELMET;
  document.getElementById("tabs").hidden = false;
  document.getElementById("sound").hidden = false;
  setTab(location.hash === "#records" ? "records" : "live");
  renderSoundButton();
}
// 누르기 · Esc는 두 화면 모두 (기본 화면에는 경보 해제 · 대피 해제 버튼이 없어서 누를 수 있는 것은 경보 보기뿐)
document.addEventListener("click", onClick);
// 불꽃 감지 · 대피 소리는 경보음을 꺼 둬도 나야 하므로, 화면을 누르거나 키를 칠 때마다 소리 장치를 깨워 둔다
document.addEventListener("pointerdown", () => alarm.unlock());
document.addEventListener("keydown", () => alarm.unlock());
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  closePick();
  // 사진 보기가 떠 있으면 그것부터 닫는다
  if (!document.getElementById("viewer").hidden) closeViewer();
  else if (!document.getElementById("popup").hidden) closePopup();
});

// 위의 함수와 값이 모두 준비된 뒤에 연결을 시작한다 (연결 중에도 바로 render를 부르기 때문)
const conn = connect({
  fake: fakeServer,
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
    // 관리자 화면: 서버에 붙을 때마다(다시 붙을 때도) 지난 경보 첫 쪽을 다시 받아 끊긴 동안 해제된 것까지 맞춘다
    if (status === "open" && admin && past.status !== "none") loadPast();
    render();
  },
});
if (conn.fake) window.fake = conn.fake;

// 로봇 state가 안 오는 것도 시간이 지나야 알 수 있으니 1초마다 다시 그린다
setInterval(render, 1000);
