// 걸음 설정별 속도·흔들림: 순찰 기본값 정하기. 벽을 향해 걸으며 초음파 거리 변화로 속도를 잰다
import { slope, std, values } from "../core/metrics.js";

export const walkMetrics = {
  speed_cm_s: (r) => -slope(r.series("sonar")),
  roll_std: (r) => std(values(r.series("roll"))),
  pitch_std: (r) => std(values(r.series("pitch"))),
};

export default {
  id: "A4", title: "걸음 설정별 속도", group: "A 주행 기초", kind: "measure", refs: ["기술검증: 주행", "F-01 순찰 속도"],
  needs: { dog: { commands: ["M", "G"], channels: ["sonar", "roll", "pitch"] } },
  setup: "벽에서 1.5m 떨어져 벽을 바라보게 세움 (초음파가 벽을 보게). 벽 30cm 앞까지 오면 멈추도록 시간을 짧게.",
  params: [{ key: "gait", label: "걸음(들기,딛기,높이)" }, { key: "stride", label: "보폭(mm)" }, { key: "ms", label: "시간(ms)" }],
  cases: [{ gait: "150,500,40", stride: 50, ms: 6000 }, { gait: "100,300,20", stride: 50, ms: 6000 }, { gait: "120,400,30", stride: 50, ms: 6000 }, { gait: "100,300,20", stride: 80, ms: 5000 }],
  record: { dog: { every: 100, channels: ["sonar", "roll", "pitch", "bat"] } },
  steps: (c) => [{ do: ["dog", "G", ...String(c.gait).split(",")] }, { do: ["dog", "M", c.stride, 0, c.ms], wait: +c.ms + 300 }],
  manual: [{ key: "note", label: "메모 (넘어짐·미끄러짐 등)", text: true }],
  metrics: walkMetrics,
};
