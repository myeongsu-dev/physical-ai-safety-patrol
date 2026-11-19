// 연결된 장치 모음. 장치 id(dog, dog2, xiao, pi, server …)로 명령을 보내고, 스트림(D)·이벤트(E)를 구독자에게 넘긴다.
import { frame, framer, parseInfo, value } from "./protocol.js";

const REPLY_MS = 3000;

export function createHub() {
  const devices = new Map(), subs = new Set();
  const emit = (msg) => subs.forEach((f) => f(msg));

  async function add(id, transport, opts) {
    const dev = { id, kind: id.replace(/\d+$/, ""), info: parseInfo([]), live: {}, chans: [], waiting: [], conn: null };
    const onFrame = (parts) => {
      const [k, ...rest] = parts;
      if (k === "D") {
        const t = +rest[0], values = Object.fromEntries(dev.chans.map((c, i) => [c, value(rest[i + 1] ?? "")]));
        Object.assign(dev.live, values);
        return emit({ dev: id, type: "D", t, values });
      }
      if (k === "E") {
        const [t, name, v] = rest;
        dev.live[name] = value(v ?? "");
        return emit({ dev: id, type: "E", t: +t, name, value: value(v ?? "") });
      }
      if (k === "6") dev.live.bat = value(rest[0]);
      const w = dev.waiting.findIndex((x) => x.cmd === k || (k === "!" && x.cmd === rest[0]));
      if (w >= 0) { const x = dev.waiting.splice(w, 1)[0]; clearTimeout(x.timer); k === "!" ? x.reject(new Error(`${rest[0]}: ${rest[1]}`)) : x.resolve(parts); }
      emit({ dev: id, type: "reply", parts });
    };
    const onClose = () => { devices.delete(id); dev.waiting.forEach((x) => x.reject(new Error("연결 끊김"))); emit({ dev: id, type: "closed" }); };
    dev.conn = await transport.connect(framer(onFrame), onClose, opts);
    devices.set(id, dev);
    dev.info = parseInfo(await send(id, "V").catch(() => ["V"]));
    emit({ dev: id, type: "open" });
    return dev;
  }

  // 명령 보내고 같은 이름의 응답(또는 CMD|!|명령|이유|$)을 기다림
  function send(id, cmd, ...args) {
    const dev = devices.get(id);
    if (!dev) return Promise.reject(new Error(`${id} 연결 안 됨`));
    if (cmd === "T") dev.chans = String(args[1] ?? "").split(",").filter((c) => dev.info.channels.includes(c)); // 로봇이 D에 담는 순서와 같게
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { dev.waiting = dev.waiting.filter((x) => x.timer !== timer); reject(new Error(`${id} ${cmd} 응답 없음`)); }, REPLY_MS);
      dev.waiting.push({ cmd, resolve, reject, timer });
      dev.conn.write(frame(cmd, ...args));
    });
  }

  // 시험이 요구하는 장치·명령·채널이 있는지. 없으면 이유 글자
  function missing(needs = {}) {
    for (const [id, req] of Object.entries(needs)) {
      const dev = devices.get(id);
      if (!dev) return `${id} 연결 필요`;
      const c = (req.commands ?? []).filter((x) => !dev.info.commands.includes(x));
      const ch = (req.channels ?? []).filter((x) => !dev.info.channels.includes(x) && !dev.info.events.includes(x));
      if (c.length || ch.length) return `${id}에 없음: ${[...c, ...ch].join(", ")}`;
    }
    return "";
  }

  const stopAll = () => Promise.allSettled([...devices.keys()].flatMap((id) => [send(id, "S"), send(id, "T", 0, "")]));
  return { devices, add, send, missing, stopAll, on: (f) => (subs.add(f), () => subs.delete(f)) };
}
