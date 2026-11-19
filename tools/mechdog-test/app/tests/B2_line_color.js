// 선 색별 인식: 공식 예제는 노란 선. 검정 선(03 미션맵 기준)을 읽는지, 안 되면 어떤 색 테이프를 살지 정함
import { seenRatio, std, values } from "../core/metrics.js";

export default {
  id: "B2", title: "선 색별 인식", group: "B 바닥 카메라", kind: "measure", refs: ["F-01", "04 재료: 주행선 테이프"],
  needs: { dog: { commands: ["C"], channels: ["line"] } },
  setup: "로봇은 서 있기만 함. 몸 중심 앞 24cm에 색 테이프를 세로로 놓고 색마다 3초씩 기록. 펌웨어에 없는 색 이름이면 그 조합은 '지원 안 함'으로 남음.",
  params: [{ key: "color", label: "색 이름 (YELLOW·RED·GREEN·BLUE·BLACK …)" }],
  cases: ["YELLOW", "BLACK", "RED", "GREEN", "BLUE"].map((color) => ({ color })),
  prepare: (c) => `${c.color} 테이프를 놓으세요`,
  record: { dog: { every: 100, channels: ["line"] } },
  steps: (c) => [{ do: ["dog", "C", c.color], soft: true }, { wait: 3000 }],
  metrics: { seen: (r) => seenRatio(r.series("line")), x_std: (r) => std(values(r.series("line"))), supported: (r) => (r.errors.length ? 0 : 1) },
};
