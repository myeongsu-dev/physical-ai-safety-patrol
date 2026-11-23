// 기본 화면. 지금은 서버·로봇 연결 상태만 보여 준다.

import { createStore, apply, setServer, connectionSummary } from "./store.js";
import { connect } from "./conn.js";

const store = createStore();
const params = new URLSearchParams(location.search);

const STATUS_TEXT = { connecting: "연결 중", open: "연결됨", closed: "끊김" };

function render() {
  const now = Date.now();
  const s = connectionSummary(store, now);
  const serverOpen = s.server === "open";

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

  // 로봇 목록
  document.getElementById("robot-list").innerHTML = s.robots
    .map((r) => {
      let cls = "", state = "확인 불가", seen = "";
      if (serverOpen) {
        cls = r.online ? "ok" : "bad";
        state = r.online ? "연결" : "끊김";
      }
      if (r.lastSeen) seen = `마지막 수신 ${ago(r.lastSeen, now)} 전`;
      else if (serverOpen && !r.known) seen = "수신 기록 없음";
      // 화면을 열 때 서버가 이미 끊긴 로봇이라고 알려 준 경우. 서버가 마지막 수신 시각은 보내지 않는다
      else if (serverOpen && !r.online) seen = "화면을 열기 전부터 끊김";
      return `<li class="${cls}"><span class="dot"></span><span class="name">${esc(r.name)}</span>
        <span class="state">${state}</span><span class="seen">${seen}</span></li>`;
    })
    .join("");
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
