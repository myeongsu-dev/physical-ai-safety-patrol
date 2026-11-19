// 걸으며 초음파: 걷는 진동 때문에 값이 튀거나 끊기는지 (F-03 "3번 연속" 규칙이 맞는지)
import { valid, seenRatio } from "../core/metrics.js";

const jumps = (r, cm = 10) => valid(r.series("sonar")).filter((p, i, a) => i && Math.abs(p.v - a[i - 1].v) > cm).length;

export default {
  id: "C3", title: "걸으며 초음파", group: "C 초음파 · 막힘", kind: "measure", refs: ["기술검증: 초음파 걸으며 측정"],
  needs: { dog: { commands: ["M"], channels: ["sonar"] } },
  setup: "벽에서 1.5m 떨어져 벽을 향해 세운다. 벽 30cm 앞에서 멈추도록 시간을 짧게.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "ms", label: "시간(ms)" }],
  cases: [{ stride: 30, ms: 8000 }, { stride: 50, ms: 6000 }, { stride: 80, ms: 5000 }],
  record: { dog: { every: 50, channels: ["sonar"] } },
  steps: (c) => [{ do: ["dog", "M", c.stride, 0, c.ms], wait: +c.ms + 300 }],
  metrics: { seen: (r) => seenRatio(r.series("sonar")), jumps_10cm: (r) => jumps(r) },
};
