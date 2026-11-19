// 바닥 물건 감지 (F-04): 바닥 카메라에 보이는데 초음파는 무반응 = 낮은 물체.
// 기본 비전 모듈은 색만 알아보므로 색이 뚜렷한 작은 물건으로 시험 (실제 물체 인식은 F 묶음 Pi 5에서)
import { valid } from "../core/metrics.js";

const FAR = 25; // 초음파가 이보다 멀면 '무반응'으로 봄 (cm)
function lowHits(r) {
  const sonar = valid(r.series("sonar"));
  return r.eventsOf("color").filter((e) => e.value !== "-" && (sonar.filter((p) => p.t <= e.t).at(-1)?.v ?? 999) > FAR).length;
}

export default {
  id: "B5", title: "바닥 물건 감지", group: "B 바닥 카메라", kind: "accuracy", refs: ["F-04"],
  needs: { dog: { commands: ["M"], channels: ["color", "sonar"] } },
  setup: "color_detection 펌웨어. 직선 경로에 색이 뚜렷한 낮은 물건(공구·부품, 높이 3cm 이하)을 띄엄띄엄 놓음. 정답은 놓은 개수.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "ms", label: "시간(ms)" }],
  cases: [{ stride: 40, ms: 12000 }],
  record: { dog: { every: 50, channels: ["color", "sonar"] } },
  steps: (c) => [{ do: ["dog", "M", c.stride, 0, c.ms], wait: +c.ms + 300 }],
  truth: { label: "놓은 물건 수" },
  metrics: { detected: lowHits, recall: (r) => Math.min(lowHits(r), +r.truth[0]) / +r.truth[0] },
};
