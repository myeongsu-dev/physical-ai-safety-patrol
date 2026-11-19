// 통로 재점검 (F-09): 멈춘 뒤 관리자가 박스를 치우면 로봇이 스스로 다시 걷는지
export default {
  id: "C5", title: "치운 뒤 재출발", group: "C 초음파 · 막힘", kind: "repeat", refs: ["F-09 통로 재점검"],
  needs: { dog: { commands: ["U"], channels: ["sonar"] } },
  setup: "박스 60cm 앞에서 출발. 로봇이 멈추면 안내에 따라 박스를 치운다.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "stop", label: "정지(cm)" }, { key: "resume", label: "재개(cm)" }],
  cases: [{ stride: 50, stop: 14, resume: 18 }],
  repeat: 5,
  prepare: (c, rep) => `${rep}번째: 로봇을 박스 60cm 앞에 놓으세요`,
  record: { dog: { every: 50, channels: ["sonar"] } },
  steps: (c) => [
    { do: ["dog", "U", c.stride, c.stop, c.resume, 15000] },
    { until: ["dog", "stop"], timeout: 12000 },
    { ask: "로봇이 멈췄으면 박스를 치우고 확인을 누르세요" },
    { until: ["dog", "resume"], timeout: 5000 },
    { wait: 1000 },
    { do: ["dog", "S"] },
  ],
  metrics: { resume_ms: (r) => (r.eventsOf("resume")[0]?.t ?? NaN) - (r.eventsOf("stop")[0]?.t ?? NaN) },
  success: (r) => r.eventsOf("stop").length > 0 && r.eventsOf("resume").length > 0,
  pass: (runs) => ({ pass: runs.every((r) => r.ok), text: `${runs.length}번 중 ${runs.filter((r) => r.ok).length}번 재출발` }),
};
