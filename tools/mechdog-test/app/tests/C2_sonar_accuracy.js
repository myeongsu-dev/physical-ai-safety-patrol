// 초음파 거리별 측정값: 서 있는 상태에서 실제 거리와 읽은 값 비교 (정지·재개 임계값을 정하는 기준)
import { mean, std, values, seenRatio } from "../core/metrics.js";

export default {
  id: "C2", title: "초음파 거리별 정확도", group: "C 초음파 · 막힘", kind: "measure", refs: ["기술검증: 초음파", "F-03 임계값"],
  needs: { dog: { channels: ["sonar"] } },
  setup: "로봇은 서 있기만 한다. 앞에 박스 면을 거리마다 놓고 2초씩 기록.",
  params: [{ key: "dist_cm", label: "실제 거리(cm)" }],
  cases: [5, 10, 14, 18, 25, 40, 60, 100].map((dist_cm) => ({ dist_cm })),
  prepare: (c) => `박스를 몸 앞 ${c.dist_cm}cm에 놓으세요`,
  record: { dog: { every: 50, channels: ["sonar"] } },
  steps: () => [{ wait: 2000 }],
  metrics: {
    mean_cm: (r) => mean(values(r.series("sonar"))),
    std_cm: (r) => std(values(r.series("sonar"))),
    error_cm: (r) => mean(values(r.series("sonar"))) - +r.case.dist_cm,
    seen: (r) => seenRatio(r.series("sonar")),
  },
};
