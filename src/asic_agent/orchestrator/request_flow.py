"""
Change-request pipeline: a person states what they want, the agents check it
against the knowledge base, write a test, change the RTL, and prove it.

    retrieve -> compare -> spec decision -> test first -> change RTL -> verify RTL
             -> synthesis -> verify gate level -> layout & signoff -> write-back

Everything project-specific comes from project.json and the knowledge base:
the design and spec files, the testbench, and the passages retrieval returns.
Nothing here knows about apb_gpio, so a client's own design and documents
work the same way once project.json points at them.

Rules carried over from the verification pipeline (README: design rules):
  - every tool run waits for approval (ui.approve), cache hits included
  - the driver bounds every loop: 3 test-writing attempts, 3 change attempts
  - a change reaches the run's RTL only after it lints and passes the new test
    and the smoke set on a scratch copy; regressions roll it back
  - write-back only after the full regression and the new test pass

Started by `python -m asic_agent run --request-file <file>` (scripts/run_flow.sh, the app).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from .. import settings
from ..agents import analysis as analysis_agent
from ..agents import rtl as rtl_agent
from ..agents import spec as spec_agent
from ..agents import test_writer as test_agent
from ..eda.runner import discover
from ..knowledge import client as knowledge
from ..llm import LLMError, LLMQuotaError, get_chat_client
from . import driver as D
from .driver import ROOT, log_event, rel, save_manifest

MAX_TEST_ATTEMPTS = 3
MAX_CHANGE_ATTEMPTS = 3
TEMPERATURES = [0.0, 0.6, 0.9]


load_project = settings.load_project


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def gather(query: str, spec: int = 4, rtl: int = 4, patterns: int = 2) -> list[dict]:
    """A balanced set of passages: each kind fetched on its own, so one kind
    cannot crowd the others out of the prompt."""
    hits: list[dict] = []
    for kind, k in (("doc", spec), ("rtl", rtl), ("pattern", patterns)):
        if k:
            hits += knowledge.retrieve(query, k=k, kinds=(kind,))
    return hits


def rtl_window(rtl_text: str, target_hits: list[dict], fallback_terms: str) -> str:
    """The parts of THIS run's RTL the knowledge base pointed at, found by
    content (rtl_agent.locate), merged, with the declarations they use."""
    lines = rtl_text.splitlines()
    ranges = sorted(r for r in (rtl_agent.locate(h["text"], rtl_text) for h in target_hits) if r)
    merged: list[list[int]] = []
    for lo, hi in ranges:
        if merged and lo <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    if not merged:
        return D._declarations(rtl_text, "") + D.rtl_excerpt(
            rtl_text, D._salient_terms(fallback_terms), window=40, numbered=False)
    body = "\n// ...\n".join("\n".join(lines[lo:hi]) for lo, hi in merged[:3])
    return all_declarations(rtl_text) + body


def all_declarations(rtl_text: str, limit: int = 70) -> str:
    """Every signal the module declares. A change may need signals defined far
    from the excerpt; without this list the agent invents names (2 Oct:
    's_gpio_in' for the real 'r_gpio_in', nine candidates in a row)."""
    decls = [l.strip() for l in rtl_text.splitlines()
             if re.match(r"\s*(logic|wire|reg)\b", l) and ";" in l]
    if not decls:
        return ""
    return ("// signals declared in this module (use only these, or declare a new one)\n"
            + "\n".join(decls[:limit]) + "\n// ...\n")


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

class Run:
    def __init__(self, args, ui: D.UI):
        self.args, self.ui = args, ui
        self.project = load_project()
        root = self.project["_root"]
        self.request = args.request_file.read_text().strip()
        self.run_id = args.run_id or datetime.now().strftime("req-%Y%m%d-%H%M%S")
        self.dir = settings.RUNS_DIR / self.run_id
        if self.dir.exists():
            sys.exit(f"{rel(self.dir)} already exists — pick another --run-id")
        design, tbcfg = self.project["design"], self.project["testbench"]
        src_rtl, src_spec, src_tb = root / design["rtl"], root / design["spec"], root / tbcfg["file"]
        base = None
        if args.base_run:
            base = json.loads((settings.RUNS_DIR / args.base_run / "manifest.json").read_text())
            if base["current_stage"] != "done":
                sys.exit(f"--base-run {args.base_run} did not complete")
            src_rtl = ROOT / base["working_rtl"]
            src_spec = ROOT / base["stages"]["spec"]["spec_path"]
            if base.get("tb_path"):
                src_tb = ROOT / base["tb_path"]
        self.rtl = self.dir / "rtl" / src_rtl.name
        self.spec = self.dir / "spec" / src_spec.name
        self.tb = self.dir / "tb" / src_tb.name
        for f, s in ((self.rtl, src_rtl), (self.spec, src_spec), (self.tb, src_tb)):
            f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(s, f)
        ui.gate_dir = self.dir
        self.client = None
        try:
            self.client = get_chat_client()
            ui.info(f"LLM: {self.client.name} / {self.client.model}")
            if hasattr(self.client, "on_switch"):
                self.client.on_switch = self._model_switched
        except LLMError as e:
            ui.info(f"No model available: {e}")
        m = D.new_manifest(self.run_id, self.dir, self.rtl, self.spec,
                           f"{self.client.name}/{self.client.model}" if self.client else "none")
        m["kind"] = "request"
        m["request"] = self.request
        m["project"] = self.project.get("name")
        m["tb_path"] = rel(self.tb)
        excluded = set(tbcfg.get("regression_exclude", []))
        m["regression"] = [t for t in discover(self.tb) if t not in excluded]
        m["tests"] = list(m["regression"])
        m["stages"]["analysis"] = {}
        m["stages"]["test"] = {"attempts": 0, "name": None, "code": None}
        if base:
            m["base_run"] = args.base_run
            m["stages"]["spec"]["revision"] = base["stages"]["spec"]["revision"]
            log_event(m, "base_run", {"run": args.base_run,
                                      "spec_revision": base["stages"]["spec"]["revision"]})
        self.m = m
        self.mpath = self.dir / "manifest.json"
        self.hits: list[dict] = []
        D._log_knowledge(m, ui)
        log_event(m, "request_received", {"request": self.request[:500]})
        self.save()

    def _model_switched(self, old: str, new: str, why: str) -> None:
        self.ui.info(f"Model quota for {old} used up — continuing on {new}\n    provider said: {why[:300]}")
        log_event(self.m, "model_fallback", {"from": old, "to": new, "reason": why[:200]})
        self.m["llm"] = f"{self.m['llm']} -> {new}"

    def save(self):
        save_manifest(self.m, self.mpath)

    # -- 1. retrieve and compare ---------------------------------------------
    def analyse(self) -> str:
        self.ui.stage("retrieve & compare", "knowledge base")
        self.ui.info(f"Request: {self.request}")
        if self.client is None:
            self.ui.info("The request pipeline needs a model. Escalating.")
            log_event(self.m, "no_model")
            return "failed"
        self.hits = gather(self.request)
        if not self.hits:
            self.ui.info("Knowledge base unavailable or returned nothing — cannot ground the request.")
            log_event(self.m, "no_evidence")
            return "failed"
        for h in self.hits:
            self.ui.info(f"  [{h['kind']:7}] {h['score']:.2f}  {h['label'][:70]}")
        try:
            a = analysis_agent.analyse(self.client, self.request, self.hits)
        except LLMError as e:
            self.ui.info(f"Analysis failed: {str(e)[:200]}")
            log_event(self.m, "analysis_failed", {"error": str(e)[:300]})
            return "failed"
        self.analysis = a
        self.m["stages"]["analysis"] = a.to_dict()
        log_event(self.m, "analysed", {"verdict": a.verdict, "method": a.method,
                                       "summary": a.summary, "targets": a.rtl_targets})
        self.ui.info(f"Verdict: {a.verdict} ({a.method})  {a.note}")
        if a.spec_quote:
            self.ui.info(f'  spec: "{a.spec_quote[:160]}"')
        self.ui.info(f"Change needed: {a.summary}")
        self.ui.info(f"Acceptance: {a.acceptance}")
        return "spec"

    # -- 2. spec decision ----------------------------------------------------
    def spec_stage(self) -> str:
        a, ss = self.analysis, self.m["stages"]["spec"]
        self.ui.stage("spec", a.verdict)
        if a.verdict == "agrees":
            ss["approved"] = True
            log_event(self.m, "spec_agrees", {"quote": a.spec_quote[:300]})
            self.ui.info("The specification already requires this; the design should change to meet it.")
            return "test"
        addition = a.spec_addition or a.summary
        choice = self.ui.choose(
            "The specification " + ("does not cover this request" if a.verdict == "extends"
                                    else "says something different") + ". Update it?",
            {"a": f"[update the specification] {addition}",
             "b": "[reject the request] leave the specification and the design unchanged"},
            "a", context={"test": "change request", "ambiguous": a.spec_quote or "(not covered by the specification)",
                          "why": a.summary,
                          "consequence": {"a": "rtl_must_change", "b": "request_rejected"}})
        if choice != "a":
            log_event(self.m, "request_rejected", {"verdict": a.verdict})
            self.ui.info("Request rejected; nothing changed.")
            return "failed"
        ss["revision"] += 1
        heading = next((h["label"].split(">")[-1].strip() for h in self.hits if h["kind"] == "doc"), "")
        res = spec_agent.SpecResolution(
            "change request", a.spec_quote or "(not covered by the specification)",
            addition, "leave the specification unchanged", "a", a.summary, addition,
            "rtl_must_change", chosen="a")
        self.spec.write_text(spec_agent.apply_clarification(self.spec.read_text(), heading,
                                                            ss["revision"], res))
        ss["clarifications"].append({**res.to_dict(), "revision": ss["revision"],
                                     "evidence": [e for e in a.evidence if e["kind"] == "doc"]})
        ss["approved"] = True
        log_event(self.m, "spec_clarified", {"test": "change request", "revision": ss["revision"],
                                             "chosen": "a", "consequence": "rtl_must_change"})
        self.ui.info(f"Specification updated (rev {ss['revision']}): {addition[:200]}")
        return "test"

    # -- 3. test first -------------------------------------------------------
    def test_stage(self) -> str:
        a, ts, tbcfg = self.analysis, self.m["stages"]["test"], self.project["testbench"]
        self.ui.stage("test", "written before the RTL is changed")
        q = f"{a.summary}. {a.acceptance}"
        spec_hits = knowledge.retrieve(q, k=4, kinds=("doc",))
        tb_hits = knowledge.retrieve(q + " " + " ".join(tbcfg.get("helper_tasks", [])),
                                     k=4, kinds=("testbench",))
        clar = self.m["stages"]["spec"]["clarifications"]
        if clar:
            spec_hits = [{"kind": "doc", "label": "Specification update for this request",
                          "text": clar[-1]["clarification"], "score": 1.0, "keyword_score": 1.0,
                          "chunk_id": "request-clarification", "location": rel(self.spec)}] + spec_hits
        original = self.tb.read_text()
        feedback = ""
        for attempt in range(1, MAX_TEST_ATTEMPTS + 1):
            ts["attempts"] = attempt
            try:
                gt = test_agent.write_test(self.client, a.summary, a.acceptance, spec_hits, tb_hits,
                                           tbcfg.get("helper_tasks", []), tbcfg.get("signals_note", ""),
                                           discover(self.tb), feedback,
                                           temperature=TEMPERATURES[min(attempt - 1, 2)])
            except LLMError as e:
                feedback = str(e)[:300]
                self.ui.info(f"  attempt {attempt}: no usable test ({feedback})")
                log_event(self.m, "test_rejected", {"attempt": attempt, "reason": feedback})
                continue
            self.tb.write_text(test_agent.insert(original, gt))
            self.ui.info(f"  attempt {attempt}: wrote test '{gt.name}' — {gt.why[:140]}")
            if not self.ui.approve(f"verilator build + run new test {gt.name} on the unchanged RTL"):
                self.tb.write_text(original)
                log_event(self.m, "approval_declined", {"stage": "test"})
                return "failed"
            res = D.run_sim(self.rtl, self.dir / f"test{attempt}" / "sim", None, False, None,
                            [gt.name], self.tb)
            if not res.get("build_ok"):
                log = (self.dir / f"test{attempt}" / "sim" / "build.log")
                lines = (log.read_text() if log.exists() else "").splitlines()
                msgs = []
                for i, l in enumerate(lines):
                    if not l.startswith(("%Error", "%Warning")) or "Exiting due to" in l:
                        continue
                    msg = re.sub(r"^%(Error|Warning)[-A-Z]*: .*?:\d+:\d+: ", r"%\1: ", l)
                    # Verilator prints the offending source line next ("  206 | ..."):
                    # the message alone ("unexpected IDENTIFIER") did not tell the
                    # model what to change, and it repeated the mistake 3 times.
                    src = next((x.split("|", 1)[1].strip() for x in lines[i + 1:i + 3]
                                if re.match(r"^\s*\d+\s*\|", x)), "")
                    msgs.append(f"{msg} at `{src}`" if src else msg)
                # Warnings are errors in this testbench; the agent needs the messages.
                feedback = ("it does not compile (warnings are errors here): "
                            + "; ".join(dict.fromkeys(msgs))[:600] if msgs else "it does not compile")
            else:
                r = res["results"][0] if res["results"] else {"status": "FAIL", "detail": "no result"}
                if r["status"] == "PASS":
                    feedback = ("it PASSES on the unchanged design, so it cannot detect the requested "
                                "change. Check the behaviour that must be different after the change.")
                else:
                    reason = "; ".join(r.get("failed_checks") or []) or r["detail"]
                    ts.update(name=gt.name, code=gt.task, why=gt.why, initial_failure=reason,
                              evidence=gt.evidence)
                    self.m["tests"] = self.m["regression"] + [gt.name]
                    self.failure = reason
                    log_event(self.m, "test_written", {"name": gt.name, "attempt": attempt,
                                                       "fails_before": reason[:200]})
                    self.ui.info(f"  fails on the unchanged RTL as it should: {reason[:160]}")
                    return "rtl"
            self.ui.info(f"  rejected: {feedback[:200]}")
            log_event(self.m, "test_rejected", {"attempt": attempt, "reason": feedback[:300]})
            self.tb.write_text(original)
        last = self.m["history"][-1].get("reason", "")
        if "PASSES" in last:
            log_event(self.m, "already_satisfied")
            self.ui.info("Every test written passes on the current design: it may already meet "
                         "the request. A person should confirm.")
        return "failed"

    # -- 4. change the RTL ---------------------------------------------------
    def change_stage(self) -> str:
        a, ts = self.analysis, self.m["stages"]["test"]
        rs, vs = self.m["stages"]["rtl"], self.m["stages"]["verify_rtl"]
        attempt = vs["attempt_count"] + 1
        self.ui.stage("rtl", f"change attempt {attempt} of {MAX_CHANGE_ATTEMPTS}")
        rtl_text = self.rtl.read_text()
        target_hits = [h for h in self.hits if h["chunk_id"] in set(a.rtl_targets)] or \
            [h for h in self.hits if h["kind"] == "rtl"][:2]
        excerpt = rtl_window(rtl_text, target_hits, a.summary + " " + a.acceptance)
        spec_text = "\n\n".join(h["text"] for h in self.hits if h["kind"] == "doc")
        clar = self.m["stages"]["spec"]["clarifications"]
        if clar:
            spec_text = f"Specification update for this request: {clar[-1]['clarification']}\n\n" + spec_text
        patterns = knowledge.retrieve(f"{a.summary}. {a.acceptance}. {self.failure}", k=2,
                                      kinds=("pattern",))
        tried = [f"<find>\n{e['find']}\n</find>\n<replace>\n{e['replace']}\n</replace>"
                 + (f"\n(rejected: {e['error']})" if e.get("error") else "")
                 + (f"\n(tested: {e['screen']})" if e.get("screen") and e["screen"] != "pass" else "")
                 for e in rs["edits"] if not e.get("applied") or e.get("reverted")]
        screen_tests = sorted(set([ts["name"]] + self.project["testbench"].get("smoke", []))
                              & set(self.m["tests"]))
        if not self.ui.approve(f"screen up to {len(TEMPERATURES)} candidate RTL changes "
                               f"(verilator lint + {len(screen_tests)} tests each)"):
            log_event(self.m, "approval_declined", {"stage": "rtl"})
            return "failed"
        meta = {"id": ts["name"], "pass_condition": a.acceptance}
        for k, temp in enumerate(TEMPERATURES):
            t0 = time.time()
            try:
                edit = rtl_agent.propose(self.client, meta, self.failure, spec_text, excerpt,
                                         f"change request: {a.summary}", tried,
                                         "The new test your change must make pass (do not edit it; "
                                         "read how it drives the design):\n" + (ts.get("code") or ""),
                                         temperature=temp, evidence=patterns,
                                         max_pairs=rtl_agent.FEATURE_MAX_PAIRS)
            except LLMError as e:
                self.ui.info(f"  candidate {k + 1}: no usable change ({str(e)[:120]})")
                log_event(self.m, "rtl_agent_failed", {"test": ts["name"], "error": str(e)[:300]})
                continue
            new_text = rtl_agent.apply(rtl_text, edit, self.rtl.name,
                                       rtl_agent.FEATURE_LIMITS if a.verdict != "agrees"
                                       else (rtl_agent.MAX_FIND_LINES, rtl_agent.MAX_REPLACE_LINES))
            n = len(rs["edits"]) + 1
            rec = {**edit.to_dict(), "test_id": ts["name"], "n": n, "candidate": k + 1,
                   "temperature": temp, "seconds": round(time.time() - t0, 1),
                   "confirmed": False, "reverted": False, "screen": None,
                   "spec_ref": next((h["label"] for h in self.hits if h["kind"] == "doc"), ""),
                   "pass_condition": a.acceptance, "symptom": self.request[:300]}
            rs["edits"].append(rec)
            change = " | ".join(l for l in edit.diff.splitlines()
                                if l[:1] in "+-" and l[:3] not in ("+++", "---"))[:160]
            if not edit.applied:
                self.ui.info(f"  candidate {k + 1}: rejected — {edit.error}")
                log_event(self.m, "rtl_edit", {"test": ts["name"], "applied": False,
                                               "error": edit.error, "candidate": k + 1})
                tried.append(f"<find>\n{edit.find}\n</find>\n(rejected: {edit.error})")
                continue
            verdict = D._screen(self.dir, f"c{n}", new_text, screen_tests, self.tb)
            rec["screen"], rec["applied"] = verdict, False
            self.ui.info(f"  candidate {k + 1} (T={temp}): {change}")
            self.ui.info(f"     screen: {verdict}")
            log_event(self.m, "rtl_edit", {"test": ts["name"], "applied": False,
                                           "candidate": k + 1, "screen": verdict})
            if verdict == "pass":
                shutil.copy(self.rtl, self.dir / "rtl" / f"before_change{n}.sv")
                self.rtl.write_text(new_text)
                rec["applied"] = True
                (self.dir / f"rtl_change{n}.diff").write_text(edit.diff)
                rec["diff_path"] = rel(self.dir / f"rtl_change{n}.diff")
                log_event(self.m, "rtl_edit", {"test": ts["name"], "applied": True,
                                               "candidate": k + 1})
                self.ui.info(f"  applied candidate {k + 1}: {edit.why[:160]}")
                return "verify_rtl"
            tried.append(f"<find>\n{edit.find}\n</find>\n<replace>\n{edit.replace}\n</replace>"
                         f"\n(tested: {verdict})")
        vs["attempt_count"] = attempt
        log_event(self.m, "verify_rtl_failed", {"attempt": attempt, "failing": [ts["name"]],
                                                "route_to": "rtl_agent", "diagnoses": [],
                                                "note": "no candidate change passed screening"})
        # Diagnose before retrying: if compiling candidates all fail the SAME
        # check (of the new test, or of an existing test the approved change
        # supersedes), ask whether the check is what is wrong.
        nxt = self.review_test(rtl_text)
        if nxt:
            return nxt
        if attempt >= MAX_CHANGE_ATTEMPTS:
            log_event(self.m, "attempt_cap_reached", {"stage": "verify_rtl", "route_to": "rtl_agent"})
            self.ui.info("Three change attempts used. Handing to a person.")
            return "failed"
        return "rtl"

    def _persistent_checks(self) -> list[tuple[str, str]]:
        """(test, check) pairs that every compiling candidate failed, when at
        least three compiled. Parsed from the screening summaries, which read
        "test: first failing check; test2: ...". """
        screens = [e["screen"] for e in self.m["stages"]["rtl"]["edits"]
                   if e.get("screen") and e["screen"] != "pass"
                   and not e["screen"].startswith(("lint", "build"))]
        if len(screens) < 3:
            return []
        parsed = [set(re.findall(r"(?:^|; )(\w+): (.*?)(?=; \w+: |$)", s)) for s in screens]
        return sorted(set.intersection(*parsed))

    def _spec_changed(self) -> str | None:
        """The approved spec change of this run, if a person approved one."""
        if any(h["event"] == "spec_clarified" for h in self.m["history"]):
            return self.analysis.spec_addition or self.analysis.summary
        return None

    def review_test(self, rtl_text: str) -> str | None:
        """Diagnose the tests before spending another attempt: a check that
        every candidate fails may be what is wrong. Two cases:
          - the NEW test checks something the spec does not require;
          - an EXISTING test checks behaviour the approved change replaces
            (e.g. "CTRL keeps only bits[1:0]" when the request adds bit 2).
        A person confirms each revision; the attempt budget gets one back."""
        ts = self.m["stages"]["test"]
        done = ts.setdefault("reviewed_checks", [])
        pairs = [(t, c) for t, c in self._persistent_checks() if f"{t}: {c}" not in done]
        if not pairs:
            return None
        revised = False
        for test, check in pairs:
            done.append(f"{test}: {check}")
            if test == ts["name"]:
                nxt = self._review_new_test(check)
            elif test in self.m.get("regression", []):
                nxt = self._review_existing_test(test, check)
            else:
                nxt = None
            if nxt == "failed":
                return "failed"
            revised |= nxt == "rtl"
        if not revised:
            return None
        self.m["stages"]["verify_rtl"]["attempt_count"] = max(
            0, self.m["stages"]["verify_rtl"]["attempt_count"] - 1)
        self.ui.info("Back to the RTL change (one attempt returned to the budget).")
        return "rtl"

    def _review_existing_test(self, test: str, check: str) -> str | None:
        change = self._spec_changed()
        if not change:
            return None        # spec unchanged: an existing test is the specification
        self.ui.stage("test review", f"every candidate fails existing test {test}")
        self.ui.info(f"Check: {check}")
        tb_text = self.tb.read_text()
        code = test_agent.task_code(tb_text, test)
        if not code:
            return None
        try:
            r = test_agent.review_existing(self.client, code, check, change, self.request)
        except LLMError as e:
            self.ui.info(f"Review failed: {str(e)[:160]}")
            return None
        log_event(self.m, "test_reviewed", {"test": test, "existing": True, "check": check[:200],
                                            "obsolete": r["obsolete"], "reason": r["reason"][:400]})
        self.ui.info(f"Made obsolete by the approved change: {r['obsolete']} — {r['reason'][:240]}")
        if not r["obsolete"]:
            return None
        choice = self.ui.choose(
            f"Existing test {test} checks behaviour the approved change replaces. Update it?",
            {"a": f"[remove this check from {test}] {r['reason']}",
             "b": "[keep the test] the old behaviour must stay; keep trying to change the RTL"},
            "a", context={"test": test, "ambiguous": check, "why": r["reason"],
                          "consequence": {"a": "existing_test_updated", "b": "rtl_must_change"}})
        if choice != "a":
            return None
        new_code = test_agent.drop_check(code, check)
        if new_code is None:
            self.ui.info("Could not isolate that check in the test; keeping it.")
            return None
        self.tb.write_text(tb_text.replace(code, new_code, 1))
        log_event(self.m, "existing_test_updated", {"test": test, "check_removed": check[:200],
                                                    "reason": r["reason"][:300]})
        self.ui.info(f"Removed the obsolete check from existing test {test} (this run's testbench copy).")
        return "rtl"

    def _review_new_test(self, check: str) -> str | None:
        ts = self.m["stages"]["test"]
        self.ui.stage("test review", "every candidate fails the same check of the new test")
        self.ui.info(f"Check: {check}")
        spec_hits = knowledge.retrieve(f"{check}. {self.analysis.acceptance}", k=4, kinds=("doc",))
        try:
            r = test_agent.review(self.client, ts, check, spec_hits, self.request)
        except LLMError as e:
            self.ui.info(f"Review failed: {str(e)[:160]}")
            return None
        log_event(self.m, "test_reviewed", {"check": check[:200], "required": r["required"],
                                            "reason": r["reason"][:400]})
        self.ui.info(f"Required by the spec: {r['required']} — {r['reason'][:240]}")
        if r["required"]:
            return None
        choice = self.ui.choose(
            "The new test checks something the specification contradicts. Revise the test?",
            {"a": f"[revise the test] {r['reason']}",
             "b": "[keep the test] the check is right; keep trying to change the RTL"},
            "a", context={"test": ts["name"], "ambiguous": check, "why": r["reason"],
                          "consequence": {"a": "test_revised", "b": "rtl_must_change"}})
        if choice != "a":
            return None
        # Rewrite through the normal test-writing path (its format checks are
        # proven), told exactly which check contradicts the spec and why.
        tbcfg = self.project["testbench"]
        old_tb = self.tb.read_text()
        base_tb = test_agent.remove(old_tb, ts["name"], ts["code"])
        q = f"{self.analysis.summary}. {self.analysis.acceptance}"
        gt = None
        for k in range(2):
            try:
                gt = test_agent.write_test(
                    self.client, self.analysis.summary, self.analysis.acceptance,
                    knowledge.retrieve(q, k=4, kinds=("doc",)),
                    knowledge.retrieve(q, k=4, kinds=("testbench",)),
                    tbcfg.get("helper_tasks", []), tbcfg.get("signals_note", ""),
                    [n for n in discover(self.tb) if n != ts["name"]],
                    f"In your test, the check '{check}' contradicts the specification: "
                    f"{r['reason']} Keep every other check; correct or remove this one.",
                    temperature=TEMPERATURES[k])
                break
            except LLMError as e:
                self.ui.info(f"  revision attempt {k + 1}: {str(e)[:160]}")
        if gt is None:
            # The model could not rewrite it: remove just the check the person
            # agreed is not required, and keep the rest of the test as written.
            task = test_agent.drop_check(ts["code"], check)
            if task is None:
                self.ui.info("Could not isolate that check in the test; keeping the original.")
                return None
            gt = test_agent.GeneratedTest(name=ts["name"], task=task, why=ts.get("why", ""))
            self.ui.info("  revision: removed only the check that is not required by the spec")
        self.tb.write_text(test_agent.insert(base_tb, gt))
        if not self.ui.approve(f"verilator build + run revised test {gt.name} on the unchanged RTL"):
            return "failed"
        res = D.run_sim(self.rtl, self.dir / "test_revised" / "sim", None, False, None,
                        [gt.name], self.tb)
        rr = (res.get("results") or [{"status": "FAIL", "detail": "did not build"}])[0]
        if not res.get("build_ok") or rr["status"] == "PASS":
            self.ui.info("Revised test rejected (does not build, or no longer fails); keeping the original.")
            self.tb.write_text(old_tb)
            return None
        self.m["tests"] = self.m["regression"] + [gt.name]
        ts.update(name=gt.name, why=gt.why)
        r["revised"] = gt.task
        ts.update(code=r["revised"], revised_reason=r["reason"],
                  initial_failure="; ".join(rr.get("failed_checks") or []) or rr["detail"])
        self.failure = ts["initial_failure"]
        log_event(self.m, "test_revised", {"name": ts["name"], "reason": r["reason"][:300]})
        self.ui.info("Test revised; it still fails on the unchanged RTL.")
        return "rtl"

    # -- 5. verify the whole design ------------------------------------------
    def verify_stage(self) -> str:
        vs, ts = self.m["stages"]["verify_rtl"], self.m["stages"]["test"]
        attempt = vs["attempt_count"] + 1
        self.ui.stage("verify_rtl", f"attempt {attempt}: new test + full regression")
        if not self.ui.approve(f"verilator --lint-only -Wall {self.rtl.name}"):
            log_event(self.m, "approval_declined", {"stage": "verify_rtl"})
            return "failed"
        p = subprocess.run(["bash", str(D.LINT_SH), str(self.rtl)], capture_output=True, text=True)
        vs["lint_passed"] = p.returncode == 0
        tests = self.m["tests"]
        if vs["lint_passed"] and not self.ui.approve(
                f"verilator build + simulate {self.rtl.name} ({len(tests)} tests: the new one "
                f"and the regression)"):
            log_event(self.m, "approval_declined", {"stage": "verify_rtl"})
            return "failed"
        vs["attempt_count"] = attempt
        res = D.run_sim(self.rtl, self.dir / f"verify_rtl{attempt}" / "sim", None, False, None,
                        tests, self.tb) if vs["lint_passed"] else {"build_ok": False, "results": []}
        vs["build_ok"] = res.get("build_ok", False)
        results = res.get("results", [])
        vs["test_cases"] = [{"id": r["test"], "status": r["status"].lower(), "reason": r["detail"]}
                            for r in results]
        failing = [r["test"] for r in results if r["status"] != "PASS"]
        if vs["lint_passed"] and vs["build_ok"] and results and not failing:
            self.ui.info(f"{len(results)}/{len(results)} passed — the change does what was asked "
                         f"and breaks nothing")
            log_event(self.m, "verify_rtl_passed", {"attempt": attempt})
            return "writeback"
        why = ("lint failed" if not vs["lint_passed"] else "testbench did not build"
               if not vs["build_ok"] else f"failing: {', '.join(failing)}")
        self.ui.info(f"Verification failed ({why}); rolling the change back.")
        last = max((e for e in self.m["stages"]["rtl"]["edits"] if e.get("applied")),
                   key=lambda e: e["n"], default=None)
        if last:
            before = self.dir / "rtl" / f"before_change{last['n']}.sv"
            if before.exists():
                shutil.copy(before, self.rtl)
            last["reverted"], last["revert_reason"] = True, why
            log_event(self.m, "rtl_edit_reverted", {"reason": why})
        self.failure = f"{self.failure} (a previous change was rolled back: {why})"
        log_event(self.m, "verify_rtl_failed", {"attempt": attempt, "failing": failing,
                                                "route_to": "rtl_agent", "diagnoses": [], "note": why})
        if attempt >= MAX_CHANGE_ATTEMPTS:
            log_event(self.m, "attempt_cap_reached", {"stage": "verify_rtl", "route_to": "rtl_agent"})
            return "failed"
        return "rtl"

    # -- 6. write-back -------------------------------------------------------
    def writeback(self) -> str:
        ts = self.m["stages"]["test"]
        e = max((x for x in self.m["stages"]["rtl"]["edits"] if x.get("applied") and not x.get("reverted")),
                key=lambda x: x["n"], default=None)
        if e:
            e["confirmed"] = True
            D.KNOWLEDGE.parent.mkdir(parents=True, exist_ok=True)
            clar = self.m["stages"]["spec"]["clarifications"]
            with D.KNOWLEDGE.open("a") as f:
                f.write(json.dumps({
                    "tag": "known_failure_pattern", "block": self.m["target_block"],
                    "run_id": self.run_id, "test": ts["name"], "root_cause": "change_request",
                    "request": self.request, "spec_clarification": clar[-1]["clarification"] if clar else None,
                    "fix_summary": self.analysis.summary, "diff": e["diff"], "fixed_by": "rtl_agent",
                    "spec_ref": e.get("spec_ref", ""), "pass_condition": self.analysis.acceptance,
                    "symptom": self.request[:300], "test_code": ts.get("code"),
                    "confirmed_at_level": "rtl",
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
            log_event(self.m, "writeback", {"tests": [ts["name"]]})
            meta = knowledge.reindex()
            if meta:
                log_event(self.m, "knowledge_reindexed", {"n_chunks": meta["n_chunks"],
                                                          "n_pattern": meta["n_pattern"]})
                self.ui.info(f"Knowledge base rebuilt with this change ({meta['n_pattern']} confirmed changes)")
        return "synth"


def _step(r: "Run", m: dict, ui: D.UI, stage: str) -> str:
    if stage == "analyse":
        return r.analyse()
    if stage == "spec":
        return r.spec_stage()
    if stage == "test":
        return r.test_stage()
    if stage == "rtl":
        return r.change_stage()
    if stage == "verify_rtl":
        return r.verify_stage()
    if stage == "writeback":
        return r.writeback()
    if stage == "synth":
        return D.stage_synth(m, ui, r.dir, r.rtl)
    if stage == "verify_gate":
        nxt = D.stage_verify(m, ui, r.client, r.dir, r.spec, r.rtl, "gate", None)
        return "failed" if nxt == "rtl" else nxt   # gate-level failure: a person looks at it
    if stage == "pnr":
        return D.stage_pnr(m, ui, r.dir, r.rtl)
    raise ValueError(f"unknown stage {stage}")


def run(args, ui: D.UI) -> int:
    r = Run(args, ui)
    m = r.m
    t0 = time.time()
    stage, guard = "analyse", 0
    while stage not in D.TERMINAL and guard < 40:
        guard += 1
        m["current_stage"] = {"analyse": "spec", "writeback": "verify_rtl"}.get(stage, stage)
        r.save()
        try:
            stage = _step(r, m, ui, stage)
        except LLMQuotaError as e:
            stage = D._quota_stop(m, ui, stage, e)
        except (Exception, KeyboardInterrupt) as e:  # noqa: BLE001 - recorded, run ends cleanly
            stage = D._abnormal_stop(m, ui, stage, e)
    m["current_stage"] = stage
    m["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m["wall_seconds"] = round(time.time() - t0, 1)
    diff = subprocess.run(["diff", "-u", str(r.project["_root"] / r.project["design"]["rtl"]), str(r.rtl)],
                          capture_output=True, text=True).stdout
    if diff:
        (r.dir / "rtl_final.diff").write_text(diff)
        m["stages"]["rtl"]["diff_path"] = rel(r.dir / "rtl_final.diff")
    r.save()
    print(f"\n{'=' * 72}\n  RESULT: " + (f"request implemented and signed off in {m['wall_seconds']:.0f}s"
          if stage == "done" else f"stopped at {m['history'][-1]['event']} — "
          + {"quota_stopped": "model quota, run again later", "error": "internal error (see above)",
             "stopped": "stopped on request"}.get(stage, "a person should look"))
          + f"\n  Artifacts: {rel(r.dir)}\n{'=' * 72}")
    return 0 if stage == "done" else 1
