// 서버 연결. 진짜 서버의 WebSocket /ws에 붙고, 끊기면 다시 붙는다.
// 주소에 ?fake를 붙이면 가짜 서버(js/fake.js)에 붙는다.

import { FakeServer } from "./fake.js";

const RETRY_MS = 2000; // 끊기면 이만큼 기다렸다가 다시 붙는다

// onMessage(msg): 서버 메시지 하나
// onStatus(status): "connecting" · "open" · "closed"
export function connect({ fake = false, onMessage, onStatus }) {
  const server = fake ? new FakeServer().start() : null;
  let sock = null;
  let timer = null;

  function open() {
    onStatus("connecting");
    if (server) {
      sock = server.socket();
    } else {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      sock = new WebSocket(`${proto}//${location.host}/ws`);
    }
    sock.onopen = () => onStatus("open");
    sock.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        console.warn("깨진 메시지 버림", ev.data);
        return;
      }
      onMessage(msg);
    };
    sock.onclose = () => {
      onStatus("closed");
      clearTimeout(timer);
      timer = setTimeout(open, RETRY_MS);
    };
  }

  open();

  return {
    fake: server, // 가짜 서버일 때만. 개발자 도구에서 fake.emit("heat") 처럼 쓴다
    // 화면 → 서버 요청 (처리 완료, 대피 해제 등)
    request(path, opts) {
      return server ? server.fetch(path, opts) : fetch(path, opts);
    },
  };
}
