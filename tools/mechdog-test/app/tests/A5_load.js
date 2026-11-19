// 하중 보행: Pi 5 + 쿨러 + 보조배터리 + 캐리어 무게만큼 추를 얹고 회전·직진·속도를 다시 잼
import { periodOfMin } from "../core/metrics.js";
import { walkMetrics } from "./A4_gait.js";

export default {
  id: "A5", title: "하중 보행", group: "A 주행 기초", kind: "measure", refs: ["기술검증: Pi 5 탑재 보행", "보조배터리 선정"],
  needs: { dog: { commands: ["M", "G"], channels: ["sonar", "roll", "pitch"] } },
  setup: "Pi 5 탑재 위치(뒤 윗판)와 배 아래에 실제와 같은 무게의 추를 테이프로 고정. 위쪽 '하중(g)' 칸에 무게를 적고 시작. 벽 1.5m 앞에서 벽을 향해.",
  params: [{ key: "mode", label: "spin/walk" }, { key: "stride", label: "보폭(mm)" }, { key: "angle", label: "각도" }, { key: "ms", label: "시간(ms)" }],
  cases: [{ mode: "walk", stride: 50, angle: 0, ms: 6000 }, { mode: "walk", stride: 80, angle: 0, ms: 5000 }, { mode: "spin", stride: 1, angle: 40, ms: 5000 }],
  record: { dog: { every: 50, channels: ["sonar", "roll", "pitch", "bat"] } },
  steps: (c) => [{ do: ["dog", "M", c.stride, c.angle, c.ms], wait: +c.ms + 300 }],
  manual: [{ key: "ok", label: "넘어지지 않고 걸었나(1/0)" }, { key: "deg", label: "spin이면 돈 각도(°)" }],
  metrics: { ...walkMetrics, turn_s: (r) => (r.case.mode === "spin" ? periodOfMin(r.series("sonar")) : NaN) },
};
