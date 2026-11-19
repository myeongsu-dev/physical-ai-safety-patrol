// 직진 흔들림: 선 ↔ 랙·작업 공간 간격 20cm (로봇 폭 절반 6.5 + 좌우 흔들림 5 + 여유 8.5) 가정 확인
import { std, values, mean, seenRatio } from "../core/metrics.js";

export default {
  id: "A3", title: "직진 흔들림", group: "A 주행 기초", kind: "measure", refs: ["기술검증: 직선 흔들림", "03 간격 20cm"],
  needs: { dog: { commands: ["M", "F"], channels: ["line"] } },
  setup: "3m 직선 테이프 (비전 모듈 line_patrol 펌웨어가 읽는 색). 시작점에서 몸 중심을 선 위에 맞춤. 'follow'가 0이면 선 추종 없이 직진만, 1이면 선 따라가기.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "follow", label: "선 추종(0/1)" }, { key: "angle", label: "꺾는 각도" }, { key: "ms", label: "시간(ms)" }],
  cases: [{ stride: 50, follow: 0, angle: 0, ms: 12000 }, { stride: 80, follow: 0, angle: 0, ms: 9000 }, { stride: 50, follow: 1, angle: 25, ms: 12000 }],
  record: { dog: { every: 100, channels: ["line", "roll", "pitch"] } },
  steps: (c) => [{ do: c.follow == 1 ? ["dog", "F", c.stride, c.angle, c.ms] : ["dog", "M", c.stride, 0, c.ms], wait: +c.ms + 300 }],
  manual: [{ key: "side_cm", label: "끝에서 선 옆으로 밀린 거리(cm)" }, { key: "dist_m", label: "간 거리(m)" }],
  metrics: {
    line_x_std: (r) => std(values(r.series("line"))),                  // 화면 x(0~160) 표준편차: 클수록 좌우로 흔들림
    line_x_mean: (r) => mean(values(r.series("line"))),
    line_seen: (r) => seenRatio(r.series("line")),                      // 선이 화면에 보인 비율
    side_cm_per_m: (r) => +r.manual.side_cm / +r.manual.dist_m,
  },
};
