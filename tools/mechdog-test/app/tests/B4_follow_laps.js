// 선 따라 3바퀴 연속 (F-01 합격 기준: 선 이탈 없이 3바퀴)
import { seenRatio } from "../core/metrics.js";

const SEG = 15000;

export default {
  id: "B4", title: "선 따라 연속 순찰", group: "B 바닥 카메라", kind: "endurance", refs: ["F-01 선 이탈 없이 3바퀴"],
  needs: { dog: { commands: ["F"], channels: ["line"] } },
  setup: "미션맵(또는 같은 곡선 반경의) 순찰 경로 테이프. 시작점 표시. 끝나면 바퀴 수와 선 이탈 횟수를 적음.",
  params: [{ key: "minutes", label: "시간(분)" }, { key: "stride", label: "보폭(mm)" }, { key: "angle", label: "꺾는 각도" }],
  cases: [{ minutes: 3, stride: 50, angle: 25 }],
  record: { dog: { every: 200, channels: ["line", "bat"] } },
  steps: (c) => Array.from({ length: Math.ceil((+c.minutes * 60000) / SEG) }, () => ({ do: ["dog", "F", c.stride, c.angle, SEG], wait: SEG })),
  manual: [{ key: "laps", label: "바퀴 수" }, { key: "off", label: "선 이탈 횟수" }],
  metrics: { line_seen: (r) => seenRatio(r.series("line")), lap_s: (r) => (+r.case.minutes * 60) / +r.manual.laps },
  success: (r) => +r.manual.laps >= 3 && +r.manual.off === 0,
  pass: (runs) => ({ pass: runs.every((r) => r.ok), text: runs.map((r) => `${r.manual.laps}바퀴 · 이탈 ${r.manual.off}`).join(", ") }),
};
