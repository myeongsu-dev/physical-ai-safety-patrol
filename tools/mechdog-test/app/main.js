// 화면. 계산·통신·실행은 core/, 시험 내용은 tests/에 있고 여기는 그리기와 입력만.
import * as ble from "./devices/ble.js";
import { createHub } from "./core/hub.js";
import { runTest, KINDS } from "./core/runner.js";
import { toCsv, summaryRows, rawRows, download } from "./core/results.js";
import { round } from "./core/metrics.js";
import { parseInfo } from "./core/protocol.js";
import TESTS from "./tests/index.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const hub = createHub();
const runs = [];
let sel = null, cases = [], repeat = 1, ctrl = null, verdict = null, liveAt = 0;

// ---------- 통신 기록 (보낸 명령도 남기려고 send를 감쌈) ----------
const logLines = [];
function log(line) {
  logLines.push(`${new Date().toTimeString().slice(0, 8)} ${line}`);
  if (logLines.length > 200) logLines.shift();
  $("log").textContent = logLines.join("\n");
  $("log").scrollTop = 1e9;
}
const rawSend = hub.send;
hub.send = (id, cmd, ...args) => { log(`→ ${id} ${[cmd, ...args].join("|")}`); return rawSend(id, cmd, ...args); };

// ---------- 시험 조건 (이 브라우저에만 기억) ----------
const META = ["floor", "load", "note"];
for (const k of META) {
  try { $(`m-${k}`).value = localStorage.getItem(`mdt-${k}`) ?? ""; } catch {}
  $(`m-${k}`).oninput = (e) => { try { localStorage.setItem(`mdt-${k}`, e.target.value); } catch {} };
}
const meta = () => ({ robot: [...hub.devices.values()].map((d) => d.conn.name).join("+"), ...Object.fromEntries(META.map((k) => [k, $(`m-${k}`).value])) });

// ---------- 장치 ----------
$("connect").onclick = async () => {
  if (!ble.available()) return alert("이 브라우저는 웹 블루투스를 지원하지 않습니다. PC 크롬·엣지 또는 안드로이드 크롬으로 열어 주세요.");
  const id = hub.devices.has("dog") ? `dog${hub.devices.size + 1}` : "dog";
  try { await hub.add(id, ble); } catch (e) { if (e.name !== "NotFoundError") alert(`연결 실패: ${e.message}`); }
};
hub.on((m) => {
  if (m.type === "open" || m.type === "closed") { log(`${m.dev} ${m.type === "open" ? "연결됨" : "연결 끊김"}`); renderAll(); }
  if (m.type === "reply") log(`← ${m.dev} ${m.parts.join("|")}`);
  if (m.type === "E") log(`← ${m.dev} 이벤트 ${m.name}=${m.value}`);
  if (m.type === "D") chartPush(m);
  if (m.type !== "D" || performance.now() - liveAt > 250) { liveAt = performance.now(); renderLive(); } // 스트림은 0.25초마다만 다시 그림
});
function renderDevs() {
  $("devs").innerHTML = [...hub.devices.values()].map((d) => `<span class="chip"><b>${esc(d.id)}</b>${esc(d.conn.name)} · ${esc(d.info.version || "버전 없음")}${d.live.bat ? ` · ${(d.live.bat / 1000).toFixed(2)}V` : ""}</span>`).join("")
    || `<span class="muted small">연결된 장치 없음</span>`;
}
function renderLive() {
  renderDevs();
  $("live").innerHTML = [...hub.devices.values()].flatMap((d) => Object.entries(d.live).map(([k, v]) => `<span class="chip">${esc(d.id)}.${esc(k)} <b>${esc(v)}</b></span>`)).join("") || `<span class="muted small">값 없음</span>`;
}

// ---------- 시험 목록 ----------
const blocked = (t) => (t.planned ? `필요 장치: ${Object.keys(t.needs).join(", ")}` : hub.missing(t.needs));
function renderTests() {
  let html = "", grp = "";
  for (const t of TESTS) {
    if (t.group !== grp) html += `<div class="grp">${esc((grp = t.group))}</div>`;
    const why = blocked(t);
    html += `<button data-test="${t.id}" class="${sel === t ? "sel" : ""}"${t.planned ? ' style="opacity:.55"' : ""}><b>${t.id}</b> ${esc(t.title)}<span class="kind">${KINDS[t.kind]}</span>${why ? `<span class="why">${esc(why)}</span>` : ""}</button>`;
  }
  $("tests").innerHTML = html;
}
$("tests").onclick = (e) => {
  const b = e.target.closest("[data-test]");
  if (!b || ctrl) return;
  sel = TESTS.find((t) => t.id === b.dataset.test);
  cases = sel.cases?.map((c) => ({ ...c })) ?? [];
  repeat = sel.repeat ?? 1;
  verdict = null;
  renderAll();
};

// ---------- 선택한 시험 ----------
function renderDetail() {
  if (!sel) return;
  const keys = sel.params?.map((p) => p.key) ?? Object.keys(cases[0] ?? {}).filter((k) => k !== "repeat");
  const labels = Object.fromEntries((sel.params ?? []).map((p) => [p.key, p.label]));
  const why = blocked(sel);
  $("detail").innerHTML = `
    <h2>${sel.id} ${esc(sel.title)} <span class="kind">${KINDS[sel.kind]}</span></h2>
    <div class="refs">${(sel.refs ?? []).map((r) => `<span>${esc(r)}</span>`).join("")}</div>
    <div class="setup small"><b>준비</b> ${esc(sel.setup)}</div>
    ${sel.planned ? `<p class="muted">장치가 생기면 tests/ 폴더에 이 시험 파일을 만들어 엽니다.</p>` : `
    <div class="tw"><table><tr><th>#</th>${keys.map((k) => `<th>${esc(labels[k] ?? k)}</th>`).join("")}<th></th></tr>
      ${cases.map((c, i) => `<tr><td>${i + 1}</td>${keys.map((k) => `<td><input data-case="${i}" data-key="${k}" value="${esc(c[k])}"></td>`).join("")}<td><button data-del="${i}" title="이 조합 빼기">×</button></td></tr>`).join("")}
    </table></div>
    <div class="row" style="margin-top:8px">
      <button id="add-case">조합 추가</button>
      <label>반복 <input id="repeat" type="number" min="1" value="${repeat}" style="width:70px"></label>
      <button id="run" class="primary" ${why || ctrl ? "disabled" : ""}>${ctrl ? "실행 중…" : "실행"}</button>
      ${why ? `<span class="small" style="color:var(--warn)">${esc(why)}</span>` : ""}
    </div>
    <div id="status" class="small muted"></div>
    <div id="verdict" class="${verdict ? (verdict.pass ? "pass" : "fail") : ""}">${verdict ? `${verdict.pass ? "합격" : "불합격"} · ${esc(verdict.text)}` : ""}</div>`}`;
}
$("detail").oninput = (e) => {
  const i = e.target.dataset.case;
  if (i != null) cases[i][e.target.dataset.key] = e.target.value;
  if (e.target.id === "repeat") repeat = +e.target.value || 1;
};
$("detail").onclick = async (e) => {
  if (e.target.dataset.del != null) { cases.splice(+e.target.dataset.del, 1); return renderDetail(); }
  if (e.target.id === "add-case") { cases.push({ ...(cases.at(-1) ?? {}) }); return renderDetail(); }
  if (e.target.id === "run") await start();
};

async function start() {
  ctrl = new AbortController();
  chartReset(Object.values(sel.record ?? {}).flatMap((r) => r.channels));
  renderAll();
  try {
    // 반복 칸은 조합에 따로 repeat이 없을 때만 적용
    const res = await runTest(sel, { cases: cases.map((c) => ({ ...c, repeat: c.repeat ?? repeat })), meta: meta(), hub, ui, signal: ctrl.signal });
    verdict = res.verdict;
    ui.status(`끝 · ${res.runs.length}회`);
  } catch (e) {
    ui.status(`멈춤: ${e.message}`);
    await hub.stopAll();
  } finally {
    ctrl = null;
    renderAll();
  }
}

// ---------- 실행기에 넘기는 화면 기능 ----------
const ui = {
  status: (s) => { const el = $("status"); if (el) el.textContent = s; },
  confirm: (text) => dialog(text, ""),
  ask: (title, fields) => dialog(title, fields.map((f) => `<label class="small">${esc(f.label)}<input name="${f.key}" ${f.text ? "" : 'inputmode="decimal"'}></label>`).join(""))
    .then((form) => Object.fromEntries(fields.map((f) => [f.key, form.elements[f.key].value.trim()]))),
  onRun: (run) => { runs.push(run); renderResults(); },
};
function dialog(title, body) {
  return new Promise((ok, no) => {
    const d = $("dlg"), form = d.querySelector("form");
    $("dlg-title").textContent = title;
    $("dlg-body").innerHTML = body;
    d.onclose = () => (d.returnValue === "ok" ? ok(form) : (ctrl?.abort(), no(new Error("중단"))));
    d.returnValue = "";
    d.showModal();
    form.querySelector("input")?.focus();
  });
}

// ---------- 결과 ----------
function renderResults() {
  $("count").textContent = runs.length ? `${runs.length}회` : "";
  if (!runs.length) return ($("results").innerHTML = `<tr><td class="muted">아직 없음</td></tr>`);
  const fmt = (o) => Object.entries(o).map(([k, v]) => `${esc(k)} <b>${esc(typeof v === "number" ? round(v) : v)}</b>`).join(" · ");
  $("results").innerHTML = `<tr><th>시각</th><th>시험</th><th>조합</th><th>회</th><th>자동 계산</th><th>손 측정</th><th>판정</th></tr>`
    + runs.slice().reverse().map((r) => `<tr><td>${r.at.slice(11, 19)}</td><td>${r.test}</td><td class="small">${fmt(r.case)}</td><td>${r.rep}</td>
      <td class="small">${fmt(r.metrics)}${r.errors.length ? ` <span class="ng">${esc(r.errors.join(", "))}</span>` : ""}</td><td class="small">${fmt(r.manual)}${r.truth ? ` · 정답 ${esc(r.truth.join(" "))}` : ""}</td>
      <td>${r.ok == null ? "" : r.ok ? `<span class="ok">성공</span>` : `<span class="ng">실패</span>`}</td></tr>`).join("");
}
const fileName = (kind) => `${new Date().toISOString().slice(0, 10)}_${[...new Set(runs.map((r) => r.test))].join("-")}_${(runs[0]?.meta.robot || "robot").replace(/\W+/g, "")}_${kind}.csv`;
$("csv-sum").onclick = () => runs.length && download(fileName("summary"), toCsv(summaryRows(runs)));
$("csv-raw").onclick = () => runs.length && download(fileName("raw"), toCsv(rawRows(runs)));
$("clear").onclick = () => runs.length && confirm("결과를 지울까요? 저장하지 않은 결과는 사라집니다.") && (runs.length = 0, renderResults());

// ---------- 실시간 그래프 (지금 실행 중인 시험이 기록하는 채널) ----------
const COLORS = ["#2563eb", "#ea580c", "#16a34a", "#9333ea", "#0891b2", "#be185d"];
let chart = { chans: [], pts: {} };
function chartReset(chans) {
  chart = { chans: [...new Set(chans)].filter((c) => c !== "color"), pts: {} };
  $("legend").innerHTML = chart.chans.map((c, i) => `<span style="color:${COLORS[i % 6]}">● ${esc(c)}</span>`).join(" &nbsp; ");
  drawChart();
}
function chartPush(m) {
  for (const [k, v] of Object.entries(m.values)) if (chart.chans.includes(k) && v !== -1) (chart.pts[k] ??= []).push([m.t, v]);
  drawChart();
}
function drawChart() {
  const c = $("chart"), g = c.getContext("2d"), W = c.width, H = c.height;
  g.clearRect(0, 0, W, H);
  chart.chans.forEach((ch, i) => {
    const p = (chart.pts[ch] ?? []).slice(-400);
    if (p.length < 2) return;
    const t0 = p[0][0], t1 = p.at(-1)[0] || 1, vs = p.map((q) => q[1]), lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
    g.strokeStyle = COLORS[i % 6]; g.lineWidth = 2; g.beginPath();
    p.forEach(([t, v], j) => g[j ? "lineTo" : "moveTo"](((t - t0) / (t1 - t0 || 1)) * (W - 10) + 5, H - 8 - ((v - lo) / span) * (H - 16)));
    g.stroke();
    g.fillStyle = COLORS[i % 6]; g.font = "12px system-ui"; g.fillText(`${ch} ${round(lo, 1)}~${round(hi, 1)}`, 8, 16 + i * 15);
  });
}

// ---------- 정지 · 자유 조종 ----------
const stop = () => { ctrl?.abort(); hub.stopAll(); };
$("stop").onclick = stop;
addEventListener("keydown", (e) => { if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && !$("dlg").open) { e.preventDefault(); stop(); } });
document.querySelector("details").onclick = async (e) => {
  const k = e.target.dataset?.free;
  if (!k) return;
  const id = [...hub.devices.keys()][0];
  if (!id) return alert("먼저 로봇을 연결하세요.");
  const send = k === "raw" ? $("f-raw").value.split("|") : k === "M" ? ["M", $("f-stride").value, $("f-angle").value, $("f-ms").value] : [k];
  try {
    const reply = await hub.send(id, ...send);
    if (k === "V") { hub.devices.get(id).info = parseInfo(reply); renderAll(); } // 로봇 프로그램을 바꾼 뒤 시험 목록 다시 열기
  } catch (err) { log(`! ${err.message}`); }
};

function renderAll() { renderDevs(); renderLive(); renderTests(); renderDetail(); renderResults(); }
renderAll();
