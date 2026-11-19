// 제자리 회전: 보폭을 줄이고 각도를 키우면 얼마나 작게·빠르게 도는지. 갈림길 "멈춤 → 제자리 회전 → 출발" 가능 여부
import { periodOfMin } from "../core/metrics.js";

export default {
  id: "A1", title: "제자리 회전", group: "A 주행 기초", kind: "measure", refs: ["기술검증: 주행·하중", "03 갈림길 회전"],
  needs: { dog: { commands: ["M", "G"], channels: ["sonar"] } },
  setup: "벽 한 면 앞 30cm에 로봇을 벽 쪽으로 세움. 바닥에 시작점(발 네 개)과 앞 방향을 테이프로 표시. 각도판이나 위에서 찍는 폰 영상 준비.",
  params: [{ key: "stride", label: "보폭(mm)" }, { key: "angle", label: "각도" }, { key: "ms", label: "시간(ms)" }, { key: "gait", label: "걸음(들기,딛기,높이)" }],
  cases: [
    { stride: 50, angle: 25, ms: 5000, gait: "" }, { stride: 20, angle: 25, ms: 5000, gait: "" }, { stride: 20, angle: 40, ms: 5000, gait: "" },
    { stride: 0, angle: 40, ms: 5000, gait: "" }, { stride: 1, angle: 40, ms: 5000, gait: "" }, { stride: 1, angle: 40, ms: 5000, gait: "100,300,20" },
  ],
  record: { dog: { every: 50, channels: ["sonar", "bat"] } },
  steps: (c) => [
    ...(c.gait ? [{ do: ["dog", "G", ...String(c.gait).split(",")] }] : []),
    { do: ["dog", "M", c.stride, c.angle, c.ms], wait: +c.ms + 300 },
  ],
  manual: [{ key: "deg", label: "돈 각도(°)" }, { key: "drift_cm", label: "시작점에서 밀린 거리(cm)" }],
  metrics: {
    turn_s: (r) => periodOfMin(r.series("sonar")),                    // 초음파 최소값 주기 = 한 바퀴 시간
    deg_per_s: (r) => +r.manual.deg / (+r.case.ms / 1000),
  },
};
