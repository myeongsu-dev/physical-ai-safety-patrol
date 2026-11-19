// LED 대피 표시 (F-12): 도트 매트릭스로 화살표·✕를 띄우고 몇 m에서 알아보는지.
// 숫자(num)·밝기(bright)는 공식 예제에서 확인된 기능. text·char는 펌웨어에 그 함수가 있을 때만 된다 (없으면 supported 0)
export default {
  id: "D1", title: "LED 표시 보이는 거리", group: "D LED 표시", kind: "measure", refs: ["F-12 화재 대피 안내"],
  needs: { dog: { commands: ["L"] } },
  setup: "로봇을 갈림길 자리에 세우고, 사람 눈높이에서 멀어지며 알아볼 수 있는 최대 거리를 잰다. 밝기는 0~7.",
  params: [{ key: "mode", label: "num/text/char" }, { key: "value", label: "값" }, { key: "bright", label: "밝기" }],
  cases: [
    { mode: "num", value: 88, bright: 7 }, { mode: "text", value: "<", bright: 7 }, { mode: "text", value: ">", bright: 7 },
    { mode: "text", value: "X", bright: 7 }, { mode: "text", value: "<", bright: 3 },
  ],
  steps: (c) => [
    { do: ["dog", "L", "bright", c.bright], soft: true },
    { do: ["dog", "L", c.mode, c.value], soft: true },
    { ask: "LED를 보고 거리를 잰 뒤 확인" },
  ],
  manual: [{ key: "visible_m", label: "알아본 최대 거리(m)" }, { key: "readable", label: "방향을 바로 알아봤나(1/0)" }],
  metrics: { supported: (r) => (r.errors.length ? 0 : 1) },
};
