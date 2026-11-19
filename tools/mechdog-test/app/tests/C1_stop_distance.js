// 통로 막힘 정지 (F-03 합격 기준: 10번 중 9번 정지·보고). 로봇 프로그램의 U(초음파 정지 걷기)가 14cm 안 3번 연속이면 멈춤
import { atLeast } from "../core/runner.js";

const stopAt = (r) => +(r.eventsOf("stop")[0]?.value ?? NaN);

export default {
  id: "C1", title: "막힘 앞 정지", group: "C 초음파 · 막힘", kind: "repeat", refs: ["F-03 10번 중 9번 정지", "정지 14cm · 재개 18cm (잠정값)"],
  needs: { dog: { commands: ["U"], channels: ["sonar"] } },
  setup: "통로 가운데 박스(적치물). 로봇은 박스 60cm 앞에서 박스를 향해. 멈추면 몸 앞~박스 거리를 줄자로 잰다.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "stop", label: "정지(cm)" }, { key: "resume", label: "재개(cm)" }, { key: "ms", label: "최대 시간(ms)" }],
  cases: [{ stride: 50, stop: 14, resume: 18, ms: 12000 }],
  repeat: 10,
  prepare: (c, rep) => `${rep}번째: 로봇을 박스 60cm 앞에 놓으세요`,
  record: { dog: { every: 50, channels: ["sonar"] } },
  steps: (c) => [{ do: ["dog", "U", c.stride, c.stop, c.resume, c.ms] }, { until: ["dog", "stop"], timeout: +c.ms }, { do: ["dog", "S"] }],
  manual: [{ key: "real_cm", label: "멈춘 뒤 실제 거리(cm, 안 멈췄으면 비움)" }],
  metrics: { sonar_cm: stopAt, error_cm: (r) => stopAt(r) - +r.manual.real_cm },
  success: (r) => r.eventsOf("stop").length > 0,
  pass: atLeast(9),
};
