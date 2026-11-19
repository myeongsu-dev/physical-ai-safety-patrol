// 시험 실행기. 시험 정의(tests/*.js)를 받아 조합 × 반복마다:
// 준비 안내 → 기록 시작(T) → 단계 실행 → 기록 끝 → 손 측정·정답 입력 → 자동 계산 → 성공 판정
// 시험 종류(kind): measure 측정 · repeat 반복 판정 · accuracy 인식 정확도 · latency 지연 · endurance 지속
export const KINDS = { measure: "측정", repeat: "반복 판정", accuracy: "인식 정확도", latency: "지연", endurance: "지속" };

const sleep = (ms, signal) => new Promise((ok, no) => {
  const t = setTimeout(ok, ms);
  signal?.addEventListener("abort", () => { clearTimeout(t); no(new Error("중단")); }, { once: true });
});

// 실행 1회의 기록을 시험 정의가 쓰기 쉽게 감싼 것
function makeRun(test, c, caseNo, rep, meta) {
  const run = { test: test.id, title: test.title, at: new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString(),
    case: c, caseNo, rep, meta: { ...meta }, stream: [], events: [], errors: [], manual: {}, truth: null, metrics: {}, ok: null };
  run.series = (ch, dev = "dog") => run.stream.filter((p) => p.dev === dev && ch in p.values).map((p) => ({ t: p.t, v: p.values[ch] }));
  run.eventsOf = (name, dev = "dog") => run.events.filter((e) => e.dev === dev && e.name === name);
  return run;
}

// 단계 형식: { do: [장치, 명령, ...값], wait: ms, soft } · { wait: ms } · { ask: "안내 글자" } · { until: [장치, 이벤트], timeout: ms }
// soft: 명령이 실패해도 시험을 멈추지 않고 run.errors에 남김 (지원 여부를 보는 시험용)
async function runStep(step, { hub, ui, signal, run }) {
  if (signal.aborted) throw new Error("중단");
  if (step.ask) await ui.confirm(step.ask);
  if (step.do) {
    const [dev, cmd, ...args] = step.do;
    ui.status(`${dev} ${cmd} ${args.join(" ")}`);
    try { await hub.send(dev, cmd, ...args); } catch (e) { if (!step.soft) throw e; run.errors.push(e.message); }
  }
  if (step.until) {
    const [dev, name] = step.until;
    await Promise.race([
      new Promise((ok) => { const iv = setInterval(() => run.eventsOf(name, dev).length && (clearInterval(iv), ok()), 50); signal.addEventListener("abort", () => clearInterval(iv)); }),
      sleep(step.timeout ?? 15000, signal),
    ]);
  }
  if (step.wait) await sleep(step.wait, signal);
}

export async function runTest(test, { cases, repeat = 1, meta, hub, ui, signal }) {
  const runs = [];
  for (const [i, c] of cases.entries()) {
    for (let rep = 1; rep <= (c.repeat ?? test.repeat ?? repeat); rep++) {
      const run = makeRun(test, c, i + 1, rep, meta);
      ui.status(`${test.id} 조합 ${i + 1}/${cases.length} · ${rep}회`);
      if (test.prepare) await ui.confirm(typeof test.prepare === "function" ? test.prepare(c, rep) : test.prepare);
      const off = hub.on((m) => {
        if (m.type === "D") run.stream.push({ dev: m.dev, t: m.t, values: m.values });
        if (m.type === "E") run.events.push({ dev: m.dev, t: m.t, name: m.name, value: m.value });
      });
      const rec = Object.entries(test.record ?? {});
      try {
        for (const [dev, r] of rec) await hub.send(dev, "T", r.every ?? 100, r.channels.join(","));
        for (const step of test.steps(c, rep)) await runStep(step, { hub, ui, signal, run });
      } finally {
        off();
        if (signal.aborted) await hub.stopAll();
        else for (const [dev] of rec) await hub.send(dev, "T", 0, "").catch(() => {});
      }
      if (test.manual?.length) run.manual = await ui.ask(`${test.id} 조합 ${i + 1} · ${rep}회 결과 입력`, test.manual);
      if (test.truth) {
        const t = await ui.ask(`${test.id} 정답 입력`, [{ key: "truth", label: test.truth.label, text: true }]);
        run.truth = String(t.truth ?? "").trim().split(test.truth.sep ?? /[\s,]+/).filter(Boolean);
      }
      for (const [k, f] of Object.entries(test.metrics ?? {})) { try { run.metrics[k] = f(run); } catch { run.metrics[k] = NaN; } }
      if (test.success) run.ok = !!test.success(run);
      runs.push(run);
      ui.onRun(run);
    }
  }
  return { runs, verdict: test.pass ? test.pass(runs) : null };
}

// 반복 판정형 기본 합격 기준: 성공 k번 이상
export const atLeast = (k) => (runs) => {
  const n = runs.filter((r) => r.ok).length;
  return { pass: n >= k, text: `${runs.length}번 중 ${n}번 성공 (기준 ${k}번 이상)` };
};
