/* Flow Console client. Hash routes: #/request, #/runs, #/runs/<id>, #/new, #/knowledge, #/models, #/project, #/how */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 0) => n == null ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const dur = (s) => s == null ? "—" : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
const when = (iso) => { if (!iso) return "—"; const d = new Date(iso); return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); };
const clock = (iso) => iso ? new Date(iso).toLocaleTimeString("en-GB", { hour12: false }) : "";
const store = { get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
function toast(msg) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.append(t);
  setTimeout(() => t.remove(), 4200);
}

// Verification scenarios come from the active project (overview.scenarios).
function scenarioLabel(id) {
  const sc = (overview?.scenarios || []).find((x) => x.id === id);
  return sc ? sc.label : (id === "clean" ? "Unmodified design" : id);
}

// Provider/model choice shared by the request and verification-run forms.
function modelField(name, allowNone) {
  const L = overview.llm, ch = L.choices || [], d = L.default;
  const opts = ch.map((c) => `<option value="${esc(c.provider)}|${esc(c.model)}" ${d && c.provider === d.provider && c.model === d.model ? "selected" : ""}>${esc(c.provider)} · ${esc(c.model)}</option>`).join("");
  return `<div class="field"><span class="legend">Model for the agents</span><div class="options">
    <label class="opt ${ch.length ? "" : "disabled"}"><input type="radio" name="${name}" value="api" ${ch.length ? "checked" : "disabled"}><b>API model</b>
      <span>${ch.length ? "Each provider and model has its own quota: if one is used up, pick another." : esc(L.error || "No provider with an API key is configured (config/llm.toml and secrets.env).")}</span>
      ${ch.length ? `<select id="${name}-model" class="mono" style="margin-top:6px">${opts}</select>` : ""}</label>
    ${allowNone ? `<label class="opt"><input type="radio" name="${name}" value="none" ${ch.length ? "" : "checked"}><b>No model</b><span>Rule-based diagnosis only. Unclear failures go straight to a person.</span></label>` : ""}</div></div>`;
}
function modelChoice(name) {
  const sel = $(`#${name}-model`);
  if (!sel) return {};
  const [provider, model] = sel.value.split("|");
  return { provider, model };
}
const STAGES = [
  ["spec", "Specification"], ["rtl", "RTL"], ["verify_rtl", "Verify · RTL"],
  ["synth", "Synthesis"], ["verify_gate", "Verify · gate"], ["pnr", "Layout · signoff"],
];

let overview = null;
let timer = null;
function stopPolling() { if (timer) clearTimeout(timer); timer = null; }

/* ------------------------------------------------------------------ shell */
async function refreshOverview() {
  overview = await api("/api/overview");
  const d = overview.llm.default;
  const llm = d ? `${d.provider} · ${d.model}` : "none configured";
  $(".brand span").textContent = `${overview.project || ""} · sky130`;
  $("#side-foot").innerHTML = `
    <span class="live ${overview.active ? "busy" : ""}"><i></i>${overview.active ? `Running <b>${esc(overview.active)}</b>` : "Idle"}</span>
    <span>Model <b>${esc(llm)}</b></span>
    <span>Verilator <b>${esc(overview.toolchain.verilator.split(" ")[0])}</b> · Yosys <b>${esc(overview.toolchain.yosys)}</b></span>
    <span>sky130A <b>${esc(overview.toolchain.pdk)}</b></span>`;
  return overview;
}

function setNav(key) {
  document.querySelectorAll(".nav a").forEach((a) => { if (a.dataset.nav === key) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
}

async function route() {
  stopPolling();
  const [, page, id] = (location.hash || "#/runs").split("/");
  const main = $("#main");
  try {
    await refreshOverview();
    if (page === "runs" && id) { setNav("runs"); await runPage(main, decodeURIComponent(id)); }
    else if (page === "new") { setNav("new"); newRunPage(main); }
    else if (page === "request") { setNav("request"); requestPage(main); }
    else if (page === "project") { setNav("project"); await projectPage(main); }
    else if (page === "models") { setNav("models"); await modelsPage(main); }
    else if (page === "knowledge") { setNav("knowledge"); await knowledgePage(main); }
    else if (page === "how") { setNav("how"); howPage(main); }
    else { setNav("runs"); runsPage(main); }
  } catch (e) {
    main.innerHTML = `<div class="notice err">Could not reach the Flow Console server: ${esc(e.message)}. Is <code>scripts/start_app.sh</code> running?</div>`;
  }
  main.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ runs list */
function runStatus(r) {
  if (r.running) return `<span class="pill run">Running</span>`;
  if (r.stage === "done") return `<span class="pill ok">Signed off</span>`;
  if (r.stage === "failed") return `<span class="pill warn">Needs a person</span>`;
  if (r.stage === "quota_stopped") return `<span class="pill idle" title="The model's daily quota ran out; no attempt was spent. Run it again later.">Model quota — rerun later</span>`;
  if (r.stage === "error") return `<span class="pill bad" title="An internal error ended the run; the Console tab has the details.">Internal error</span>`;
  if (r.stage === "stopped") return `<span class="pill idle">Stopped on request</span>`;
  return `<span class="pill idle">Stopped</span>`;
}
function modelName(llm) { return (llm || "none").replace("openai-compat/", ""); }

function runsPage(main) {
  const runs = overview.runs;
  const done = runs.filter((r) => r.stage === "done").length;
  main.innerHTML = `
  <div class="page-head"><div><h1>Runs</h1>
    <p>Each run takes the project's design through specification, RTL, verification, synthesis, gate-level verification and layout.
    Open a run to follow it stage by stage.</p></div>
    <div class="row"><a class="btn" href="#/new">Verification run</a><a class="btn primary" href="#/request">New request</a></div></div>
  <div class="row muted">${runs.length} runs · ${done} signed off · ${runs.filter((r) => r.stage === "failed").length} waiting for a person</div>
  <section class="panel tablewrap">${runs.length ? `<table>
    <thead><tr><th>Run</th><th>Scenario</th><th>Model</th><th>Started</th><th>Duration</th><th>RTL tests</th><th>Setup slack</th><th>Status</th></tr></thead>
    <tbody>${runs.map((r) => `<tr class="link" data-id="${esc(r.run_id)}" tabindex="0">
      <td class="id"><span class="mono">${esc(r.run_id)}</span>${r.human_fix ? ` <span class="tag human">human fix</span>` : ""}</td>
      <td>${r.kind === "request" ? `<b>Change request</b><div class="muted" title="${esc(r.request)}" style="font-size:12.5px;min-width:28ch;max-width:44ch;white-space:normal">${esc(r.request.slice(0, 70))}${r.request.length > 70 ? "…" : ""}</div>` : esc(scenarioLabel(r.scenario))}${r.base_run ? `<div class="muted" style="font-size:12.5px">from ${esc(r.base_run)}</div>` : ""}</td>
      <td class="mono" style="font-size:12.5px">${esc(modelName(r.llm))}</td>
      <td class="num">${when(r.started)}</td><td class="num">${r.running ? "…" : dur(r.wall_seconds)}</td>
      <td class="num">${r.tests_total ? `${r.tests_passed}/${r.tests_total}` : "—"}</td>
      <td class="num">${r.setup_wns_ns == null ? "—" : `+${fmt(r.setup_wns_ns, 2)} ns`}</td>
      <td>${runStatus(r)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No runs yet. <a href="#/new">Start the first one.</a></div>`}</section>`;
  main.querySelectorAll("tr.link").forEach((tr) => {
    const go = () => location.hash = `#/runs/${encodeURIComponent(tr.dataset.id)}`;
    tr.addEventListener("click", go); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  if (overview.active) timer = setTimeout(route, 4000);
}

/* ------------------------------------------------------------------ run page */
const runState = { id: null, tab: "activity", consoleOffset: 0, consoleText: "", gateId: null, data: null };

function stageStates(run) {
  const ev = run.history.map((h) => h.event);
  const has = (e) => ev.includes(e);
  const cur = run.result;
  const failedAt = (() => {
    if (cur !== "failed") return null;
    for (const h of [...run.history].reverse()) {
      if (/verify_gate/.test(h.event) || h.stage === "verify_gate") return "verify_gate";
      if (/verify_rtl|lint|rtl_edit|attempt_cap|build_failed/.test(h.event)) return h.stage || "verify_rtl";
      if (/spec|test_change/.test(h.event)) return "spec";
      if (/synth/.test(h.event)) return "synth";
      if (/signoff|pnr/.test(h.event)) return "pnr";
    }
    return "verify_rtl";
  })();
  const nEdits = run.edits.filter((e) => e.applied).length;
  const humanFix = run.edits.some((e) => e.author === "human");
  const t = (lvl) => { const v = Object.values(run.tests).map((x) => x[lvl]).filter(Boolean); return v.length ? `${v.filter((x) => x === "pass").length}/${v.length} pass` : ""; };
  const st = {
    spec: { done: has("spec_approved") || has("spec_clarified"), d: run.spec.clarifications.length ? `clarified · rev ${run.spec.revision}` : "as delivered" },
    rtl: { done: has("rtl_ready") || nEdits > 0 || has("verify_rtl_passed"), d: humanFix ? "fixed by a person" : nEdits ? `${nEdits} agent edit${nEdits > 1 ? "s" : ""}` : run.inject ? `fault: ${run.inject}` : "upstream copy" },
    verify_rtl: { done: has("verify_rtl_passed"), d: run.verify_rtl.attempt_count ? `${t("verify_rtl")} · try ${run.verify_rtl.attempt_count}/${run.verify_rtl.max_attempts}` : "" },
    synth: { done: run.synth.cells != null && has("synth_done") || has("synth_cache_hit"), d: run.synth.cells ? `${fmt(run.synth.cells)} cells${run.synth.cache_hit ? " · cached" : ""}` : "" },
    verify_gate: { done: has("verify_gate_passed"), d: run.verify_gate.attempt_count ? `${t("verify_gate")}` : "" },
    pnr: { done: !!run.signoff.signoff_pass, d: run.signoff.setup_wns_ns != null ? `WNS +${fmt(run.signoff.setup_wns_ns, 2)} ns${run.signoff.cache_hit ? " · cached" : ""}` : "" },
  };
  return STAGES.map(([k, label], i) => {
    const s = st[k];
    const tried = (k === "verify_rtl" || k === "verify_gate") && run[k].attempt_count > 0;
    let pill = ["idle", "Not started"];
    if (run.running && cur === k) pill = ["run", "In progress"];
    else if (failedAt === k) pill = ["warn", "Needs a person"];
    else if (s.done) pill = ["ok", "Done"];
    else if (tried) pill = ["bad", "Failing — being fixed"];
    return { k, label, n: i + 1, pill, d: s.d, now: run.running && cur === k };
  });
}

const REQ_STAGES = [["spec", "Compare & spec"], ["test", "New test"], ["rtl", "RTL change"],
  ["verify_rtl", "Verify · RTL"], ["synth", "Synthesis"], ["verify_gate", "Verify · gate"], ["pnr", "Layout · signoff"]];

function requestStates(run) {
  const ev = run.history.map((h) => h.event), has = (x) => ev.includes(x), cur = run.result;
  const a = run.analysis || {}, t = run.test || {};
  const nEd = run.edits.filter((e) => e.applied && !e.reverted).length;
  const last = run.history[run.history.length - 1] || {};
  const failedAt = cur !== "failed" ? null : /request_rejected|analysis|no_model|no_evidence/.test(last.event) ? "spec"
    : /test_|already_satisfied/.test(last.event) ? "test" : /verify_gate/.test(last.event) ? "verify_gate"
    : /synth/.test(last.event) ? "synth" : /signoff/.test(last.event) ? "pnr" : "rtl";
  const st = {
    spec: { done: has("spec_agrees") || has("spec_clarified"), d: a.verdict ? `${a.verdict}${run.spec.clarifications.length ? " · spec updated" : ""}` : "" },
    test: { done: has("test_written"), d: t.name || (t.attempts ? `attempt ${t.attempts}` : "") },
    rtl: { done: nEd > 0 && has("verify_rtl_passed"), d: nEd ? `${nEd} change${nEd > 1 ? "s" : ""}` : run.verify_rtl.attempt_count ? `try ${run.verify_rtl.attempt_count}/3` : "" },
    verify_rtl: { done: has("verify_rtl_passed"), d: (() => { const v = Object.values(run.tests).map((x) => x.verify_rtl).filter(Boolean); return v.length ? `${v.filter((x) => x === "pass").length}/${v.length} pass` : ""; })() },
    synth: { done: has("synth_done") || has("synth_cache_hit"), d: run.synth.cells ? `${fmt(run.synth.cells)} cells${run.synth.cache_hit ? " · cached" : ""}` : "" },
    verify_gate: { done: has("verify_gate_passed"), d: (() => { const v = Object.values(run.tests).map((x) => x.verify_gate).filter(Boolean); return v.length ? `${v.filter((x) => x === "pass").length}/${v.length} pass` : ""; })() },
    pnr: { done: !!run.signoff.signoff_pass, d: run.signoff.setup_wns_ns != null ? `WNS +${fmt(run.signoff.setup_wns_ns, 2)} ns` : "" },
  };
  return REQ_STAGES.map(([k, label], i) => {
    let pill = ["idle", "Not started"];
    if (run.running && cur === k) pill = ["run", "In progress"];
    else if (failedAt === k) pill = ["warn", "Needs a person"];
    else if (st[k].done) pill = ["ok", "Done"];
    return { k, label, n: i + 1, pill, d: st[k].d, now: run.running && cur === k };
  });
}

function renderRequest(run) {
  const box = $("#r-req");
  if (run.kind !== "request") { box.innerHTML = ""; return; }
  const a = run.analysis || {};
  const vtag = { agrees: ["doc", "agrees with the specification"], extends: ["spec_ambiguity", "not covered by the specification"], conflicts: ["unknown", "conflicts with the specification"] }[a.verdict];
  box.innerHTML = `<section class="panel pad" style="gap:12px">
    <div class="eyebrow">Request</div><p style="margin:0;font-size:15.5px">${esc(run.request)}</p>
    ${a.verdict ? `<div class="row"><span class="tag ${vtag[0]}">${vtag[1]}</span><span class="muted mono" style="font-size:12px">${esc(a.method)}</span></div>
      ${a.spec_quote ? `<div class="quote">“${esc(a.spec_quote)}”</div>` : ""}
      <dl class="kv"><dt>Change needed</dt><dd>${esc(a.summary)}</dd><dt>Test must check</dt><dd>${esc(a.acceptance)}</dd>
      ${a.spec_addition && a.verdict !== "agrees" ? `<dt>Proposed spec text</dt><dd>${esc(a.spec_addition)}</dd>` : ""}
      ${a.note ? `<dt>Note</dt><dd>${esc(a.note)}</dd>` : ""}</dl>
      ${evidenceHtml(a.evidence, "Retrieved from the knowledge base")}` : `<p class="muted" style="margin:0">Searching the knowledge base…</p>`}</section>`;
}

function eventView(e) {
  const T = (what, dot = "", more = "") => ({ what, dot, more });
  switch (e.event) {
    case "model_fallback": return T(`Hosted model quota used up — continued on <span class="mono">${esc(e.to)}</span>`, "warn", `<p class="muted mono" style="margin:0;font-size:12.5px">${esc(e.reason)}</p>`);
    case "test_reviewed": return T(e.required ? `Test check reviewed — required by the specification; keep changing the RTL` : `Test check reviewed — the specification contradicts it`, e.required ? "info" : "warn", `<p class="muted mono" style="margin:0;font-size:12.5px">${esc(e.check)}</p><p style="margin:0">${esc(e.reason)}</p>`);
    case "test_revised": return T(`New test revised — it still fails on the unchanged RTL`, "info", `<p class="muted" style="margin:0">${esc(e.reason)}</p>`);
    case "request_received": return T(`Request received`, "info", `<p style="margin:0">${esc(e.request)}</p>`);
    case "analysed": return T(`Compared with the knowledge base — <span class="tag ${e.verdict === "agrees" ? "doc" : e.verdict === "conflicts" ? "unknown" : "spec_ambiguity"}">${esc(e.verdict)}</span>`, "info", `<p class="muted" style="margin:0">${esc(e.summary)}</p>`);
    case "spec_agrees": return T(`The specification already requires this`, "ok", `<div class="quote">“${esc(e.quote)}”</div>`);
    case "request_rejected": return T(`Request rejected — nothing changed`, "warn");
    case "test_written": return T(`New test <span class="mono">${esc(e.name)}</span> written — fails on the unchanged RTL, as it must`, "ok", `<p class="muted mono" style="margin:0;font-size:12.5px">${esc(e.fails_before)}</p>`);
    case "test_rejected": return T(`Test attempt ${e.attempt} rejected`, "", `<p class="muted" style="margin:0">${esc(e.reason)}</p>`);
    case "already_satisfied": return T(`Every test written passes on the current design — it may already meet the request`, "warn");
    case "no_model": case "no_evidence": case "analysis_failed": return T(e.event === "no_model" ? "No model available for the request pipeline" : e.event === "no_evidence" ? "The knowledge base returned nothing for this request" : "Analysis failed", "bad", e.error ? `<p class="muted" style="margin:0">${esc(e.error)}</p>` : "");
    case "knowledge": return e.available
      ? T(`Knowledge base ready — ${e.n_chunks} chunks, ${e.n_pattern} confirmed fix${e.n_pattern === 1 ? "" : "es"}`, "info")
      : T(`Knowledge base not running — agents use only the spec section each test names`, "warn");
    case "knowledge_reindexed": return T(`Knowledge base rebuilt with the new fix — ${e.n_pattern} confirmed fix pattern${e.n_pattern === 1 ? "" : "s"}`, "ok");
    case "base_run": return T(`Started from run <span class="mono">${esc(e.run)}</span> — its fixed RTL and its spec (revision ${e.spec_revision})`, "info");
    case "fault_injected": return T(`Fault injected into this run's copy of the RTL <span class="tag">${esc(e.kind)}</span>`, "warn");
    case "spec_approved": return T(`Specification accepted (revision ${e.revision})`, "ok");
    case "rtl_ready": return T(`RTL ready for verification`);
    case "verify_rtl_failed": case "verify_gate_failed": {
      const lvl = e.event.startsWith("verify_rtl") ? "RTL" : "Gate-level";
      const ds = (e.diagnoses || []).map((d) => `<div class="diag ${d.root_cause}"><div class="row" style="gap:6px 8px">
          <span class="mono">${esc(d.test_id)}</span><span class="tag ${d.root_cause}">${d.root_cause.replace("_", " ")}</span>→
          <span class="tag ${d.route_to === "human" ? "human" : ""}">${esc(d.route_to.replace("_", " "))}</span>
          <span class="muted mono" style="font-size:11.5px">${esc(d.method)}</span></div><p>${esc(d.rationale)}</p>${evidenceHtml(d.evidence, "Retrieved from the knowledge base")}</div>`).join("");
      return T(`${lvl} verification, attempt ${e.attempt}: ${e.failing.length} failing — sent to <b>${esc(e.route_to.replace("_", " "))}</b>`, "bad",
        ds + (e.note ? `<p class="muted" style="margin:0">${esc(e.note)}</p>` : ""));
    }
    case "spec_clarified": return T(`Specification clarified — revision ${e.revision}, reading (${e.chosen})`, "info", `<p class="muted" style="margin:0">${e.consequence === "rtl_must_change" ? "The RTL must change to match." : "The test must change; escalated."}</p>`);
    case "spec_agent_failed": case "spec_agent_unavailable": return T(`Spec agent could not resolve the question`, "warn", e.error ? `<p class="muted" style="margin:0">${esc(e.error)}</p>` : "");
    case "spec_decision_escalated": return T(`Decision passed to a person`, "warn");
    case "test_change_required": return T(`The test, not the RTL, is wrong under the clarified spec — escalated`, "warn");
    case "rtl_edit":
      if (e.applied) return T(`RTL agent's change applied${e.candidate ? ` (candidate ${e.candidate})` : ""}`, "metal");
      if (e.screen === "pass") return T(`RTL agent candidate ${e.candidate} passed screening (lint and target tests on a scratch copy)`, "ok");
      return T(`RTL agent candidate${e.candidate ? " " + e.candidate : ""} ${e.screen ? "failed screening" : "rejected"}`, "", `<p class="muted mono" style="margin:0;font-size:12.5px">${esc(e.screen || e.error || "")}</p>`);
    case "rtl_agent_failed": return T(`RTL agent returned no usable change`, "warn");
    case "rtl_edit_reverted": return T(`Change rolled back`, "warn", `<p class="muted" style="margin:0">${esc(e.reason)}</p>`);
    case "lint_failed": return T(`Lint failed`, "bad", `<p class="muted mono" style="margin:0;font-size:12.5px">${esc(e.error)}</p>`);
    case "verify_rtl_passed": return T(`RTL verification passed`, "ok");
    case "verify_gate_passed": return T(`Gate-level verification passed`, "ok");
    case "writeback": return T(`Confirmed fix recorded in the knowledge base`, "ok");
    case "synth_done": return T(`Synthesised to sky130 standard cells: ${fmt(e.cells)} cells, ${fmt(e.area_um2)} µm²`, "ok");
    case "synth_cache_hit": case "pnr_cache_hit": return T(`${e.event.startsWith("synth") ? "Synthesis" : "Layout"} reused from run <span class="mono">${esc(e.from)}</span> — identical inputs`, "ok");
    case "signoff": return T(e.pass ? "Signoff passed" : "Signoff failed", e.pass ? "ok" : "bad");
    case "attempt_cap_reached": return T(`Three attempts used — handed to a person`, "warn");
    case "build_failed": return T(`Testbench did not build`, "bad");
    case "human_fix": return T(`Fix supplied by a person after ${e.agent_attempts_used} agent attempt(s); verification budget reset`, "info", `<p class="muted mono" style="margin:0;font-size:12.5px">${esc((e.file || "").split("/").pop())}</p>`);
    case "approval_declined": return T(`Tool run declined — run stopped`, "warn");
    default: return null;
  }
}

function diffHtml(d) {
  return d.split("\n").map((l) => `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "a" : l.startsWith("-") && !l.startsWith("---") ? "d" : l.startsWith("@@") ? "h" : ""}">${esc(l)}</span>`).join("\n");
}
function consoleHtml(t) {
  return t.split("\n").map((l) => {
    const c = /STAGE:/.test(l) ? "c-stage" : /\[WAITING\]|\[APPROVAL\]|\[DECISION\]/.test(l) ? "c-wait"
      : /RESULT: flow completed|passed \(|Lint clean|\bPASS\b|applied/.test(l) ? "c-ok"
      : /FAIL|ERROR|Error|escalat|rejected|Rolled back/.test(l) ? "c-bad" : /^=+$/.test(l.trim()) ? "c-dim" : "";
    return `<span class="${c}">${esc(l)}</span>`;
  }).join("\n");
}

async function runPage(main, id) {
  if (runState.id !== id) Object.assign(runState, { id, tab: store.get("tab", "activity"), consoleOffset: 0, consoleText: "", gateId: null });
  main.innerHTML = `<div id="r-head"></div><div id="r-gate"></div><div id="r-req"></div><div id="r-pipe"></div><div id="r-tabs"></div><div id="r-body"></div>`;
  await tickRun(main, true);
}

async function tickRun(main, first = false) {
  const id = runState.id;
  let run;
  try { run = await api(`/api/runs/${encodeURIComponent(id)}`); }
  catch (e) { main.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  if (location.hash !== `#/runs/${encodeURIComponent(id)}`) return;
  runState.data = run;
  const c = await api(`/api/runs/${encodeURIComponent(id)}/console?offset=${runState.consoleOffset}`);
  runState.consoleText += c.text; runState.consoleOffset = c.offset;

  renderRunHead(run);
  renderGate(run);
  renderRequest(run);
  renderPipe(run);
  renderTabs(run);
  if (first || run.running || runState.tab === "console") renderBody(run);
  if (run.running) timer = setTimeout(() => tickRun(main), 1500);
  else if (!first && overview.active === id) { await refreshOverview(); }
}

function renderRunHead(run) {
  const s = run.summary;
  const status = run.running ? `<span class="pill run">Running</span>` : run.result === "done" ? `<span class="pill ok">Signed off</span>`
    : run.result === "failed" ? `<span class="pill warn">Needs a person</span>`
    : run.result === "quota_stopped" ? `<span class="pill idle">Model quota — rerun later</span>`
    : run.result === "error" ? `<span class="pill bad">Internal error</span>`
    : run.result === "stopped" ? `<span class="pill idle">Stopped on request</span>` : `<span class="pill idle">Stopped</span>`;
  const actions = run.running ? `<button class="btn danger small" id="stop">Stop run</button>`
    : run.result === "failed" ? `<button class="btn primary small" id="fixbtn">Supply a fix and continue</button>` : "";
  $("#r-head").innerHTML = `<div class="page-head"><div>
      <div class="eyebrow"><a href="#/runs" style="text-decoration:none">Runs</a> / ${run.kind === "request" ? "Change request" : esc(scenarioLabel(s.scenario))}</div>
      <h1 class="mono" style="font-family:var(--f-mono);font-size:22px">${esc(run.run_id)}</h1>
      <div class="row muted" style="margin-top:6px;font-size:13px">${status}
        <span>Model <span class="mono">${esc(modelName(run.llm))}</span></span>
        <span>Started ${when(run.started)}</span>${run.wall_seconds ? `<span>${dur(run.wall_seconds)}</span>` : ""}
        ${s.base_run ? `<span>From <a href="#/runs/${encodeURIComponent(s.base_run)}" class="mono">${esc(s.base_run)}</a></span>` : ""}</div></div>
    <div class="row">${actions}</div></div>`;
  $("#stop")?.addEventListener("click", async () => {
    try { await api(`/api/runs/${encodeURIComponent(run.run_id)}/stop`, {}); toast("Stopping the run"); } catch (e) { toast(e.message); }
  });
  $("#fixbtn")?.addEventListener("click", () => { runState.tab = "fix"; store.set("tab", "changes"); renderTabs(run); renderBody(run); });
}

function renderGate(run) {
  const g = run.gate, box = $("#r-gate");
  if (!g) { box.innerHTML = ""; runState.gateId = null; return; }
  if (runState.gateId === g.id) return;          // keep the card stable while waiting
  runState.gateId = g.id;
  const who = store.get("approver", "");
  const nameField = `<label class="row muted" style="font-size:13px">Your name
      <input type="text" id="g-by" value="${esc(who)}" placeholder="for the run record" style="width:200px;padding:6px 9px"></label>`;
  if (g.kind === "approve") {
    box.innerHTML = `<div class="gate" role="region" aria-label="Approval needed"><div class="head">
        <div><div class="eyebrow">Approval needed · step ${g.id}</div><h2 style="margin-top:2px">Run this tool?</h2></div>${nameField}</div>
      <div class="body"><div class="cmd">${esc(g.action)}</div>
        <p class="muted" style="margin:0">Every Verilator, Yosys and OpenROAD run waits for a person. Declining stops the run.</p>
        <div class="actions"><button class="btn primary" data-a="1">Approve and run</button><button class="btn" data-a="0">Decline</button></div></div></div>`;
    box.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => answer(run, g, { approve: b.dataset.a === "1" })));
  } else {
    const ctx = g.context || {}, cons = ctx.consequence || {};
    const opts = Object.entries(g.options).map(([k, v]) => {
      const label = v.replace(/^\[(.*?)\]\s*/, ""), who = (v.match(/^\[(.*?)\]/) || [])[1] || "";
      return `<button class="choice" data-c="${k}"><div class="k"><b>Reading (${k})</b>${k === g.recommended ? `<span class="pill info">Recommended</span>` : ""}</div>
        <span class="eyebrow">${esc(who)}</span><span>${esc(label)}</span>
        <span class="muted" style="font-size:12.5px">${cons[k] === "rtl_must_change" ? "Choosing this changes the RTL to match." : "Choosing this means the test is wrong; it goes to a person."}</span></button>`;
    }).join("");
    box.innerHTML = `<div class="gate" role="region" aria-label="Decision needed"><div class="head">
        <div><div class="eyebrow">Decision needed · ${esc(ctx.test || "")}</div><h2 style="margin-top:2px">The specification can be read two ways</h2></div>${nameField}</div>
      <div class="body">${ctx.ambiguous ? `<div class="quote">“${esc(ctx.ambiguous)}”</div>` : ""}
        ${ctx.why ? `<p style="margin:0"><b>Spec agent:</b> ${esc(ctx.why)}</p>` : ""}
        <div class="choices">${opts}</div>
        <div class="actions"><button class="btn" data-c="">Neither — escalate to a person</button></div></div></div>`;
    box.querySelectorAll("[data-c]").forEach((b) => b.addEventListener("click", () => answer(run, g, { choice: b.dataset.c || null })));
  }
}

async function answer(run, g, payload) {
  const by = ($("#g-by")?.value || "").trim(); if (by) store.set("approver", by);
  $("#r-gate").querySelectorAll("button").forEach((b) => b.disabled = true);
  try { await api(`/api/runs/${encodeURIComponent(run.run_id)}/gate`, { id: g.id, by, ...payload }); $("#r-gate").innerHTML = ""; }
  catch (e) { toast(e.message); $("#r-gate").querySelectorAll("button").forEach((b) => b.disabled = false); }
}

function renderPipe(run) {
  const states = run.kind === "request" ? requestStates(run) : stageStates(run);
  $("#r-pipe").innerHTML = `<section><div class="pipeline ${run.kind === "request" ? "seven" : ""}">${states.map((s) => `<div class="stage ${s.now ? "now" : ""}">
      <div class="n">${String(s.n).padStart(2, "0")}</div><div class="t">${s.label}</div>
      <span class="pill ${s.pill[0]}">${s.pill[1]}</span><div class="d">${esc(s.d)}</div></div>`).join("")}</div></section>`;
}

function renderTabs(run) {
  const failing = Object.values(run.tests).filter((t) => t.verify_rtl === "fail" || t.verify_gate === "fail").length;
  const tabs = [["activity", "Activity", run.history.length], ["console", "Console", ""], ["tests", "Tests", failing ? `${failing} failing` : ""],
    ["changes", "Changes", run.edits.filter((e) => e.applied).length || ""], ["signoff", "Signoff & layout", ""]];
  if (run.kind === "request") tabs.splice(2, 0, ["newtest", "New test", run.test && run.test.name ? "" : ""]);
  if (run.result === "failed" && !run.running) tabs.push(["fix", "Supply a fix", ""]);
  if (!tabs.some(([k]) => k === runState.tab)) runState.tab = "activity";
  $("#r-tabs").innerHTML = `<div class="tabs" role="tablist">${tabs.map(([k, l, n]) => `<button role="tab" aria-selected="${runState.tab === k}" data-t="${k}">${l}${n !== "" ? `<span class="count">${n}</span>` : ""}</button>`).join("")}</div>`;
  $("#r-tabs").querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => {
    runState.tab = b.dataset.t; if (b.dataset.t !== "fix") store.set("tab", b.dataset.t); renderTabs(run); renderBody(run);
  }));
}

function renderBody(run) {
  const body = $("#r-body"), tab = runState.tab;
  if (tab === "activity") {
    const items = run.history.map((e) => [e, eventView(e)]).filter(([, v]) => v);
    body.innerHTML = `<section class="panel">${items.length ? `<ol class="events">${items.map(([e, v]) => `<li><time>${clock(e.at)}</time><span class="dot ${v.dot}"></span>
      <div><div class="what">${v.what}</div>${v.more ? `<div class="more">${v.more}</div>` : ""}</div></li>`).join("")}</ol>` : `<div class="empty">Starting…</div>`}</section>`;
  } else if (tab === "console") {
    const atBottom = (() => { const el = $(".console"); return !el || el.scrollTop + el.clientHeight >= el.scrollHeight - 30; })();
    body.innerHTML = `<pre class="console" aria-live="polite">${consoleHtml(runState.consoleText || "Waiting for output…")}</pre>`;
    const el = $(".console"); if (atBottom) el.scrollTop = el.scrollHeight;
  } else if (tab === "tests") {
    const ids = Object.keys(run.tests);
    body.innerHTML = `<section class="panel tablewrap">${ids.length ? `<table><thead><tr><th>Test</th><th>RTL</th><th>Gate level</th></tr></thead><tbody>${ids.map((id) => {
      const t = run.tests[id], c = (v) => v ? `<td class="${v === "pass" ? "pass" : "fail"}">${v === "pass" ? "PASS" : "FAIL"}</td>` : `<td class="muted">—</td>`;
      return `<tr><td class="mono">${esc(id)}</td>${c(t.verify_rtl)}${c(t.verify_gate)}</tr>`;
    }).join("")}</tbody></table>` : `<div class="empty">No tests have run yet.</div>`}</section>`;
  } else if (tab === "changes") {
    const clar = run.spec.clarifications.map((c) => `<div class="panel pad" style="display:grid;gap:8px">
        <div class="eyebrow">Specification revision ${c.revision} · from ${esc(c.test_id)}</div>
        <div class="quote">“${esc(c.ambiguous_text)}”</div>
        <dl class="kv"><dt>Reading (a)</dt><dd>${esc(c.reading_a)}</dd><dt>Reading (b)</dt><dd>${esc(c.reading_b)}</dd>
        <dt>Chosen</dt><dd>(${esc(c.chosen)}) — ${esc(c.consequence === "rtl_must_change" ? "the RTL changes" : "the test changes")}</dd>
        <dt>Added to spec</dt><dd>${esc(c.clarification)}</dd></dl>${evidenceHtml(c.evidence, "Knowledge the spec agent was given")}</div>`).join("");
    const edits = run.edits.filter((e) => e.applied).map((e) => `<div class="panel pad" style="display:grid;gap:8px">
        <div class="row"><span class="eyebrow">${e.author === "human" ? "Change by a person" : `RTL agent · candidate ${e.candidate || 1}`}</span>
        <span class="mono muted" style="font-size:12.5px">${esc(e.test_id)}</span>${e.reverted ? `<span class="pill warn">rolled back</span>` : e.confirmed ? `<span class="pill ok">confirmed</span>` : ""}</div>
        ${e.why ? `<p style="margin:0">${esc(e.why)}</p>` : ""}<pre class="diff">${diffHtml(e.diff || "")}</pre>${evidenceHtml(e.evidence, "Knowledge the agent was given")}</div>`).join("");
    const rejected = run.edits.filter((e) => !e.applied && (e.screen || e.error)).length;
    body.innerHTML = `<section style="gap:14px">${clar}${edits || `<div class="panel empty">No change was applied to the RTL in this run.</div>`}
      ${rejected ? `<p class="muted" style="margin:0">${rejected} further candidate change(s) were rejected before reaching the RTL — see Activity.</p>` : ""}
      ${run.final_diff ? `<details class="panel pad"><summary style="cursor:pointer;font-weight:500">Final RTL compared with upstream</summary><pre class="diff" style="margin-top:10px">${diffHtml(run.final_diff)}</pre></details>` : ""}</section>`;
  } else if (tab === "signoff") {
    const so = run.signoff;
    if (so.setup_wns_ns == null) { body.innerHTML = `<section class="panel empty">This run has not reached layout yet.</section>`; return; }
    const m = (cls, label, v, unit = "") => `<div class="metric ${cls}"><span class="eyebrow">${label}</span><span class="v">${v}${unit ? `<small>${unit}</small>` : ""}</span></div>`;
    body.innerHTML = `<section style="gap:16px"><div class="metrics">
      ${m(so.setup_wns_ns >= 0 ? "good" : "badv", `Setup slack @ ${fmt(1000 / so.clock_period_ns)} MHz`, `${so.setup_wns_ns >= 0 ? "+" : ""}${fmt(so.setup_wns_ns, 2)}`, "ns")}
      ${m(so.hold_wns_ns >= 0 ? "good" : "badv", "Hold slack", `${so.hold_wns_ns >= 0 ? "+" : ""}${fmt(so.hold_wns_ns, 2)}`, "ns")}
      ${m(so.route_drc_errors === 0 ? "good" : "badv", "Routing DRC", fmt(so.route_drc_errors), "errors")}
      ${m(so.lec_equivalent ? "good" : "badv", "Equivalence", so.lec_equivalent ? "Identical" : "Mismatch")}
      ${m("", "Standard cells", fmt(so.stdcells))}${m("", "Die area", fmt(so.die_area_um2), "µm²")}
      ${m("", "Power (typical)", fmt(so.power_mw, 2), "mW")}${m("", "Wire length", fmt(so.wirelength_um / 1000, 1), "mm")}</div>
      ${run.images.length ? `<div class="gallery">${run.images.map((i) => `<figure><img src="${i.src}" alt="${esc(i.label)}, layout on sky130" loading="lazy"><figcaption>${esc(i.label)}</figcaption></figure>`).join("")}</div>` : ""}</section>`;
    body.querySelectorAll(".gallery img").forEach((img) => img.addEventListener("click", () => {
      const lb = document.createElement("div"); lb.className = "lightbox"; lb.innerHTML = `<img src="${img.src}" alt="${esc(img.alt)}">`;
      lb.addEventListener("click", () => lb.remove()); document.body.append(lb);
    }));
  } else if (tab === "newtest") {
    const t = run.test || {};
    body.innerHTML = t.code ? `<section class="panel pad" style="gap:12px">
        <div class="row"><span class="eyebrow">Written by the test agent before the RTL was changed</span><span class="mono">${esc(t.name)}</span></div>
        ${t.why ? `<p style="margin:0">${esc(t.why)}</p>` : ""}
        <pre class="diff">${esc(t.code)}</pre>
        <dl class="kv"><dt>On the unchanged RTL</dt><dd><span class="fail">FAIL</span> — ${esc(t.initial_failure || "")}</dd>
        <dt>After the change</dt><dd>${run.tests[t.name] && run.tests[t.name].verify_rtl === "pass" ? `<span class="pass">PASS</span>` : "—"}</dd>
        <dt>Attempts</dt><dd>${esc(t.attempts)}</dd>${t.revised_reason ? `<dt>Revised</dt><dd>${esc(t.revised_reason)}</dd>` : ""}</dl>
        ${evidenceHtml(t.evidence, "Knowledge the test agent was given")}</section>`
      : `<section class="panel empty">${run.running ? "The test agent has not finished yet." : "No test was accepted in this run — see Activity."}</section>`;
  } else if (tab === "fix") {
    renderFix(run, body);
  }
}

async function renderFix(run, body) {
  if ($("#fix-form")) return;                      // do not wipe what the person is typing
  const pending = run.history.filter((h) => /_failed$/.test(h.event)).pop();
  body.innerHTML = `<section class="panel pad" style="gap:16px"><form id="fix-form" class="form">
      <div class="prose"><p>This run stopped and is waiting for a person. Paste a unified diff against this run's RTL
        (<a href="/api/runs/${encodeURIComponent(run.run_id)}/file?path=rtl/${encodeURIComponent(run.rtl_file || "")}" target="_blank" rel="noopener">view the current file</a>).
        The same pipeline then checks it: lint, all tests, synthesis, gate-level tests, layout and signoff. The agent's attempts stay in the record.</p>
        ${pending ? `<p class="muted">Last failure: ${esc((pending.failing || []).join(", "))}</p>` : ""}</div>
      <div class="field"><label for="fix-diff">Change (unified diff)</label>
        <textarea id="fix-diff" spellcheck="false" placeholder="--- a/design.sv&#10;+++ b/design.sv&#10;@@ -140,7 +140,7 @@&#10;..."></textarea></div>
      <div class="field"><span class="legend">Approvals</span><div class="options">
        <label class="opt"><input type="radio" name="fmode" value="manual" checked><b>Ask me before each tool run</b><span>You approve every step in this window.</span></label>
        <label class="opt"><input type="radio" name="fmode" value="auto"><b>Approve automatically</b><span>Each approval is still recorded.</span></label></div></div>
      <div id="fix-msg"></div><div><button class="btn primary" type="submit">Apply the fix and continue</button></div></form></section>`;
  $("#fix-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = $("#fix-form").querySelector("input[name=fmode]:checked").value;
    try {
      await api(`/api/runs/${encodeURIComponent(run.run_id)}/resume`, { diff: $("#fix-diff").value, mode });
      toast("Fix accepted — verification restarted"); runState.tab = "activity"; await refreshOverview(); tickRun($("#main"));
    } catch (err) { $("#fix-msg").innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
  });
}

const KIND = { doc: "spec", rtl: "RTL", pattern: "confirmed change", testbench: "testbench" };
function evidenceHtml(ev, title) {
  if (!ev || !ev.length) return "";
  return `<div class="evidence"><span class="eyebrow">${esc(title)}</span>${ev.map((h) => `<div class="e">
    <span class="tag ${h.kind}">${KIND[h.kind] || h.kind}</span><span>${esc(h.label)}</span>
    <span class="s">score ${fmt(h.score, 2)}${h.location ? " · " + esc(h.location) : ""}</span>
    ${h.keyword_score > 0 ? "" : `<span class="weak">no exact identifier match</span>`}</div>`).join("")}</div>`;
}

/* ------------------------------------------------------------------ knowledge */
async function knowledgePage(main) {
  const st = await api("/api/knowledge/status");
  const m = st.meta || {};
  main.innerHTML = `<div class="page-head"><div><h1>Knowledge base</h1>
      <p>What the agents read. The project's specification and RTL, split by section and by module block, plus every fix the
      full test suite has confirmed. Search combines meaning (vector) and exact identifiers (keyword).</p></div>
      <button class="btn" id="kb-rebuild" ${st.available ? "" : "disabled"}>Rebuild index</button></div>
    ${st.available ? "" : `<div class="notice">The knowledge base service is not running. It starts with <code>scripts/start_app.sh</code>; runs continue without it, using the spec section each test names.</div>`}
    <section><div class="stats">
      <div class="metric"><span class="eyebrow">Chunks</span><span class="v">${fmt(m.n_chunks)}</span></div>
      <div class="metric"><span class="eyebrow">Specification</span><span class="v">${fmt(m.n_doc)}</span></div>
      <div class="metric"><span class="eyebrow">RTL</span><span class="v">${fmt(m.n_rtl)}</span></div>
      <div class="metric"><span class="eyebrow">Testbench</span><span class="v">${fmt(m.n_testbench)}</span></div>
      <div class="metric"><span class="eyebrow">Confirmed fixes</span><span class="v">${fmt(m.n_pattern)}</span></div>
      <div class="metric"><span class="eyebrow">Embedding model</span><span class="v" style="font-size:16px">${esc(m.embedder || "—")}</span></div>
      <div class="metric"><span class="eyebrow">Last built</span><span class="v" style="font-size:15px">${when(m.built_at)}</span></div></div></section>
    <section><h2>Search</h2>
      <form class="searchbar" id="kb-form"><input type="text" id="kb-q" placeholder="e.g. when is the interrupt line cleared" value="${esc(store.get("kbq", ""))}">
        <button class="btn primary" type="submit">Search</button></form>
      <div id="kb-hits"></div></section>
    <section><h2>How agents use it</h2><div class="panel pad prose">
      <ul><li><b>Diagnosis</b> receives the spec and RTL passages that match the failing test. A quote the model relies on may come from any retrieved spec passage, and is still checked word for word.</li>
      <li><b>Spec agent</b> receives related spec passages and earlier confirmed resolutions.</li>
      <li><b>RTL agent</b> receives confirmed fixes for similar failures, and must still take every edited line from the run's own RTL.</li>
      <li>Passages scoring under 0.35 are not shown to a model; a passage with no exact identifier match is marked as weak evidence.</li>
      <li>A fix enters the knowledge base only after the full test suite passes. Retracted fixes are removed at the next rebuild.</li></ul></div></section>`;
  const search = async () => {
    const q = $("#kb-q").value.trim(); store.set("kbq", q);
    if (!q) { $("#kb-hits").innerHTML = ""; return; }
    try {
      const r = await api(`/api/knowledge/search?q=${encodeURIComponent(q)}&k=8`);
      $("#kb-hits").innerHTML = `<div class="panel">${r.hits.length ? `<ol class="hits">${r.hits.map((h, i) => `<li>
        <div class="row" style="gap:6px 10px"><span class="mono muted">${i + 1}</span><span class="tag ${h.kind}">${KIND[h.kind] || h.kind}</span>
          <b>${esc(h.label)}</b><span class="mono muted" style="font-size:12px">${esc(h.location)}</span></div>
        <div class="mono muted" style="font-size:12px">score ${fmt(h.score, 2)} · meaning ${fmt(h.vector_score, 2)} · identifiers ${fmt(h.keyword_score, 2)}
          ${h.keyword_score > 0 ? "" : ` · <span class="weak">no exact identifier match</span>`}</div>
        <details><summary style="cursor:pointer">Show text</summary><pre>${esc(h.text)}</pre></details></li>`).join("")}</ol>` : `<div class="empty">Nothing found.</div>`}</div>`;
    } catch (e) { $("#kb-hits").innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
  };
  $("#kb-form").addEventListener("submit", (e) => { e.preventDefault(); search(); });
  if ($("#kb-q").value) search();
  $("#kb-rebuild")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Rebuilding…";
    try { const r = await api("/api/knowledge/reindex", {}); toast(`Rebuilt: ${r.n_chunks} chunks, ${r.n_pattern} confirmed fixes`); knowledgePage(main); }
    catch (err) { toast(err.message); e.target.disabled = false; e.target.textContent = "Rebuild index"; }
  });
}

/* ------------------------------------------------------------------ new request */
function requestPage(main) {
  const busy = overview.active, noModel = !(overview.llm.choices || []).length;
  const done = overview.runs.filter((r) => r.stage === "done");
  main.innerHTML = `<div class="page-head"><div><h1>New request</h1>
      <p>Describe the change you want in plain language. The agents compare it with the project's knowledge base, write a test for it,
      change the RTL and prove it — new test, full regression, synthesis, gate level and layout.</p></div></div>
    ${busy ? `<div class="notice">Run <a href="#/runs/${encodeURIComponent(busy)}" class="mono">${esc(busy)}</a> is still in progress.</div>` : ""}
    <form id="rq" class="form panel pad">
      <div class="field"><label for="rq-text">What should change?</label>
        <textarea id="rq-text" style="min-height:120px;font-family:var(--f-body);font-size:14.5px" placeholder="e.g. The interrupt output must stay high until software reads INTSTATUS." required>${esc(store.get("rq-draft", ""))}</textarea>
        <span class="hint">Name registers, signals and the behaviour you expect. The more concrete, the better the test the agent writes.</span></div>
      <div class="two">
        <div class="field"><label for="rq-id">Run name</label><input type="text" id="rq-id" placeholder="leave empty for a dated name" maxlength="64"></div>
        <div class="field"><label for="rq-base">Start from</label><select id="rq-base"><option value="">The project's design as configured</option>
          ${done.map((r) => `<option value="${esc(r.run_id)}">Signed-off run ${esc(r.run_id)}</option>`).join("")}</select></div></div>
      ${modelField("rq-llm", false)}
      <div class="field"><span class="legend">Approvals</span><div class="options">
        <label class="opt"><input type="radio" name="rq-mode" value="manual" checked><b>Ask me before each tool run</b><span>Every simulation, synthesis and layout step waits for you.</span></label>
        <label class="opt"><input type="radio" name="rq-mode" value="auto"><b>Approve automatically</b><span>Each approval is still recorded. Spec decisions take the recommended option.</span></label></div></div>
      <div id="rq-msg"></div>
      <div class="row"><button class="btn primary" type="submit" ${busy || noModel ? "disabled" : ""}>Submit request</button><span class="muted">Project: ${esc(overview.project || "")}</span></div>
    </form>`;
  $("#rq-text").addEventListener("input", (e) => store.set("rq-draft", e.target.value));
  $("#rq").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = $("#rq"), v = (n) => f.querySelector(`input[name=${n}]:checked`)?.value;
    try {
      const r = await api("/api/requests", { request: $("#rq-text").value, run_id: $("#rq-id").value.trim(), base_run: $("#rq-base").value, ...modelChoice("rq-llm"), mode: v("rq-mode") });
      store.set("rq-draft", ""); location.hash = `#/runs/${encodeURIComponent(r.run_id)}`;
      const wait = async (n) => { try { await api(`/api/runs/${encodeURIComponent(r.run_id)}`); route(); } catch { if (n) setTimeout(() => wait(n - 1), 700); } };
      wait(30);
    } catch (err) { $("#rq-msg").innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
  });
}

/* ------------------------------------------------------------------ project */
async function projectPage(main) {
  const p = await api("/api/project"), pj = p.project;
  main.innerHTML = `<div class="page-head"><div><h1>Project</h1>
      <p>What the pipeline works on. To use your own design and your own documents, point these entries at them and save;
      the knowledge base is rebuilt from the sources listed here. No agent depends on anything else.</p></div></div>
    <section class="two">
      <div class="panel pad" style="display:grid;gap:10px"><h2>${esc(pj.name)}</h2><dl class="kv">
        <dt>Top module</dt><dd class="mono">${esc(pj.design.top)}</dd><dt>RTL</dt><dd class="mono">${esc(pj.design.rtl)}</dd>
        <dt>Specification</dt><dd class="mono">${esc(pj.design.spec)}</dd><dt>Testbench</dt><dd class="mono">${esc(pj.testbench.file)}</dd>
        <dt>Helper tasks</dt><dd class="mono">${esc((pj.testbench.helper_tasks || []).join(", "))}</dd></dl></div>
      <div class="panel tablewrap"><table><thead><tr><th>Knowledge source</th><th>Kind</th></tr></thead><tbody>
        ${pj.knowledge_sources.map((k) => `<tr><td class="mono">${esc(k.path)}</td><td><span class="tag ${k.kind === "spec" ? "doc" : k.kind === "rtl" ? "rtl" : "pattern"}">${esc(k.kind)}</span></td></tr>`).join("")}
        <tr><td class="mono">data/knowledge/patterns</td><td><span class="tag pattern">confirmed changes</span></td></tr></tbody></table></div></section>
    <section><h2>Edit config/project.json</h2>
      <p class="muted" style="margin:0">Paths are relative to the repository folder and must stay inside it. The testbench needs the <code>@agent-tests</code> and <code>@agent-dispatch</code> marker comments; see the notes at the top of the file.</p>
      <textarea id="pj-text" spellcheck="false" style="min-height:420px">${esc(p.text)}</textarea>
      <div id="pj-msg"></div><div class="row"><button class="btn primary" id="pj-save">Save and rebuild the knowledge base</button></div></section>`;
  $("#pj-save").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Saving…";
    try { const r = await api("/api/project", { text: $("#pj-text").value });
      $("#pj-msg").innerHTML = `<div class="notice info">Saved. ${r.reindexed ? `Knowledge base rebuilt: ${r.meta.n_chunks} passages.` : "The knowledge base service was not running; rebuild it from the Knowledge page."}</div>`; }
    catch (err) { $("#pj-msg").innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
    e.target.disabled = false; e.target.textContent = "Save and rebuild the knowledge base";
  });
}

/* ------------------------------------------------------------------ new run */
function newRunPage(main) {
  const busy = overview.active;
  const done = overview.runs.filter((r) => r.stage === "done");
  const scs = overview.scenarios || [];
  main.innerHTML = `<div class="page-head"><div><h1>New run</h1>
      <p>Choose what to put through the flow. A run takes about 3–7 minutes, most of it place-and-route.</p></div></div>
    ${busy ? `<div class="notice">Run <a href="#/runs/${encodeURIComponent(busy)}" class="mono">${esc(busy)}</a> is still in progress. One run at a time keeps the laptop responsive.</div>` : ""}
    <form id="new" class="form panel pad">
      <div class="field"><label for="n-id">Run name</label><input type="text" id="n-id" placeholder="leave empty for a dated name" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]*">
        <span class="hint">Letters, digits, dot, dash and underscore.</span></div>
      <div class="field"><span class="legend">Scenario</span><div class="options">
        ${scs.map((sc, i) => `<label class="opt"><input type="radio" name="sc" value="${esc(sc.id)}" ${i ? "" : "checked"}><b>${esc(sc.label)}</b><span>${esc(sc.description || "")}</span></label>`).join("")}</div></div>
      <div class="field"><label for="n-base">Start from</label><select id="n-base"><option value="">The design as configured (its RTL and spec)</option>
        ${done.map((r) => `<option value="${esc(r.run_id)}">Signed-off run ${esc(r.run_id)} — its fixed RTL and clarified spec</option>`).join("")}</select>
        <span class="hint">For an injected fault, start from a signed-off run so the injected problem is the only one.</span></div>
      ${modelField("llm", true)}
      <div class="field"><span class="legend">Approvals</span><div class="options">
        <label class="opt"><input type="radio" name="mode" value="manual" checked><b>Ask me before each tool run</b><span>Lint, simulation, synthesis and layout each wait for your approval here.</span></label>
        <label class="opt"><input type="radio" name="mode" value="auto"><b>Approve automatically</b><span>For a recorded demo. Each approval is still written to the log.</span></label></div></div>
      <div id="n-msg"></div>
      <div class="row"><button class="btn primary" type="submit" ${busy ? "disabled" : ""}>Start run</button><span class="muted">You will be taken to the run as it starts.</span></div>
    </form>`;
  $("#new").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = $("#new"), v = (n) => f.querySelector(`input[name=${n}]:checked`)?.value;
    try {
      const r = await api("/api/runs", { run_id: $("#n-id").value.trim(), scenario: v("sc"), base_run: $("#n-base").value, llm: v("llm") === "none" ? "none" : "api", ...modelChoice("llm"), mode: v("mode") });
      location.hash = `#/runs/${encodeURIComponent(r.run_id)}`;
      // The manifest appears a moment after the process starts.
      const wait = async (n) => { try { await api(`/api/runs/${encodeURIComponent(r.run_id)}`); route(); } catch { if (n) setTimeout(() => wait(n - 1), 700); } };
      wait(20);
    } catch (err) { $("#n-msg").innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
  });
}

/* ------------------------------------------------------------------ models */
async function modelsPage(main) {
  const d = await api("/api/models");
  const roleTag = (p) => Object.entries(p.roles).map(([r, m]) => `<span class="tag doc">${esc(r)}: ${esc(m)}</span>`).join(" ");
  const testable = (p) => [...new Set([...Object.values(p.roles), ...p.models])];
  main.innerHTML = `<div class="page-head"><div><h1>Models</h1>
      <p>The agents call language models through provider APIs; nothing runs on this machine. Each provider needs its own key,
      and each provider and model has its own quota. Roles: <b>chat</b> (all agents), <b>fallback</b> (used when chat's quota is spent),
      <b>embeddings</b> (knowledge-base search by meaning; without one, search is by words and identifiers).</p></div></div>
    <section class="panel tablewrap"><table><thead><tr><th>Provider</th><th>Key</th><th>Roles</th><th>Test a model</th></tr></thead><tbody>
      ${d.providers.map((p) => `<tr>
        <td><b>${esc(p.provider)}</b><div class="muted mono" style="font-size:11.5px;max-width:30ch;overflow-wrap:anywhere;white-space:normal">${esc(p.base_url)}</div></td>
        <td>${p.has_key ? `<span class="pill ok">Key set</span>` : `<span class="pill idle">No key</span>`}<div class="muted mono" style="font-size:12px">$${esc(p.key_env)}</div></td>
        <td style="white-space:normal;max-width:24ch">${roleTag(p) || `<span class="muted">—</span>`}</td>
        <td>${p.has_key ? `<div class="row" style="gap:6px;flex-wrap:wrap">
            <input class="mono" list="ml-${esc(p.provider)}" data-prov="${esc(p.provider)}" placeholder="model name" style="width:19ch" value="${esc(testable(p)[0] || "")}">
            <datalist id="ml-${esc(p.provider)}">${testable(p).map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
            <button class="btn small" data-test="${esc(p.provider)}">Test</button><span class="muted" data-out="${esc(p.provider)}"></span></div>`
          : `<span class="muted">add a key first</span>`}</td></tr>`).join("")}
    </tbody></table></section>
    <section class="panel pad"><div class="prose">
      <h2>Adding a provider key and choosing models</h2>
      <p>Keys are never entered in the browser. On the server, in the project folder:</p>
      <pre class="mono" style="white-space:pre-wrap">python3 -m asic_agent models add-key openai      # key typed, not shown
python3 -m asic_agent models available openai    # models this key can use
python3 -m asic_agent models test openai gpt-4.1
python3 -m asic_agent models use chat openai gpt-4.1   # chat | fallback | embeddings</pre>
      <p>The key is stored in <code>~/.config/asic-agent/secrets.env</code> on the server (readable only by its owner), never in the project.</p>
      <p>A provider not listed here is one table in <code>config/llm.toml</code> if it speaks the OpenAI or Anthropic API
      (Azure OpenAI included; see the template there). Every run records which provider and model answered.</p></div></section>`;
  main.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    const prov = b.dataset.test, out = main.querySelector(`[data-out="${CSS.escape(prov)}"]`);
    const model = main.querySelector(`input[data-prov="${CSS.escape(prov)}"]`).value.trim();
    if (!model) { out.textContent = "enter a model name"; return; }
    b.disabled = true; out.textContent = "testing…";
    try {
      const r = await api("/api/models/test", { provider: prov, model });
      out.innerHTML = r.ok ? `<span class="pill ok">OK</span> ${esc(String(r.latency_ms))} ms`
        : `<span class="pill bad">${esc(r.error)}</span> <span title="${esc(r.detail)}">${esc((r.detail || "").slice(0, 80))}</span>`;
    } catch (e) { out.textContent = e.message; }
    b.disabled = false;
  }));
}

/* ------------------------------------------------------------------ how it works */
function howPage(main) {
  main.innerHTML = `<div class="page-head"><div><h1>How it works</h1>
      <p>From a written specification or an English change request to a signed-off layout, with software agents doing the routine work and people approving every tool run.</p></div></div>
  <section><h2>A change request, end to end</h2><div class="flowmap">
    <div class="box core"><b>1 · Retrieve & compare</b><span>The request is matched against the knowledge base: spec, RTL, testbench and past changes. Does the spec already require it, not cover it, or say otherwise?</span></div>
    <div class="box"><b>2 · Specification</b><span>If the spec doesn't cover it or conflicts, a person decides; the spec copy is updated with the decision.</span></div>
    <div class="box core"><b>3 · New test</b><span>Written from the retrieved spec and the testbench's own helpers. It must fail on the current design.</span></div>
    <div class="box core"><b>4 · RTL change</b><span>Made where the knowledge base located the behaviour. Each candidate is linted and tested on a scratch copy first.</span></div>
    <div class="box"><b>5 · Verify</b><span>The new test passes and every existing test still passes; then synthesis and gate-level tests.</span></div>
    <div class="box"><b>6 · Layout & signoff</b><span>OpenROAD places and routes; timing, DRC and equivalence are checked. The confirmed change joins the knowledge base.</span></div></div></section>
  <section class="panel pad"><div class="prose">
    <h2>Your data, not ours</h2>
    <p>Everything specific to a design comes from two places: <a href="#/project">the project settings</a> (which RTL, which specification, which testbench) and the knowledge base built from the sources listed there. The two designs shipped in <code>designs/</code> (PULP apb_gpio and a small timer) are examples. Point the knowledge sources at your own documents, RTL and testbench, save, and the same agents work from your material. The testbench needs one small convention: one task per test selected by name, and two marker comments where new tests are added.</p>
    <p>Agents never rely on hand-written hints about the design. Where to change the RTL, which helpers a test may call, and what the specification requires all come from what retrieval returns, and every run shows the passages each agent was given.</p></div></section>
  <section class="panel pad"><div class="prose">
    <h2>Verification runs: what happens when a test fails</h2>
    <p>The old approach sent every failure back to the RTL and tried again. When the real problem was an unclear specification, all three attempts were spent changing code against a requirement nobody had written down.</p>
    <p>Here the verification agent decides <b>why</b> a test failed before deciding <b>where</b> the fix goes:</p></div></section>
  <div class="routes">
    <div class="panel pad" style="display:grid;gap:6px"><span class="tag implementation_bug" style="justify-self:start">implementation bug</span><b>The spec says it; the RTL doesn't do it.</b><span class="muted">Sent to the RTL agent.</span></div>
    <div class="panel pad" style="display:grid;gap:6px"><span class="tag spec_ambiguity" style="justify-self:start">spec ambiguity</span><b>The spec never actually says it.</b><span class="muted">Sent to the spec agent, which sets out both readings for a person to choose.</span></div>
    <div class="panel pad" style="display:grid;gap:6px"><span class="tag human" style="justify-self:start">unknown</span><b>Genuinely unclear.</b><span class="muted">Sent to a person instead of guessing.</span></div></div>
  <section class="panel pad"><div class="prose">
    <h2>How the diagnosis is made</h2>
    <ol>
      <li><b>A rule check first.</b> Every test records which spec section it relies on and what it checks. If the spec never mentions what the test checks, it is a spec gap. If every term is covered, the requirement is written down and the RTL is at fault.</li>
      <li><b>The model when the rules can't tell.</b> To call something an implementation bug, the model must quote the spec sentence that requires the behaviour. The quote is checked word for word against the spec, and a second, narrow question asks whether that sentence on its own really requires it. If either check fails, the failure is treated as a spec gap.</li>
      <li><b>At gate level a spec gap is impossible.</b> The same design already passed against the same spec one stage earlier, so gate-level failures only go to the RTL agent or a person.</li>
    </ol>
    <h2>How fixes are made and checked</h2>
    <ul>
      <li><b>Spec agent.</b> Shows reading (a), taken word for word from the failing test, and reading (b), what the RTL actually does. A person chooses. The chosen reading is written into this run's copy of the spec. Choosing (a) means the RTL must change; choosing (b) means the test is wrong and a person takes over.</li>
      <li><b>RTL agent.</b> Proposes up to three small, local changes. Each one is linted and tested on a scratch copy first; only a change that passes reaches the design. Changes that do not fix their test, or break another, are rolled back.</li>
      <li><b>Three attempts.</b> The driver counts attempts and stops at three. The agents cannot extend their own budget.</li>
      <li><b>A person can always close the loop.</b> When a run stops, supply a fix on the run page; the same checks run on it, through to signoff.</li>
    </ul>
    <h2>Rules the software enforces</h2>
    <ul>
      <li>Every Verilator, Yosys and OpenROAD run waits for a person's approval, including results reused from the cache.</li>
      <li>Cached synthesis and layout are reused only when the RTL, tool versions, PDK version and constraint files are all identical.</li>
      <li>Only fixes confirmed by the full test suite are written to the knowledge base. A fix later found to be wrong is marked retracted.</li>
      <li>The netlist is never edited by hand. Gate-level problems are fixed in the RTL and synthesised again.</li>
    </ul>
    <h2>The knowledge base</h2>
    <p>The specification and RTL are split into passages (one per register section, one per logic block) and indexed two ways: by meaning, and by exact identifiers such as <code>INTSTATUS</code> or <code>r_status</code>. Each agent receives the passages that best match its question, alongside the spec section the test names. Every fix the full test suite confirms is added as a passage of its own, so a later run facing the same failure is shown how it was fixed before. The <a href="#/knowledge">Knowledge</a> page shows what is indexed and lets you search it; each diagnosis and each change in a run lists the passages it was given.</p>
    <h2>What runs where</h2>
    <p>Verilator, Yosys and the sky130 PDK run on this machine; OpenROAD runs in a pinned Docker image. Every version is pinned in <code>config/versions.lock</code>. The agents call a language model through an API: any provider in <code>config/llm.toml</code> (Groq, OpenAI, Anthropic, Gemini, Mistral, OpenRouter, or another compatible one), chosen per run. Nothing runs a model locally. Knowledge-base vectors also come from an embeddings API; without one configured, retrieval matches exact identifiers and words only.</p>
    <h2>Known limits</h2>
    <ul>
      <li>Agents were measured on apb_gpio; the tool chain is also proven on a second design (apb_timer). One timing corner, one clock at 100 MHz.</li>
      <li>New features work end to end but depend on the model: the INTTYPE=11 level-interrupt request reached signoff after the test review corrected an over-specified test.</li>
      <li>Questions phrased very differently from the spec retrieve poorly with keyword-only search; configure an embeddings provider for meaning-based matching. The agents always also get the spec section a test names.</li>
    </ul></div></section>`;
}

/* ------------------------------------------------------------------ start */
if (!location.hash) location.hash = "#/runs";
route();
