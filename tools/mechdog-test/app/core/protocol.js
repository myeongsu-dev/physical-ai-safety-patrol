// CMD|..|$ 형식 (기본 앱 Blue.py와 같은 형식). 장치 종류와 상관없이 같은 형식을 쓴다.
export const frame = (...parts) => `CMD|${parts.join("|")}|$`;

// BLE 알림은 20바이트씩 잘려 올 수 있어서 '$'가 나올 때까지 모아서 한 프레임으로 만든다
export function framer(onFrame) {
  let buf = "";
  return (chunk) => {
    buf += chunk;
    for (let i; (i = buf.indexOf("$")) >= 0; ) {
      const f = buf.slice(0, i), s = f.lastIndexOf("CMD|");
      buf = buf.slice(i + 1);
      if (s < 0) continue;
      const parts = f.slice(s + 4).split("|");
      if (parts.at(-1) === "") parts.pop();
      onFrame(parts);
    }
  };
}

// CMD|V|버전|명령,…|숫자 채널,…|이벤트 채널,…|$  →  { version, commands, channels, events }
const list = (s) => s.split(",").filter(Boolean);
export const parseInfo = ([, version = "", cmds = "", chans = "", events = ""]) => ({
  version, commands: list(cmds), channels: list(chans), events: list(events),
});

// 숫자로 읽히면 숫자, 아니면 글자 그대로
export const value = (s) => (s !== "" && !isNaN(+s) ? +s : s);
