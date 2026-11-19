// 걸으며 회전 반경: 간선 60cm · 도크 70cm(곡선 진입 25 + U턴 25) 안에서 돌 수 있는지
export default {
  id: "A2", title: "걸으며 회전 반경", group: "A 주행 기초", kind: "measure", refs: ["기술검증: 곡선 반지름", "03 간선 60cm · 도크 70cm"],
  needs: { dog: { commands: ["M"] } },
  setup: "넓은 바닥. 시작점에 몸 중심 표시. 반 바퀴(180°) 돌았을 때 몸 중심 위치를 표시해 시작점과의 거리(=지름)를 잰다.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "angle", label: "각도" }, { key: "ms", label: "시간(ms)" }],
  cases: [50, 80].flatMap((stride) => [15, 25, 40].map((angle) => ({ stride, angle, ms: 8000 }))),
  record: { dog: { every: 200, channels: ["bat"] } },
  steps: (c) => [{ do: ["dog", "M", c.stride, c.angle, c.ms], wait: +c.ms + 300 }],
  manual: [{ key: "diameter_cm", label: "반 바퀴 지름(cm)" }, { key: "half_s", label: "반 바퀴 걸린 시간(초, 영상)" }],
  metrics: { radius_cm: (r) => +r.manual.diameter_cm / 2 },
};
