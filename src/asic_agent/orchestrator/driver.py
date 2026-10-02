"""
Pipeline driver — the full flow for the active project's design, one command.

    spec -> rtl -> verify_rtl -> synth -> verify_gate -> pnr (signoff) -> done
                       |                      |
                  diagnose-and-route     diagnose (rtl_agent | human only)

Properties enforced HERE, not in any agent prompt (README: design rules):

  1. Retry cap: attempt_count per verification stage is read from the
     manifest; past max_attempts the driver escalates to a human.
  2. A human approval gate before every EDA tool invocation (lint,
     simulate, synthesise, place-and-route) — including cache hits, where it
     degrades to "reuse result X from run Y?". --auto-approve still prints it.
  3. spec_ambiguity is overridden at gate level (agents/diagnose.py) — a
     gate-level failure routes to rtl_agent or human only.
  4. Gate-level fixes go to the RTL agent and are re-synthesised; the
     netlist is never edited.
  5. Knowledge write-back records only CONFIRMED fixes: an agent edit whose
     target tests then passed the full suite.
  6. Cache keys hash RTL content + tool versions + PDK version + constraint
     and config files — never RTL alone.

Every run works on copies under runs/<id>/ (rtl/, spec/); the design under
designs/ is never modified. The run record is runs/<id>/manifest.json
(orchestrator/manifest_schema.json).

    scripts/run_flow.sh --run-id r1                       # interactive gates
    scripts/run_flow.sh --run-id r2 --auto-approve
    scripts/run_flow.sh --run-id r3 --auto-approve --inject int_rise_bug
    scripts/run_flow.sh --run-id r4 --auto-approve --no-llm   # rule-based only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import settings
from ..agents import rtl as rtl_agent
from ..agents import spec as spec_agent
from ..agents.diagnose import _find_spec_section, _salient_terms, diagnose
from ..knowledge import client as knowledge
from ..llm import LLMError, LLMQuotaError, get_chat_client
from .cache import PNR_SH, SYNTH_SH, cache_dir, cache_key, cache_lookup, cache_store  # noqa: F401
from .manifest import MAX_ATTEMPTS, log_event, new_manifest, rel, save_manifest  # noqa: F401
from .ui import UI

ROOT = settings.ROOT
PROJECT = settings.load_project()      # one run = one process: read once


UPSTREAM_RTL = settings.project_path(PROJECT["design"]["rtl"])
UPSTREAM_SPEC = settings.project_path(PROJECT["design"]["spec"])
LINT_SH = settings.EDA_DIR / "lint.sh"
KNOWLEDGE = settings.KNOWLEDGE_RECORD
SCENARIOS = {s["id"]: s for s in PROJECT.get("fault_scenarios", [])}


def test_cases() -> list[dict]:
    """Per-test metadata from project.json testbench.metadata; without it, the
    testbench's own dispatcher (id only, rule-based diagnosis has less to go on)."""
    rel_meta = PROJECT["testbench"].get("metadata")
    if rel_meta and settings.project_path(rel_meta).exists():
        return json.loads(settings.project_path(rel_meta).read_text())["test_cases"]
    from ..eda.runner import discover
    excluded = set(PROJECT["testbench"].get("regression_exclude", []))
    tb = settings.project_path(PROJECT["testbench"]["file"])
    return [{"id": t, "optional": t in excluded} for t in discover(tb)]


def _runner_env() -> dict:
    """Environment for the test-runner subprocess: the package on PYTHONPATH."""
    src = str(ROOT / "src")
    pp = os.environ.get("PYTHONPATH", "")
    return {**os.environ, "PYTHONPATH": src + (os.pathsep + pp if pp else "")}


# ---------------------------------------------------------------------------
# Fault injection (exercises the implementation_bug branch on the run's copy)
# ---------------------------------------------------------------------------

def inject(kind: str, rtl: Path, ui: UI) -> list[str]:
    """Apply a fault scenario from project.json to the run's RTL copy. Returns
    extra tests the scenario enables (enable_tests)."""
    sc = SCENARIOS.get(kind)
    if sc is None:
        sys.exit(f"unknown fault scenario '{kind}'. This project defines: "
                 f"{', '.join(SCENARIOS) or 'none'}")
    if sc.get("kind") == "rtl_edit":
        new, n = re.subn(sc["pattern"], sc["replace"], rtl.read_text(), count=1)
        if n != 1:
            sys.exit(f"fault scenario {kind}: pattern not found in {rtl.name}")
        rtl.write_text(new)
    elif sc.get("kind") != "enable_tests":
        sys.exit(f"fault scenario {kind}: unknown kind '{sc.get('kind')}'")
    ui.info(sc.get("note") or sc.get("description") or f"Applied fault scenario {kind}.")
    return list(sc.get("tests", []))



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def driver_line(rtl_text: str, signal: str) -> int | None:
    """Index of the line that drives `signal` (continuous assign or flop)."""
    pat = re.compile(rf"^\s*(assign\s+{re.escape(signal)}\b|{re.escape(signal)}\s*<=)")
    return next((i for i, l in enumerate(rtl_text.splitlines()) if pat.search(l)), None)


def rtl_excerpt(rtl_text: str, terms: set[str], window: int = 45,
                numbered: bool = True, anchor: int | None = None) -> str:
    """The RTL window that mentions the test's terms most.

    Terms are weighted by rarity across the file. Raw counts let a term that
    is everywhere (`gpio` is in every register name) pick the window — on
    1 Oct that sent the RTL agent to the INTTYPE write decoder for an
    interrupt-output bug.
    """
    lines = rtl_text.splitlines()
    low = [l.lower() for l in lines]
    weight = {}
    for k in {t for t in terms if len(t) >= 4}:
        df = sum(1 for l in low if k in l)
        if 0 < df <= max(3, len(lines) // 12):
            weight[k] = 1.0 / df
    best, best_score = 0, -1.0
    starts = range(0, max(1, len(lines) - window + 1), 3)
    if anchor is not None:
        # Only windows that contain the line driving the observed output.
        starts = [st for st in range(max(0, anchor - window + 5), min(anchor, len(lines)))]
    for start in starts:
        chunk = low[start:start + window]
        score = sum(w for k, w in weight.items() for l in chunk if k in l)
        if score > best_score:
            best, best_score = start, score
    seg = lines[best:best + window]
    if not numbered:
        return "\n".join(seg)
    return "\n".join(f"{best + i + 1:4d}  {l}" for i, l in enumerate(seg))


def test_terms(tm: dict) -> set[str]:
    return _salient_terms(tm.get("pass_condition", "") + " " + tm.get("description", "")
                          + " " + tm.get("id", "").replace("_", " "))


def run_sim(rtl_or_netlist: Path, out: Path, retry_from: Path | None,
            gate: bool, jobs: int | None, tests: list[str], tb: Path | None = None) -> dict:
    cmd = [sys.executable, "-m", "asic_agent.eda.runner", "--out", str(out), "--tests", ",".join(tests)]
    if tb:
        cmd += ["--tb", str(tb)]   # a run's own testbench copy (request pipeline)
    cmd += ["--gate", str(rtl_or_netlist)] if gate else ["--rtl", str(rtl_or_netlist)]
    if retry_from:
        cmd += ["--retry-from", str(retry_from)]
    if jobs:
        cmd += ["--jobs", str(jobs)]
    out.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, env=_runner_env())
    (out / "runner.log").write_text(p.stdout + p.stderr)
    results = out / "results.json"
    if not results.exists():
        return {"build_ok": False, "results": []}
    return json.loads(results.read_text())


# ---------------------------------------------------------------------------
# Stage: spec  (approve; or resolve a routed spec_ambiguity with the spec agent)
# ---------------------------------------------------------------------------

def stage_spec(m: dict, ui: UI, client, spec: Path, rtl: Path) -> str:
    ss = m["stages"]["spec"]
    ui.stage("spec", f"revision {ss['revision']}")
    pending = m.get("pending")

    if not pending or pending["route_to"] != "spec_agent":
        ui.info(f"Specification: {rel(spec)}")
        ss["approved"] = True
        log_event(m, "spec_approved", {"revision": ss["revision"]})
        return "rtl"

    if client is None:
        ui.info("spec_ambiguity routed here, but no LLM is configured for the spec "
                "agent. Escalating to a human.")
        log_event(m, "spec_agent_unavailable")
        return "failed"

    meta = {tc["id"]: tc for tc in test_cases()}
    spec_text = spec.read_text()
    rtl_text = rtl.read_text()
    next_items = []
    for item in pending["items"]:
        tm = meta.get(item["test"], {"id": item["test"]})
        section = _find_spec_section(spec_text, tm.get("spec_ref", "")) or ""
        ui.info(f"Spec agent: resolving ambiguity behind {item['test']} ...")
        t0 = time.time()
        try:
            hits = knowledge.retrieve(f"{tm.get('pass_condition', '')}. {item['reason']}",
                                      k=4, kinds=("doc", "pattern"))
            res = spec_agent.resolve(client, tm, section, item["reason"],
                                     rtl_excerpt(rtl_text, test_terms(tm)), hits)
        except LLMError as e:
            ui.info(f"Spec agent failed ({e}). Escalating to a human.")
            log_event(m, "spec_agent_failed", {"test": item["test"], "error": str(e)[:300]})
            return "failed"
        ui.info(f"  ({time.time() - t0:.0f}s) ambiguous: \"{res.ambiguous_text[:150]}\"")
        if res.draft:
            ui.info(f"  agent's suggested wording: {res.draft[:200]}")
        choice = ui.choose(f"Which reading does the spec intend for {item['test']}?",
                           {"a": f"[as the test assumes] {res.reading_a}",
                            "b": f"[as the RTL does] {res.reading_b}"}, res.recommended,
                           context={"test": item["test"], "ambiguous": res.ambiguous_text,
                                    "why": res.why, "draft": res.draft,
                                    "consequence": spec_agent.CONSEQUENCE})
        if choice is None:
            log_event(m, "spec_decision_escalated", {"test": item["test"]})
            ui.info("No reading chosen. Escalating to a human.")
            return "failed"
        res.chosen = choice
        res.clarification = spec_agent.clarification_for(res, choice)
        res.consequence = spec_agent.CONSEQUENCE[choice]
        ss["revision"] += 1
        spec_text = spec_agent.apply_clarification(spec_text, tm.get("spec_ref", ""),
                                                   ss["revision"], res)
        ss["clarifications"].append({**res.to_dict(), "revision": ss["revision"]})
        log_event(m, "spec_clarified", {"test": item["test"], "revision": ss["revision"],
                                        "chosen": choice, "consequence": res.consequence})
        ui.info(f"  Clarification (rev {ss['revision']}): {res.clarification[:200]}")
        ui.info(f"  Consequence: {res.consequence}")
        if res.consequence == "test_must_change":
            ui.info("  The RTL already meets the clarified spec; the TEST is wrong. "
                    "Tests are not agent-editable — escalating to a human.")
            spec.write_text(spec_text)
            log_event(m, "test_change_required", {"test": item["test"]})
            return "failed"
        next_items.append({**item, "diagnosis": f"Spec clarified (rev {ss['revision']}): "
                                                f"{res.clarification}"})

    spec.write_text(spec_text)
    ss["approved"] = True
    m["pending"] = {"route_to": "rtl_agent", "items": next_items}
    # The previous failure was against an older spec; retry the full suite.
    m["stages"]["verify_rtl"]["last_failure"] = None
    return "rtl"


# ---------------------------------------------------------------------------
# Stage: rtl  (the RTL agent edits the run's copy)
# ---------------------------------------------------------------------------

def stage_rtl(m: dict, ui: UI, client, run_dir: Path, spec: Path, rtl: Path) -> str:
    ui.stage("rtl")
    ui.info(f"RTL under test: {rel(rtl)}")
    m["stages"]["rtl"]["files_changed"] = [rel(rtl)]
    pending = m.get("pending")
    if not pending or pending["route_to"] != "rtl_agent":
        log_event(m, "rtl_ready")
        return "verify_rtl"

    if client is None:
        ui.info("RTL agent needs an LLM (none configured): RTL unchanged.")
        log_event(m, "rtl_unchanged", {"reason": "no llm"})
        return "verify_rtl"

    meta = {tc["id"]: tc for tc in test_cases()}
    spec_text = spec.read_text()
    rs = m["stages"]["rtl"]
    n = len(rs["edits"])
    # Snapshot: if this pass does not fix its targets, or breaks anything else,
    # verification rolls the RTL back to here before the next attempt.
    hist = rtl.parent / "history"
    hist.mkdir(exist_ok=True)
    snap = hist / f"{rtl.stem}.before_pass{n + 1}.sv"
    shutil.copy(rtl, snap)
    vs = m["stages"]["verify_rtl"]
    rs["open_pass"] = {"snapshot": rel(snap), "first_edit": n + 1,
                       "targets": [i["test"] for i in pending["items"][:1]],
                       "prev_failing": (vs.get("last_failure") or {}).get("failing_test_ids", [])}
    # One test per pass. Failures often share a root cause (an int_rise bug
    # also fails intstatus_clear_on_read); fixing the first and re-running
    # shows which others remain, instead of the agent patching symptoms.
    for item in pending["items"][:1]:
        tm = meta.get(item["test"], {"id": item["test"]})
        section = _find_spec_section(spec_text, tm.get("spec_ref", "")) or ""
        rtl_text = rtl.read_text()
        ui.info(f"RTL agent: fixing {item['test']} ...")
        # Feed back everything already tried for this test: edits rolled back
        # after verification, and edits rejected before they were applied.
        tried = lambda: [  # noqa: E731
            f"<find>\n{e['find']}\n</find>\n<replace>\n{e['replace']}\n</replace>"
            + (f"\n(rejected: {e['error']})" if e.get("error") else "")
            + (f"\n(tested: {e['screen']})" if e.get("screen") and e["screen"] != "pass" else "")
            for e in rs["edits"] if e["test_id"] == item["test"]
            and (e.get("reverted") or not e.get("applied"))]
        # Generate-and-test. A 7B model at temperature 0 repeats itself, so up
        # to CANDIDATES edits are drawn at rising temperature and each one is
        # screened (lint + the target test + smoke set) on a scratch copy; the
        # first that passes is applied. Screening is a Verilator run, so it is
        # behind the approval gate like every other tool call.
        sig = (tm.get("observes") or [None])[0]
        sig = None if sig == "PRDATA" else sig   # register reads: no single driver line
        at = driver_line(rtl_text, sig) if sig else None
        observed = (f"The test observes the output `{sig}`, driven here: "
                    f"`{rtl_text.splitlines()[at].strip()}`" if at is not None else "")
        excerpt = rtl_excerpt(rtl_text, test_terms(tm), window=36, numbered=False, anchor=at)
        excerpt = _declarations(rtl_text, excerpt) + excerpt
        screen = sorted(set([item["test"]] + SMOKE) & set(m["tests"]))
        hits = knowledge.retrieve(_fix_query(tm, item["reason"], excerpt), k=4,
                                  kinds=("pattern", "doc"))
        if any(h["kind"] == "pattern" for h in hits):
            ui.info("  knowledge base: " + "; ".join(h["label"] for h in hits if h["kind"] == "pattern"))
        if not ui.approve(f"screen up to {CANDIDATES} candidate RTL edits for {item['test']} "
                          f"(verilator lint + {len(screen)} tests each)"):
            log_event(m, "approval_declined", {"stage": "rtl", "tool": "screen"})
            return "failed"
        chosen = None
        for k, temp in enumerate(TEMPERATURES[:CANDIDATES]):
            t0 = time.time()
            try:
                edit = rtl_agent.propose(client, tm, item["reason"], section, excerpt,
                                         item.get("diagnosis", "implementation_bug"),
                                         tried(), observed, temperature=temp, evidence=hits)
            except LLMError as e:
                ui.info(f"  candidate {k + 1}: no usable edit ({str(e)[:120]})")
                log_event(m, "rtl_agent_failed", {"test": item["test"], "error": str(e)[:300]})
                continue
            new_text = rtl_agent.apply(rtl_text, edit, rtl.name)
            n += 1
            rec = {**edit.to_dict(), "n": n, "candidate": k + 1, "temperature": temp,
                   "spec_ref": tm.get("spec_ref", ""), "pass_condition": tm.get("pass_condition", ""),
                   "symptom": item["reason"][:300],
                   "seconds": round(time.time() - t0, 1), "confirmed": False,
                   "reverted": False, "screen": None}
            rs["edits"].append(rec)
            change = " | ".join(l for l in edit.diff.splitlines()
                                if l[:1] in "+-" and l[:3] not in ("+++", "---"))[:150]
            if not edit.applied:
                ui.info(f"  candidate {k + 1} (T={temp}): rejected — {edit.error}")
                log_event(m, "rtl_edit", {"test": item["test"], "applied": False,
                                          "error": edit.error, "candidate": k + 1})
                continue
            verdict = _screen(run_dir, f"p{len(rs['edits'])}c{k + 1}", new_text, screen)
            rec["screen"] = verdict
            # Not applied to the run's RTL unless it survives screening.
            rec["applied"] = False
            ui.info(f"  candidate {k + 1} (T={temp}, {rec['seconds']:.0f}s): {change}")
            ui.info(f"     screen: {verdict}")
            log_event(m, "rtl_edit", {"test": item["test"], "applied": False,
                                      "candidate": k + 1, "screen": verdict})
            if verdict == "pass":
                chosen = (rec, new_text, edit)
                break
        if chosen:
            rec, new_text, edit = chosen
            rec["applied"] = True
            rtl.write_text(new_text)
            dpath = run_dir / f"rtl_edit{rec['n']}_{item['test']}.diff"
            dpath.write_text(edit.diff)
            rec["diff_path"] = rel(dpath)
            ui.info(f"  applied candidate {rec['candidate']}: {edit.why[:160]}")
            log_event(m, "rtl_edit", {"test": item["test"], "applied": True,
                                      "candidate": rec["candidate"]})
        else:
            ui.info("  no candidate passed screening; RTL unchanged for this attempt")
    return "verify_rtl"


# ---------------------------------------------------------------------------
# Stages: verify_rtl and verify_gate  (lint, run, diagnose, route)
# ---------------------------------------------------------------------------

def stage_verify(m: dict, ui: UI, client, run_dir: Path, spec: Path, rtl: Path,
                 level: str, jobs: int | None) -> str:
    key = "verify_rtl" if level == "rtl" else "verify_gate"
    vs = m["stages"][key]
    attempt = vs["attempt_count"] + 1
    ui.stage(key, f"attempt {attempt} of {vs['max_attempts']}")
    adir = run_dir / f"{key}{attempt}"
    adir.mkdir(parents=True, exist_ok=True)
    meta = {tc["id"]: tc for tc in test_cases()
            if tc["id"] in m["tests"]}

    if level == "rtl":
        # Step 0 — lint. Only RTL is linted; a netlist is tool output.
        if not ui.approve(f"verilator --lint-only -Wall {rtl.name}"):
            log_event(m, "approval_declined", {"stage": key, "tool": "lint"})
            return "failed"
        vs["attempt_count"] = attempt
        p = subprocess.run(["bash", str(LINT_SH), str(rtl)], capture_output=True, text=True)
        (adir / "lint.log").write_text(p.stdout + p.stderr)
        vs["lint_passed"] = p.returncode == 0
        if not vs["lint_passed"]:
            ui.info(f"LINT FAILED — {rel(adir / 'lint.log')}")
            err = next((l for l in (p.stdout + p.stderr).splitlines() if "%Error" in l
                        or "%Warning" in l), "lint failed")
            prev_failure = vs.get("last_failure") or {}
            vs["last_failure"] = {**prev_failure, "root_cause": "implementation_bug",
                                  "route_to": "rtl_agent"}
            log_event(m, "lint_failed", {"attempt": attempt, "error": err[:200]})
            # An agent edit that breaks lint is rolled back; the same tests go
            # back to the RTL agent with the lint error as context.
            if _revert_open_pass(m, ui, rtl, f"lint error: {err[:160]}") is None \
                    and not m.get("pending"):
                m["pending"] = {"route_to": "rtl_agent", "items": [
                    {"test": "lint", "reason": f"lint error: {err}",
                     "diagnosis": "implementation_bug"}]}
            return _route_or_stop(m, ui, key, "rtl_agent")
        ui.info("Lint clean.")
        target = rtl
    else:
        netlist = run_dir / "synth" / f"{os.environ['TOP']}.netlist.v"
        target = netlist
        vs["attempt_count"] = attempt

    prior = vs.get("last_failure") or {}
    retry_from = ROOT / prior["results_path"] if prior.get("results_path") else None
    what = ("fail-fast: previous failures + smoke set, then the rest"
            if retry_from else f"all {len(meta)} tests")
    if not ui.approve(f"verilator build + simulate {target.name} at {level} level ({what})"):
        log_event(m, "approval_declined", {"stage": key, "tool": "sim"})
        return "failed"
    t0 = time.time()
    res = run_sim(target, adir / "sim", retry_from, level == "gate", jobs, m["tests"],
                  ROOT / m["tb_path"] if m.get("tb_path") else None)
    vs["build_ok"] = res.get("build_ok", False)
    if not vs["build_ok"] or not res["results"]:
        # Never read "no tests ran" as "no tests failed".
        ui.info(f"BUILD FAILED or no tests ran — {rel(adir / 'sim')}/build.log")
        log_event(m, "build_failed", {"stage": key, "attempt": attempt})
        return _route_or_stop(m, ui, key, "human")

    results = res["results"]
    ui.info(f"{res['passed']}/{res['total']} passed ({time.time() - t0:.0f}s)")
    vs["test_cases"] = [{"id": r["test"], "status": r["status"].lower(),
                         "reason": r["detail"]} for r in results]
    for r in results:
        if r["status"] != "PASS":
            ui.info(f"  [FAIL] {r['test']:24} {'; '.join(r['failed_checks'][:1]) or r['detail']}")

    failing = [r for r in results if r["status"] != "PASS"]

    # Did the RTL agent's last pass help? Roll it back if its target still
    # fails or anything that passed before now fails; route the same work back.
    op = m["stages"]["rtl"].get("open_pass")
    if level == "rtl" and op:
        now = {r["test"] for r in failing}
        broke = now - set(op["prev_failing"]) - set(op["targets"])
        unfixed = now & set(op["targets"])
        if broke or unfixed:
            why = (f"broke {', '.join(sorted(broke))}" if broke else "") + \
                  ("; " if broke and unfixed else "") + \
                  (f"{', '.join(sorted(unfixed))} still failing" if unfixed else "")
            items = _revert_open_pass(m, ui, rtl, why)
            if items:
                vs["last_failure"] = {
                    "failing_test_ids": sorted(set(op["prev_failing"]) | set(op["targets"])),
                    "root_cause": "implementation_bug", "route_to": "rtl_agent",
                    "results_path": prior.get("results_path")}
                log_event(m, f"{key}_failed", {"attempt": attempt, "failing": sorted(now),
                                               "route_to": "rtl_agent", "diagnoses": [],
                                               "note": f"agent edit rolled back: {why}"})
                print()
                ui.info("ROUTING: rtl_agent (same failures, previous edit rolled back)")
                return _route_or_stop(m, ui, key, "rtl_agent")
        else:
            m["stages"]["rtl"]["open_pass"] = None

    if not failing:
        vs["last_failure"] = None
        _confirm_fixes(m, ui, {r["test"] for r in results}, level)
        m["pending"] = None
        log_event(m, f"{key}_passed", {"attempt": attempt})
        return "synth" if level == "rtl" else "pnr"

    # Step 4 — diagnose each failure, then route.
    if m.get("baseline"):
        # The old design, kept only for comparison: no diagnosis, every
        # failure goes back to the RTL agent until the attempt cap.
        items = [{"test": r["test"], "reason": "; ".join(r["failed_checks"]) or r["detail"],
                  "diagnosis": "not diagnosed (baseline)", "root_cause": "undiagnosed"}
                 for r in failing]
        vs["last_failure"] = {"failing_test_ids": [r["test"] for r in failing],
                              "root_cause": "undiagnosed", "route_to": "rtl_agent",
                              "results_path": rel(adir / "sim" / "results.json")}
        m["pending"] = {"route_to": "rtl_agent", "items": items}
        log_event(m, f"{key}_failed", {"attempt": attempt, "failing": [r["test"] for r in failing],
                                       "route_to": "rtl_agent", "diagnoses": [], "baseline": True})
        print()
        ui.info("ROUTING: rtl_agent  (baseline: no diagnosis)")
        return _route_or_stop(m, ui, key, "rtl_agent")
    spec_text = spec.read_text()
    rtl_text = rtl.read_text()
    diagnoses, items = [], []
    print()
    ui.info("DIAGNOSIS")
    for r in failing:
        tm = meta.get(r["test"], {"id": r["test"]})
        reason = "; ".join(r["failed_checks"]) or r["detail"]
        t1 = time.time()
        # Knowledge base: spec and RTL evidence only. Past fixes are kept out
        # of diagnosis so the verdict rests on what the spec says.
        hits = knowledge.retrieve(f"{tm.get('description', '')}. {tm.get('pass_condition', '')}. "
                                  f"{reason}", k=4, kinds=("doc", "rtl")) if client else []
        d = diagnose(client, tm, spec_text, reason, stage=key,
                     rtl_excerpt=rtl_excerpt(rtl_text, test_terms(tm)), evidence=hits)
        diagnoses.append(d)
        items.append({"test": r["test"], "reason": reason,
                      "diagnosis": f"{d.root_cause}: {d.rationale}", "root_cause": d.root_cause})
        ui.info(f"  {d.test_id}: {d.root_cause} -> {d.route_to}  "
                f"[{d.method}/{d.confidence}, {time.time() - t1:.0f}s]")
        ui.info(f"     {d.rationale[:220]}")

    spec_amb = [d for d in diagnoses if d.root_cause == "spec_ambiguity"]
    unknown = [d for d in diagnoses if d.root_cause == "unknown"]
    if spec_amb:
        chosen, cause = "spec_agent", "spec_ambiguity"
        items = [i for i in items if i["root_cause"] == "spec_ambiguity"]
    elif len(unknown) == len(diagnoses):
        chosen, cause = "human", "unknown"
    else:
        chosen, cause = "rtl_agent", "implementation_bug"
        items = [i for i in items if i["root_cause"] == "implementation_bug"]

    vs["last_failure"] = {
        "failing_test_ids": [r["test"] for r in failing],
        "root_cause": cause, "route_to": chosen,
        "results_path": rel(adir / "sim" / "results.json"),
    }
    m["pending"] = {"route_to": chosen, "items": items}
    log_event(m, f"{key}_failed", {"attempt": attempt, "failing": [r["test"] for r in failing],
                                   "route_to": chosen, "diagnoses": [d.to_dict() for d in diagnoses]})
    print()
    ui.info(f"ROUTING: {chosen}")
    if unknown and chosen != "human":
        ui.info(f"  still undiagnosed, needs a human after this fix: "
                f"{', '.join(d.test_id for d in unknown)}")
    return _route_or_stop(m, ui, key, chosen)


CANDIDATES = 3
TEMPERATURES = [0.0, 0.6, 0.9]
SMOKE = PROJECT.get("testbench", {}).get("smoke", ["reset_values"])


def _fix_query(tm: dict, reason: str, excerpt: str) -> str:
    """What the RTL agent searches the knowledge base with. Similar bugs share
    a spec section and RTL signals more than they share wording: the spec
    section and the excerpt's internal signals go in alongside the symptom."""
    sigs = sorted(set(re.findall(r"\b[rs]_[A-Za-z0-9_]+\b", excerpt)))[:20]
    return (f"Spec section: {tm.get('spec_ref', '')}. Requirement tested: "
            f"{tm.get('pass_condition', '')}. Symptom: {reason}. Signals: {', '.join(sigs)}")


def _declarations(rtl_text: str, excerpt: str, limit: int = 14) -> str:
    """Declarations of the internal signals an excerpt uses, so the agent
    knows their widths (it treated the 32-bit r_status as one bit)."""
    uses = re.findall(r"\b([rs]_[A-Za-z0-9_]+)\b", excerpt)
    count = {u: uses.count(u) for u in set(uses)}
    decls = []
    for l in rtl_text.splitlines():
        if re.match(r"\s*logic\b", l):
            hit = max((c for u, c in count.items() if re.search(rf"\b{u}\b", l)), default=0)
            if hit:
                decls.append((-hit, l.strip()))
    decls = [l for _, l in sorted(decls)]  # most-used signals first
    if not decls:
        return ""
    return ("// declarations (from elsewhere in the file)\n" + "\n".join(decls[:limit])
            + "\n// ...\n")


def _screen(run_dir: Path, tag: str, text: str, tests: list[str], tb: Path | None = None) -> str:
    """Lint + run `tests` on a candidate RTL. Returns 'pass' or why not."""
    d = run_dir / "candidates" / tag
    d.mkdir(parents=True, exist_ok=True)
    f = d / UPSTREAM_RTL.name          # same name: the lint waivers match on it
    f.write_text(text)
    p = subprocess.run(["bash", str(LINT_SH), str(f)], capture_output=True, text=True)
    if p.returncode:
        # The agent needs the message itself ("Can't find definition of
        # variable: 's_gpio_in'"), not just that lint failed — on 2 Oct it
        # repeated the same undeclared signal across nine candidates without it.
        errs = [re.sub(r"^%(Error|Warning)[-A-Z]*: .*?:\d+:\d+: ", r"%\1: ", l)
                for l in (p.stdout + p.stderr).splitlines()
                if l.startswith(("%Error", "%Warning")) and "Exiting due to" not in l]
        return "lint: " + ("; ".join(errs[:3]) if errs else "failed")
    res = run_sim(f, d / "sim", None, False, None, tests, tb)
    if not res.get("build_ok"):
        return "build failed"
    fails = [r for r in res["results"] if r["status"] != "PASS"]
    if not fails:
        return "pass"
    # The failing check's own message, not just the test name: "pad 5: ..."
    # is what tells the agent its fix only covered pad 0.
    return "; ".join(f"{r['test']}: {(r['failed_checks'] or [r['detail']])[0]}" for r in fails)


def _revert_open_pass(m: dict, ui: UI, rtl: Path, why: str) -> list | None:
    """Restore the RTL from before the agent's last pass. Returns the pending
    items to retry (with the failure noted), or None if there was no pass."""
    rs = m["stages"]["rtl"]
    op = rs.get("open_pass")
    if not op:
        return None
    shutil.copy(ROOT / op["snapshot"], rtl)
    for e in rs["edits"]:
        if e["n"] >= op["first_edit"] and e["applied"]:
            e["reverted"] = True
            e["revert_reason"] = why
    rs["open_pass"] = None
    ui.info(f"Rolled back the RTL agent's edit(s): {why}")
    log_event(m, "rtl_edit_reverted", {"reason": why})
    items = (m.get("pending") or {}).get("items") or []
    for i in items:
        i["reason"] = f"{i['reason']} (previous fix attempt rolled back: {why})"
    m["pending"] = {"route_to": "rtl_agent", "items": items}
    return items


def _route_or_stop(m: dict, ui: UI, key: str, route_to: str) -> str:
    """Apply the retry bound. This is where the cap is actually enforced."""
    vs = m["stages"][key]
    if route_to == "human":
        ui.info("Escalating to a human: the diagnosis was inconclusive.")
        return "failed"
    if vs["attempt_count"] >= vs["max_attempts"]:
        ui.info(f"Attempt cap reached ({vs['max_attempts']}). Escalating to a human "
                f"rather than looping.")
        log_event(m, "attempt_cap_reached", {"stage": key, "route_to": route_to})
        return "failed"
    return "spec" if route_to == "spec_agent" else "rtl"


def _confirm_fixes(m: dict, ui: UI, passed: set[str], level: str) -> None:
    """RAG write-back: only edits whose target test now passes the FULL suite."""
    new = [e for e in m["stages"]["rtl"]["edits"]
           if e["applied"] and not e["confirmed"] and e["test_id"] in passed]
    if not new:
        return
    KNOWLEDGE.parent.mkdir(parents=True, exist_ok=True)
    clar = {c["test_id"]: c for c in m["stages"]["spec"]["clarifications"]}
    with KNOWLEDGE.open("a") as f:
        for e in new:
            e["confirmed"] = True
            c = clar.get(e["test_id"])
            f.write(json.dumps({
                "tag": "known_failure_pattern", "block": PROJECT["design"]["top"],
                "run_id": m["run_id"], "test": e["test_id"],
                "root_cause": "spec_ambiguity" if c else "implementation_bug",
                "spec_clarification": c["clarification"] if c else None,
                "fix_summary": e["why"], "diff": e["diff"],
                "spec_ref": e.get("spec_ref", ""), "pass_condition": e.get("pass_condition", ""),
                "symptom": e.get("symptom", ""),
                "fixed_by": e.get("author", "rtl_agent"),
                "confirmed_at_level": level,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    ui.info(f"Write-back: {len(new)} confirmed fix(es) -> {rel(KNOWLEDGE)}")
    log_event(m, "writeback", {"tests": [e["test_id"] for e in new]})
    meta = knowledge.reindex()
    if meta:
        ui.info(f"Knowledge base rebuilt: {meta['n_chunks']} chunks, "
                f"{meta['n_pattern']} confirmed fix pattern(s)")
        log_event(m, "knowledge_reindexed", {"n_chunks": meta["n_chunks"],
                                             "n_pattern": meta["n_pattern"]})
    else:
        ui.info("Knowledge base service not running: fix recorded, index not rebuilt")


# ---------------------------------------------------------------------------
# Stages: synth and pnr (cached)
# ---------------------------------------------------------------------------

def _cached_tool(m: dict, ui: UI, kind: str, label: str, rtl: Path, out: Path,
                 cmd: list[str]) -> bool:
    st = m["stages"]["synth" if kind == "synth" else "signoff"]
    key = cache_key(kind, rtl)
    st["cache_key"] = key
    hit = cache_lookup(kind, key)
    if hit:
        if not ui.approve(f"reuse {label} result {key[:12]} from run {hit['run_id']} "
                          f"(identical RTL, tools, PDK and constraints)"):
            log_event(m, "approval_declined", {"stage": kind})
            return False
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(cache_dir() / kind / key / "out", out)
        st.update(cache_hit=True, reused_from_run=hit["run_id"])
        log_event(m, f"{kind}_cache_hit", {"key": key, "from": hit["run_id"]})
        return True
    if not ui.approve(label):
        log_event(m, "approval_declined", {"stage": kind})
        return False
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{kind}_driver.log").write_text(p.stdout + p.stderr)
    st.update(cache_hit=False, reused_from_run=None, seconds=round(time.time() - t0, 1))
    if p.returncode != 0:
        ui.info(f"{kind.upper()} FAILED — {rel(out)}")
        log_event(m, f"{kind}_failed")
        return False
    cache_store(kind, key, out, m["run_id"])
    return True


def stage_synth(m: dict, ui: UI, run_dir: Path, rtl: Path) -> str:
    ui.stage("synth")
    out = run_dir / "synth"
    ok = _cached_tool(m, ui, "synth", f"yosys synthesis of {rtl.name} -> sky130_fd_sc_hd",
                      rtl, out, ["bash", str(SYNTH_SH), str(out), str(rtl)])
    if not ok:
        return "failed"
    stat = (out / "stat.txt").read_text()
    # "     1476 1.95E+04 cells": count, then area in E-notation.
    cells = re.search(r"^\s*(\d+)\s+\S+\s+cells\s*$", stat, re.M)
    area = re.search(r"[Cc]hip area.*?([\d.]+)\s*$", stat, re.M)
    s = m["stages"]["synth"]
    s.update(netlist=rel(out / f"{os.environ['TOP']}.netlist.v"),
             cells=int(cells.group(1)) if cells else None,
             area_um2=float(area.group(1)) if area else None)
    ui.info(f"Synthesis: {s['cells']} cells, {s['area_um2']} um^2"
            + ("  (cache hit)" if s["cache_hit"] else ""))
    log_event(m, "synth_done", {"cells": s["cells"], "area_um2": s["area_um2"]})
    return "verify_gate"


def stage_pnr(m: dict, ui: UI, run_dir: Path, rtl: Path) -> str:
    ui.stage("pnr + signoff", "OpenROAD on sky130hd")
    out = run_dir / "pnr"
    ok = _cached_tool(m, ui, "pnr", f"OpenROAD place-and-route + signoff of {rtl.name} "
                      f"(~4 min)", rtl, out, ["bash", str(PNR_SH), str(out), str(rtl)])
    if not ok:
        return "failed"
    # Re-summarise from this run's own copy, so a cache hit's report paths
    # point here rather than at the run that produced them.
    p = subprocess.run([sys.executable, str(settings.EDA_DIR / "pnr_summary.py"), str(out)],
                       capture_output=True, text=True, check=True)
    (out / "pnr_summary.json").write_text(p.stdout)
    summ = json.loads(p.stdout)
    summ["reports"] = {k: rel(Path(v)) for k, v in summ["reports"].items()}
    m["stages"]["signoff"].update(summ)
    ui.info(f"setup WNS {summ['setup_wns_ns']} ns, hold WNS {summ['hold_wns_ns']} ns, "
            f"DRC {summ['route_drc_errors']}, LEC {'equivalent' if summ['lec_equivalent'] else 'FAIL'}, "
            f"{summ['stdcells']} cells, {summ['die_area_um2']} um^2 die, {summ['power_mw']} mW")
    log_event(m, "signoff", {"pass": summ["signoff_pass"]})
    if not summ["signoff_pass"]:
        ui.info("Signoff FAILED. Escalating to a human.")
        return "failed"
    return "done"


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Run the full ASIC agent pipeline on the active project.")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--auto-approve", action="store_true",
                    help="Skip the interactive gates (CI / recorded demo). Each gate is still printed.")
    ap.add_argument("--inject", default=None, metavar="SCENARIO",
                    help="Apply a fault scenario from project.json fault_scenarios "
                         f"({', '.join(SCENARIOS) or 'none defined'}).")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true",
                    help="No model: heuristic diagnosis only, no spec/RTL agents.")
    ap.add_argument("--stop-after", choices=["verify_rtl", "verify_gate"], default=None,
                    help="End the run after this stage passes (skips the slower back end).")
    ap.add_argument("--base-run", default=None, metavar="RUN_ID",
                    help="Start from a finished run's RTL AND spec (its clarifications "
                         "travel with its fixes), so an injected fault is the only failure.")
    ap.add_argument("--web", action="store_true",
                    help="Ask approvals and decisions through the web app (app/server.py).")
    ap.add_argument("--request-file", type=Path, default=None,
                    help="Run the change-request pipeline on the request in this text file.")
    ap.add_argument("--baseline", action="store_true",
                    help="Old design, for comparison only: no diagnosis, every failure "
                         "goes to the RTL agent until the attempt cap.")
    ap.add_argument("--resume", metavar="RUN_ID", default=None,
                    help="Continue a run that escalated to a person, after a human fix.")
    ap.add_argument("--human-fix", type=Path, default=None,
                    help="With --resume: unified diff against the run's RTL copy, written by a person.")
    args = ap.parse_args()
    stop_on_sigterm()

    for var in ("BUILD_DIR", "TOP", "RTL_DIR", "LIB_TT", "ORFS_IMAGE"):
        if not os.environ.get(var):
            sys.exit(f"{var} is not set — run `source scripts/env.sh` first")

    if args.resume:
        return resume(args)
    if args.request_file:
        from . import request_flow
        return request_flow.run(args, UI(args.auto_approve, args.web))

    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = settings.RUNS_DIR / run_id
    if run_dir.exists():
        sys.exit(f"{rel(run_dir)} already exists — pick another --run-id")
    rtl = run_dir / "rtl" / UPSTREAM_RTL.name
    spec = run_dir / "spec" / UPSTREAM_SPEC.name
    rtl.parent.mkdir(parents=True)
    spec.parent.mkdir(parents=True)
    base = None
    if args.base_run:
        base = json.loads((settings.RUNS_DIR / args.base_run / "manifest.json").read_text())
        if base["current_stage"] != "done":
            sys.exit(f"--base-run {args.base_run} did not complete; start from a finished run")
    shutil.copy(ROOT / base["working_rtl"] if base else UPSTREAM_RTL, rtl)
    shutil.copy(ROOT / base["stages"]["spec"]["spec_path"] if base else UPSTREAM_SPEC, spec)

    ui = UI(args.auto_approve, args.web)
    ui.gate_dir = run_dir
    client = None
    if not args.no_llm:
        try:
            client = get_chat_client()
            ui.info(f"LLM: {client.name} / {client.model}")
            if hasattr(client, "on_switch"):
                client.on_switch = lambda old, new, why: (
                    ui.info(f"Model quota for {old} used up — continuing on {new}\n    provider said: {why[:300]}"),
                    log_event(m, "model_fallback", {"from": old, "to": new, "reason": why[:200]}))
        except LLMError as e:
            ui.info(f"LLM unavailable: heuristic diagnosis only, no agents.\n  {e}")
    m = new_manifest(run_id, run_dir, rtl, spec,
                     f"{client.name}/{client.model}" if client else "none")
    mpath = run_dir / "manifest.json"
    cases = test_cases()
    enabled = set(SCENARIOS.get(args.inject, {}).get("tests", [])) if args.inject else set()
    m["tests"] = [tc["id"] for tc in cases if not tc.get("optional") or tc["id"] in enabled]
    _log_knowledge(m, ui)
    if args.baseline:
        m["baseline"] = True
        log_event(m, "baseline_mode", {"note": "diagnose-and-route disabled"})
    if base:
        m["base_run"] = args.base_run
        # Carry the spec revision forward so new clarifications number after it.
        m["stages"]["spec"]["revision"] = base["stages"]["spec"]["revision"]
        log_event(m, "base_run", {"run": args.base_run,
                                  "spec_revision": base["stages"]["spec"]["revision"]})
        ui.info(f"Starting from run {args.base_run}: its RTL and its spec "
                f"(rev {base['stages']['spec']['revision']})")
    if args.inject:
        inject(args.inject, rtl, ui)
        log_event(m, "fault_injected", {"kind": args.inject})

    return run_loop(m, ui, client, run_dir, spec, rtl, "spec", args)


def resume(args) -> int:
    """The human route, closed. A run escalated to a person; the person
    supplies the fix as a diff; the same driver re-verifies it through every
    remaining stage. The agent's failed attempts stay in the record."""
    run_dir = settings.RUNS_DIR / args.resume
    mpath = run_dir / "manifest.json"
    if not mpath.exists():
        sys.exit(f"no run {args.resume}")
    m = json.loads(mpath.read_text())
    if m["current_stage"] != "failed":
        sys.exit(f"run {args.resume} is '{m['current_stage']}', not escalated — nothing to resume")
    if not args.human_fix:
        sys.exit("--resume needs --human-fix <diff>")
    rtl, spec = ROOT / m["working_rtl"], ROOT / m["stages"]["spec"]["spec_path"]
    ui = UI(args.auto_approve, args.web)
    ui.gate_dir = run_dir
    ui.stage("human fix", f"resuming {args.resume}")
    diff = args.human_fix.read_text()
    p = subprocess.run(["patch", "--dry-run", "-s", str(rtl)], input=diff,
                       capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"human fix does not apply to {rel(rtl)}:\n{p.stdout}{p.stderr}")
    before = rtl.read_text()
    subprocess.run(["patch", "-s", str(rtl)], input=diff, check=True, capture_output=True,
                   text=True)
    rs = m["stages"]["rtl"]
    pending = (m.get("pending") or {}).get("items") or [{}]
    target = pending[0].get("test", "")
    rs["open_pass"] = None
    rs["edits"].append({
        "test_id": target, "author": "human", "find": "", "replace": "",
        "why": f"human fix from {args.human_fix.name}", "applied": True, "error": "",
        "diff": "".join(__import__("difflib").unified_diff(
            before.splitlines(True), rtl.read_text().splitlines(True),
            f"a/{rtl.name}", f"b/{rtl.name}")),
        "n": len(rs["edits"]) + 1, "confirmed": False, "reverted": False})
    for line in rs["edits"][-1]["diff"].splitlines():
        if line[:1] in "+-" and line[:3] not in ("+++", "---"):
            ui.info(f"   {line}")
    # The 3-attempt cap bounds the AGENT loop. A person's fix is a new
    # intervention with its own budget; the reset is recorded, not silent.
    vs = m["stages"]["verify_rtl"]
    log_event(m, "human_fix", {"test": target, "file": str(args.human_fix),
                               "agent_attempts_used": vs["attempt_count"]})
    vs["attempt_count"] = 0
    m["pending"] = {"route_to": "rtl_agent", "items": pending if pending[0] else []}
    client = None
    if not args.no_llm:
        try:
            client = get_chat_client()
        except LLMError:
            pass
    return run_loop(m, ui, client, run_dir, spec, rtl, "verify_rtl", args)


def _log_knowledge(m: dict, ui: UI) -> None:
    try:
        meta = knowledge._get("/health", 1.5).get("meta") if knowledge.available() else None
    except Exception:  # noqa: BLE001
        meta = None
    if meta:
        ui.info(f"Knowledge base: {meta['n_chunks']} chunks ({meta.get('n_doc')} spec, "
                f"{meta.get('n_rtl')} RTL, {meta.get('n_pattern', 0)} confirmed fixes)")
        log_event(m, "knowledge", {"available": True, "n_chunks": meta["n_chunks"],
                                   "n_pattern": meta.get("n_pattern", 0),
                                   "built_at": meta.get("built_at")})
    else:
        ui.info("Knowledge base: retrieval service not running — agents use the "
                "spec section each test names")
        log_event(m, "knowledge", {"available": False})


def _run_stage(m: dict, ui: UI, client, run_dir: Path, spec: Path, rtl: Path,
               stage: str, args) -> str:
    if stage == "spec":
        return stage_spec(m, ui, client, spec, rtl)
    if stage == "rtl":
        return stage_rtl(m, ui, client, run_dir, spec, rtl)
    if stage == "verify_rtl":
        nxt = stage_verify(m, ui, client, run_dir, spec, rtl, "rtl", args.jobs)
        return "done" if nxt == "synth" and args.stop_after == "verify_rtl" else nxt
    if stage == "synth":
        return stage_synth(m, ui, run_dir, rtl)
    if stage == "verify_gate":
        nxt = stage_verify(m, ui, client, run_dir, spec, rtl, "gate", args.jobs)
        return "done" if nxt == "pnr" and args.stop_after == "verify_gate" else nxt
    if stage == "pnr":
        return stage_pnr(m, ui, run_dir, rtl)
    raise ValueError(f"unknown stage {stage}")


TERMINAL = ("done", "failed", "quota_stopped", "error", "stopped")


def _abnormal_stop(m: dict, ui: UI, stage: str, e: BaseException) -> str:
    """An unexpected exception or a stop request: record why and end the run
    cleanly, so the record never shows a stage that silently never finished."""
    import traceback
    if isinstance(e, KeyboardInterrupt):
        ui.info(f"Run stopped during '{stage}' (stop requested).")
        log_event(m, "stopped", {"stage": stage})
        return "stopped"
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    ui.info(f"Internal error during '{stage}': {type(e).__name__}: {e}\n{tb[-1500:]}")
    log_event(m, "internal_error", {"stage": stage, "error": f"{type(e).__name__}: {str(e)[:300]}"})
    return "error"


def stop_on_sigterm() -> None:
    """The app stops a run with SIGTERM; turn it into a recorded stop."""
    import signal

    def _raise(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _raise)


def _quota_stop(m: dict, ui: UI, stage: str, e: Exception) -> str:
    """The model's quota ran out with no fallback: stop without spending the
    attempt budget, so the run can be repeated later and is not reported as an
    escalation."""
    ui.info(f"Model quota used up during '{stage}'; stopping the run (no attempt spent).\n"
            f"    provider said: {str(e)[:300]}")
    log_event(m, "model_quota_exhausted", {"stage": stage, "reason": str(e)[:200]})
    return "quota_stopped"


def run_loop(m: dict, ui: UI, client, run_dir: Path, spec: Path, rtl: Path,
             stage: str, args) -> int:
    mpath = run_dir / "manifest.json"
    t_start = time.time() - m.get("wall_seconds", 0)
    guard = 0
    while stage not in TERMINAL and guard < 40:
        guard += 1
        m["current_stage"] = stage
        save_manifest(m, mpath)
        try:
            stage = _run_stage(m, ui, client, run_dir, spec, rtl, stage, args)
        except LLMQuotaError as e:
            stage = _quota_stop(m, ui, stage, e)
        except (Exception, KeyboardInterrupt) as e:  # noqa: BLE001 - recorded, run ends cleanly
            stage = _abnormal_stop(m, ui, stage, e)


    m["current_stage"] = stage
    m["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m["wall_seconds"] = round(time.time() - t_start, 1)
    final_diff = subprocess.run(["diff", "-u", str(UPSTREAM_RTL), str(rtl)],
                                capture_output=True, text=True).stdout
    if final_diff:
        (run_dir / "rtl_final.diff").write_text(final_diff)
        m["stages"]["rtl"]["diff_path"] = rel(run_dir / "rtl_final.diff")
    save_manifest(m, mpath)

    print(f"\n{'=' * 72}")
    if stage == "done":
        print(f"  RESULT: flow completed{' (stopped after ' + args.stop_after + ')' if args.stop_after else ''}"
              f" in {m['wall_seconds']:.0f}s")
    else:
        print(f"  RESULT: stopped at {m['history'][-1]['event'] if m['history'] else '?'} "
              + {"quota_stopped": "— model quota, run again later", "error": "— internal error (see above)",
                 "stopped": "— stopped on request"}.get(stage, "and escalated to a human"))
    for k in ("verify_rtl", "verify_gate"):
        vs = m["stages"][k]
        if vs["attempt_count"]:
            print(f"          {k}: {vs['attempt_count']}/{vs['max_attempts']} attempts")
    print(f"  Artifacts: {rel(run_dir)}\n{'=' * 72}")
    return 0 if stage == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
