// 시험에서 공통으로 쓰는 계산. 입력 series는 [{t(ms), v}] (값이 -1이면 읽기 실패라 뺌)
export const valid = (series) => series.filter((p) => p.v !== -1 && Number.isFinite(p.v));
export const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);
export const std = (xs) => { const m = mean(xs); return xs.length > 1 ? Math.sqrt(mean(xs.map((x) => (x - m) ** 2))) : NaN; };
export const values = (series) => valid(series).map((p) => p.v);
export const first = (series) => valid(series)[0]?.v ?? NaN;
export const last = (series) => valid(series).at(-1)?.v ?? NaN;
export const seenRatio = (series) => (series.length ? valid(series).length / series.length : NaN);

// 최소제곱 기울기 (값/초). 초음파로 벽에 다가가면 -속도(cm/s)
export function slope(series) {
  const p = valid(series);
  if (p.length < 2) return NaN;
  const ts = p.map((q) => q.t / 1000), mt = mean(ts), mv = mean(p.map((q) => q.v));
  const den = ts.reduce((a, t) => a + (t - mt) ** 2, 0);
  return den ? p.reduce((a, q, i) => a + (ts[i] - mt) * (q.v - mv), 0) / den : NaN;
}

// 값이 낮게 떨어지는 구간(아래 25%)이 반복되는 주기(초). 제자리 회전 중 벽까지 초음파 거리 -> 한 바퀴 시간
export function periodOfMin(series, frac = 0.25) {
  const p = valid(series);
  if (p.length < 5) return NaN;
  const vs = p.map((q) => q.v), lo = Math.min(...vs), hi = Math.max(...vs);
  if (hi - lo < 1) return NaN; // 거의 안 변하면 회전 안 함
  const cut = lo + frac * (hi - lo), centers = [];
  let run = [];
  for (const q of [...p, { t: Infinity, v: Infinity }]) {
    if (q.v <= cut) run.push(q.t);
    else if (run.length) { centers.push(mean(run)); run = []; }
  }
  return centers.length < 2 ? NaN : (centers.at(-1) - centers[0]) / (centers.length - 1) / 1000;
}

// 인식 정확도: 정답 순서(truth, 예 ["Y","G","Y"])와 인식된 순서(seen)의 최장 공통 부분열로 맞힌 개수를 셈
export function matchRate(seen, truth) {
  const a = seen.filter((x, i) => x !== seen[i - 1]), b = truth; // 연달아 같은 값은 한 번으로
  const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++)
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
  const hit = dp[a.length][b.length];
  return { hit, total: b.length, recall: b.length ? hit / b.length : NaN, falseRate: a.length ? (a.length - hit) / a.length : 0 };
}

// 지연: a 시각 다음에 처음 오는 b 시각까지 (ms)
export const latency = (ta, tb) => (Number.isFinite(ta) && Number.isFinite(tb) ? tb - ta : NaN);

export const round = (x, d = 2) => (Number.isFinite(x) ? Math.round(x * 10 ** d) / 10 ** d : "");
