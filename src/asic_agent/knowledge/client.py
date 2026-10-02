"""
The knowledge layer as the agents see it: retrieval and write-back.

Retrieval goes to the knowledge service (knowledge/service.py, a small HTTP
service on 127.0.0.1 that keeps the index loaded). If the service is not running, retrieval returns nothing and the
agents work from the spec section the test names, as before — the pipeline
never fails because the knowledge layer is down; the run record says so.

Write-back exports CONFIRMED, non-retracted fixes from
data/knowledge/known_failure_patterns.jsonl as one Markdown document each in
data/knowledge/patterns/, which the index ingests with kind "pattern". The
JSONL stays the record; the patterns directory is regenerated from it, so a
retraction removes the pattern from retrieval at the next rebuild.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .. import settings

KB = settings.KNOWLEDGE_RECORD
PATTERNS = settings.PATTERNS_DIR
SERVICE = os.environ.get("RAG_URL", f"http://127.0.0.1:{os.environ.get('RAG_PORT', '8090')}")

_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_SV_WORDS = {"assign", "begin", "end", "if", "else", "for", "int", "logic", "always_ff",
             "always_comb", "posedge", "negedge", "i", "or", "and"}


def _test_meta() -> dict:
    """Per-test metadata (spec_ref, pass_condition) named in project.json, if any."""
    rel = settings.load_project().get("testbench", {}).get("metadata")
    f = settings.project_path(rel) if rel else None
    return {t["id"]: t for t in json.loads(f.read_text())["test_cases"]} if f and f.exists() else {}


# Evidence below this combined score is not shown to a model: a vector search
# always returns its nearest neighbours, relevant or not (README: design rules).
MIN_SCORE = 0.35


def export_patterns() -> int:
    """Regenerate runs/knowledge/patterns/ from the confirmed write-back record."""
    PATTERNS.mkdir(parents=True, exist_ok=True)
    for old in PATTERNS.glob("*.md"):
        old.unlink()
    if not KB.exists():
        return 0
    # Past fixes belong to the design they were made on: a client's block
    # must not retrieve another design's fixes as if they were its own.
    try:
        top = settings.load_project()["design"]["top"]
    except (OSError, ValueError, KeyError):
        top = None
    n, seen = 0, set()
    for line in KB.read_text().splitlines():
        e = json.loads(line)
        if e.get("retracted") or e.get("tag") != "known_failure_pattern":
            continue
        if top and e.get("block", "apb_gpio") != top:
            continue
        # The same fix confirmed by several runs is one pattern, not several
        # near-identical chunks crowding out everything else.
        sig = (e["test"], tuple(l for l in (e.get("diff") or "").splitlines()
                                if l[:1] in "+-" and l[:3] not in ("+++", "---")))
        if sig in seen:
            continue
        seen.add(sig)
        meta = _test_meta().get(e["test"], {})
        spec_ref = e.get("spec_ref") or meta.get("spec_ref", "")
        requirement = e.get("pass_condition") or meta.get("pass_condition", "")
        added = [l[1:].strip() for l in (e.get("diff") or "").splitlines()
                 if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:].strip() for l in (e.get("diff") or "").splitlines()
                   if l.startswith("-") and not l.startswith("---")]
        signals = sorted(set(_IDENT.findall(" ".join(removed + added))) - _SV_WORDS)
        # The fix itself comes first: evidence passages are truncated for the
        # prompt, and on 2 Oct a longer header pushed the fix lines past the
        # cut — the 7B agent then saw the pattern but not the fix.
        body = [
            f"# Known failure pattern: {e['test']} ({e['root_cause']})",
            "",
            "Lines removed:", *[f"    {l}" for l in removed],
            "Lines added:", *[f"    {l}" for l in added],
            "",
            f"Why: {e.get('fix_summary', '')}",
            # Similar bugs share a spec section and signals more than wording.
            f"Spec section: {spec_ref}. Requirement tested: {requirement}",
        ]
        if e.get("symptom"):
            body.append(f"Symptom: {e['symptom']}")
        if e.get("spec_clarification"):
            body.append(f"Spec clarification that preceded the fix: {e['spec_clarification']}")
        body += [f"Signals changed: {', '.join(signals)}",
                 f"Confirmed in run {e['run_id']} by the full test suite "
                 f"({e.get('confirmed_at_level', 'rtl')} level), fixed by {e.get('fixed_by', 'rtl_agent')}.", ""]
        name = f"{e['run_id']}__{e['test']}.md".replace("/", "_")
        (PATTERNS / name).write_text("\n".join(body))
        n += 1
    return n


def _get(path: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(SERVICE + path, timeout=timeout) as r:
        return json.loads(r.read())


def available() -> bool:
    try:
        return bool(_get("/health", 1.5).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def retrieve(query: str, k: int = 5, kinds: tuple[str, ...] = ("doc", "rtl", "pattern"),
             min_score: float = MIN_SCORE) -> list[dict]:
    """Top-k chunks of the given kinds. [] if the service is down."""
    # Ask for a wide pool: the kind filter below can discard most of it now
    # that spec, RTL, testbench and patterns share one index.
    q = urllib.parse.urlencode({"q": query[:2000], "k": max(k * 3, 40)})
    try:
        hits = _get(f"/query?{q}")["hits"]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return []
    out = [h for h in hits if h["kind"] in kinds and h["score"] >= min_score]
    return out[:k]


def reindex() -> dict | None:
    """Export patterns and ask the service to rebuild its index in place."""
    n = export_patterns()
    try:
        req = urllib.request.Request(SERVICE + "/reindex", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            meta = json.loads(r.read())
        meta["patterns_exported"] = n
        return meta
    except (urllib.error.URLError, OSError, ValueError):
        return None


def evidence_block(hits: list[dict], max_chars: int = 600) -> str:
    """Retrieved chunks formatted for a prompt, weak matches flagged."""
    if not hits:
        return ""
    parts = []
    for i, h in enumerate(hits, 1):
        kind = {"doc": "specification", "rtl": "RTL (upstream copy)",
                "pattern": "previously confirmed fix"}.get(h["kind"], h["kind"])
        weak = "" if h["keyword_score"] > 0 else " [no exact identifier match: weak evidence]"
        text = h["text"] if len(h["text"]) <= max_chars else h["text"][:max_chars] + "\n..."
        parts.append(f"[{i}] {kind} — {h['label']} (score {h['score']:.2f}){weak}\n{text}")
    return "\n\n".join(parts)


def cite(hits: list[dict]) -> list[dict]:
    """The compact form stored in the run record and shown in the app."""
    return [{"chunk_id": h["chunk_id"], "kind": h["kind"], "label": h["label"],
             "score": round(h["score"], 3), "keyword_score": round(h["keyword_score"], 3),
             "location": h.get("location", "")} for h in hits]
