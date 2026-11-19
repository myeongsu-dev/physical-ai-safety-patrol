// 빈 통로·옆 구조물 오탐 (F-03 "빈 통로 오탐 없음", 기술검증 "코너에서 옆 랙 오인식")
export default {
  id: "C4", title: "빈 통로 · 옆 구조물 오탐", group: "C 초음파 · 막힘", kind: "repeat", refs: ["F-03 빈 통로 오탐 없음", "기술검증: 코너 옆 랙 오인식"],
  needs: { dog: { commands: ["U"], channels: ["sonar"] } },
  setup: "앞이 빈 통로를 걷는다. 조합마다 옆 구조물 배치를 바꾼다: 없음 / 선에서 20cm 옆 가벽 / 코너 직전 옆 랙.",
  params: [{ key: "place", label: "배치" }, { key: "stride", label: "보폭(mm)" }, { key: "ms", label: "시간(ms)" }],
  cases: ["빈 통로", "옆 20cm 가벽", "코너 옆 랙"].map((place) => ({ place, stride: 50, ms: 8000, repeat: 5 })),
  prepare: (c, rep) => `${c.place} · ${rep}번째: 출발 위치에 놓으세요`,
  record: { dog: { every: 50, channels: ["sonar"] } },
  steps: (c) => [{ do: ["dog", "U", c.stride, 14, 18, c.ms], wait: +c.ms + 300 }],
  metrics: { false_stops: (r) => r.eventsOf("stop").length },
  success: (r) => r.eventsOf("stop").length === 0,
  pass: (runs) => {
    const bad = runs.filter((r) => !r.ok).length;
    return { pass: bad === 0, text: `${runs.length}번 중 오탐 ${bad}번 (기준 0번)` };
  },
};
