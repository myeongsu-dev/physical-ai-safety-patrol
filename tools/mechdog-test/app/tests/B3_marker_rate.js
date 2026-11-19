// 색 띠 마커 인식률 (F-02 합격 기준: 걸으며 마커 20개 중 19개)
import { matchRate } from "../core/metrics.js";

const seen = (r) => r.eventsOf("color").map((e) => String(e.value)).filter((v) => v !== "-");

export default {
  id: "B3", title: "색 띠 마커 인식률", group: "B 바닥 카메라", kind: "accuracy", refs: ["F-02 마커 20개 중 19개"],
  needs: { dog: { commands: ["M"], channels: ["color"] } },
  setup: "비전 모듈에 color_detection 펌웨어. 직선 3m에 두 색 띠 20개를 번갈아 붙임. 정답은 실시간 값에 나오는 색 번호로, 지나간 순서대로 적음 (예: 1 2 1 2 …).",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "ms", label: "시간(ms)" }],
  cases: [{ stride: 30, ms: 15000 }, { stride: 50, ms: 12000 }],
  record: { dog: { every: 50, channels: ["color"] } },
  steps: (c) => [{ do: ["dog", "M", c.stride, 0, c.ms], wait: +c.ms + 300 }],
  truth: { label: "지나간 띠 색 번호 순서 (띄어쓰기)" },
  metrics: {
    recall: (r) => matchRate(seen(r), r.truth).recall,
    false_rate: (r) => matchRate(seen(r), r.truth).falseRate,
    hit: (r) => matchRate(seen(r), r.truth).hit,
  },
  success: (r) => r.metrics.recall >= 19 / 20,
  pass: (runs) => ({ pass: runs.every((r) => r.ok), text: runs.map((r) => `${r.metrics.hit}/${r.truth.length}`).join(", ") + " (기준 19/20)" }),
};
