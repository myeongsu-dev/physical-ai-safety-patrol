// 셀프 체크: node app/selfcheck.mjs
// 가짜 로봇(같은 CMD 형식)으로 프로토콜 → 허브 → 실행기 → 판정까지 돌려 본다. 실제 로봇·브라우저 없이.
import assert from "node:assert/strict";
import { framer, frame } from "./core/protocol.js";
import { periodOfMin, matchRate, slope } from "./core/metrics.js";
import { createHub } from "./core/hub.js";
import { runTest } from "./core/runner.js";
import { toCsv, summaryRows } from "./core/results.js";
import TESTS from "./tests/index.js";

// 프로토콜: 20바이트로 잘려 와도 한 프레임
const got = [];
const feed = framer((p) => got.push(p));
for (const piece of "CMD|D|120|8510|23.4|$CMD|6|85".match(/.{1,7}/g)) feed(piece);
feed("20|$");
assert.deepEqual(got, [["D", "120", "8510", "23.4"], ["6", "8520"]]);
assert.equal(frame("M", 50, -25, 5000), "CMD|M|50|-25|5000|$");

// 계산
const spin = Array.from({ length: 200 }, (_, i) => ({ t: i * 50, v: 30 + 20 * Math.cos((2 * Math.PI * i * 50) / 2000) })); // 2초에 한 바퀴
assert.ok(Math.abs(periodOfMin(spin) - 2) < 0.1);
assert.equal(matchRate(["1", "1", "2", "1", "3"], ["1", "2", "1", "2"]).hit, 3);
assert.ok(Math.abs(slope([{ t: 0, v: 100 }, { t: 1000, v: 90 }, { t: 2000, v: 80 }]) + 10) < 1e-9);

// 가짜 로봇: V·M·U·T·S·G 정도만 흉내. U는 거리가 줄다가 14cm 안이면 stop 이벤트
function fakeRobot({ stopAt = true } = {}) {
  let onChunk, timer, t = 0, dist = 60;
  const say = (...p) => setTimeout(() => onChunk(frame(...p)), 1);
  return {
    connect: async (cb) => {
      onChunk = cb;
      return {
        name: "MechDog_TEST",
        write: async (text) => {
          const [cmd, ...a] = text.slice(4, -2).split("|");
          if (cmd === "V") return say("V", "dogtest-1", "M,F,U,G,S,P,L,C,6,T,V", "bat,sonar,roll,pitch", "");
          if (cmd === "T") {
            clearInterval(timer); t = 0;
            const chans = (a[1] ?? "").split(",").filter(Boolean);
            if (+a[0]) timer = setInterval(() => { t += +a[0]; dist = Math.max(5, dist - 2); onChunk(frame("D", t, ...chans.map((c) => (c === "sonar" ? dist : c === "bat" ? 8400 : 0.5)))); }, 5);
          }
          if (cmd === "U" || cmd === "M") dist = 60; // 걷기마다 벽에서 다시 출발 (안 그러면 앞 시험에서 5cm로 바닥난 채 시작해 타이머가 정확한 Linux에서만 실패)
          if (cmd === "U" && stopAt) setTimeout(() => say("E", t, "stop", 13.5), 30);
          say(cmd, "ok");
        },
        close() {},
      };
    },
  };
}
const ui = { status() {}, confirm: async () => {}, ask: async (_, fields) => Object.fromEntries(fields.map((f) => [f.key, f.key === "truth" ? "1 2 1 2" : "12"])), onRun() {} };
const byId = (id) => TESTS.find((t) => t.id === id);
const go = (hub, test, cases) => runTest(test, { cases, meta: { robot: "T", floor: "", load: "", note: "" }, hub, ui, signal: new AbortController().signal });

// C1 막힘 정지: 10번 모두 stop 이벤트 → 합격, 요약 CSV에 m_sonar_cm
let hub = createHub();
await hub.add("dog", fakeRobot());
assert.equal(hub.missing(byId("C1").needs), "");
assert.match(hub.missing(byId("B3").needs), /color/);      // 이 가짜 로봇엔 color 채널 없음 → 시험 막힘
const c1 = await go(hub, byId("C1"), [{ stride: 50, stop: 14, resume: 18, ms: 300, repeat: 10 }]);
assert.equal(c1.runs.length, 10);
assert.equal(c1.verdict.pass, true);
assert.match(toCsv(summaryRows(c1.runs)), /m_sonar_cm/);

// 정지하지 않는 로봇이면 불합격
hub = createHub();
await hub.add("dog", fakeRobot({ stopAt: false }));
const bad = await go(hub, byId("C1"), [{ stride: 50, stop: 14, resume: 18, ms: 200, repeat: 3 }]);
assert.equal(bad.verdict.pass, false);

// A4 걸음 설정: 스트림이 기록되고 속도(초음파 기울기)가 양수로 나옴
const a4 = await go(hub, byId("A4"), [{ gait: "100,300,20", stride: 50, ms: 200 }]);
assert.ok(a4.runs[0].stream.length > 5 && a4.runs[0].metrics.speed_cm_s > 0);

// 장치가 없는 시험은 막힘
assert.match(createHub().missing(byId("E1").needs), /xiao/);
console.log("selfcheck ok");
process.exit(0);
