// 결과 저장: 요약 CSV(실행 1회 = 1줄) + 원시 CSV(스트림·이벤트 전부). 엑셀에서 한글이 깨지지 않게 BOM을 붙임
const cell = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };

export function toCsv(rows) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
}

// runs: runner가 만든 실행 기록 배열
export const summaryRows = (runs) => runs.map((r) => ({
  date: r.at.slice(0, 10), time: r.at.slice(11, 19), test: r.test, title: r.title, robot: r.meta.robot, floor: r.meta.floor,
  load_g: r.meta.load, case: r.caseNo, rep: r.rep, ...prefix("c_", r.case), ...prefix("m_", r.metrics), ...prefix("in_", r.manual),
  ok: r.ok ?? "", note: r.meta.note,
}));

export const rawRows = (runs) => runs.flatMap((r, i) => [
  ...r.stream.map((p) => ({ run: i + 1, test: r.test, rep: r.rep, dev: p.dev, kind: "D", t_ms: p.t, ...p.values })),
  ...r.events.map((e) => ({ run: i + 1, test: r.test, rep: r.rep, dev: e.dev, kind: "E", t_ms: e.t, event: e.name, value: e.value })),
]);

function prefix(p, obj = {}) {
  return Object.fromEntries(Object.entries(obj).map(([k, v]) => [p + k, typeof v === "number" ? Math.round(v * 1000) / 1000 : v]));
}

export function download(name, text) {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv" })), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
