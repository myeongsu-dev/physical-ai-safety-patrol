// 배터리 지속: 연속으로 선을 따라 걸으며 전압을 기록. F-11 충전 교대 기준("20% 미만")에 쓸 전압값을 정함
import { first, last, slope } from "../core/metrics.js";

const SEG = 15000; // 이동 명령 한 번 최대 15초라 잘라서 이어 보냄

export default {
  id: "A6", title: "배터리 지속", group: "A 주행 기초", kind: "endurance", refs: ["F-11 충전 교대"],
  needs: { dog: { commands: ["F"], channels: ["bat", "line"] } },
  setup: "한 바퀴 이어진 선(원이나 사각형). 완충 후 시작. 중간에 멈추려면 정지 버튼.",
  params: [{ key: "minutes", label: "시간(분)" }, { key: "stride", label: "보폭(mm)" }],
  cases: [{ minutes: 20, stride: 50 }],
  record: { dog: { every: 1000, channels: ["bat"] } },
  steps: (c) => Array.from({ length: Math.ceil((+c.minutes * 60000) / SEG) }, () => ({ do: ["dog", "F", c.stride, 25, SEG], wait: SEG })),
  manual: [{ key: "note", label: "메모 (느려짐·멈춤 시각 등)", text: true }],
  metrics: {
    v_start: (r) => first(r.series("bat")), v_end: (r) => last(r.series("bat")),
    v_drop_per_min: (r) => -slope(r.series("bat")) * 60,
  },
};
