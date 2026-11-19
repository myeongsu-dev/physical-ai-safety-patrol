// 바닥 카메라가 보는 거리: 몸 중심 앞 15~33cm를 본다는 설계(02 기능정의서)를 확인하고 꺾는 각도를 정함
import { seenRatio, mean, values } from "../core/metrics.js";

export default {
  id: "B1", title: "바닥 카메라 보는 거리", group: "B 바닥 카메라", kind: "measure", refs: ["기술검증: 바닥 카메라 꺾는 각도", "F-02 위치 보정"],
  needs: { dog: { channels: ["line"] } },
  setup: "로봇은 서 있기만 함. 테이프 조각(선 색)을 몸 중심 앞 거리마다 가로로 놓고 3초씩 기록. 먼저 카메라 각도를 위 메모 칸에 적어 둠.",
  params: [{ key: "dist_cm", label: "몸 중심에서 거리(cm)" }],
  cases: [10, 15, 20, 24, 28, 33, 38].map((dist_cm) => ({ dist_cm })),
  prepare: (c) => `테이프를 몸 중심 앞 ${c.dist_cm}cm에 놓으세요`,
  record: { dog: { every: 100, channels: ["line"] } },
  steps: () => [{ wait: 3000 }],
  metrics: { seen: (r) => seenRatio(r.series("line")), x_mean: (r) => mean(values(r.series("line"))) },
};
