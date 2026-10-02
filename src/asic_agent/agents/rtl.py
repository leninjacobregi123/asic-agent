"""
RTL agent — edits SystemVerilog to satisfy a failing, spec-backed test.

The model proposes ONE local edit as an exact find/replace pair. The edit is
applied only if the find text occurs exactly once in the file (whitespace at
line ends ignored); otherwise nothing is changed and the attempt is reported
as failed. A whole-file rewrite is never accepted: on a 300-line block a small
model rewriting everything is how unrelated logic gets silently broken.

Tagged blocks rather than JSON: Verilog inside JSON strings needs escaping
that small local models get wrong.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass

from .. import settings
from ..knowledge.client import cite, evidence_block
from ..llm import ChatClient, LLMError

_SYSTEM = """You are the RTL agent in an ASIC design pipeline. You fix SystemVerilog
so that it meets the specification. Make the smallest change that satisfies the
spec. Do not rename signals, change ports, or touch unrelated logic.

Answer in EXACTLY this format and nothing else:
<find>
(one to five lines copied EXACTLY from the RTL shown, to be replaced)
</find>
<replace>
(the new lines)
</replace>
<why>(one sentence)</why>

Prefer a single find/replace pair. Only when the change genuinely needs several
places (for example a new signal declared in one place and used in another), give
up to three pairs, each <find>...</find> followed by its <replace>...</replace>,
then one <why>."""

# The skills library: edit rules and idioms the agent draws on.
_SKILL = settings.SKILLS_DIR / "rtl-agent" / "SKILL.md"
if _SKILL.exists():
    _body = _SKILL.read_text().split("## Edit rules", 1)[-1]
    _SYSTEM += "\n\nRules and idioms (skills/rtl-agent/SKILL.md):\n## Edit rules" + _body

_TAG = re.compile(r"<(find|replace|why)>\n?(.*?)\n?</\1>", re.S)
_PAIR = re.compile(r"<find>\n?(.*?)\n?</find>\s*<replace>\n?(.*?)\n?</replace>", re.S)
MAX_PAIRS = 3          # repair: a fix should be small and local
FEATURE_MAX_PAIRS = 6  # change request: new behaviour touches declaration, write, read-back, logic

# The prompt asks for 1-5 lines; the model does not always comply (1 Oct: a
# 13-line rewrite of the wrong block). Enforced here, not trusted.
MAX_FIND_LINES = 6
MAX_REPLACE_LINES = 12


@dataclass
class RtlEdit:
    test_id: str
    find: str
    replace: str
    why: str
    applied: bool = False
    error: str = ""
    diff: str = ""
    evidence: list = None  # retrieved chunks shown to the model
    more: list = None      # further (find, replace) pairs for multi-place changes

    def to_dict(self) -> dict:
        return asdict(self)


def propose(client: ChatClient, test_meta: dict, failure_reason: str,
            spec_section: str, rtl_excerpt: str, diagnosis: str,
            rejected: list[str] | None = None, observed: str = "",
            temperature: float = 0.0, evidence: list[dict] | None = None,
            max_pairs: int = MAX_PAIRS) -> RtlEdit:
    tried = ""
    if rejected:
        tried = ("\nEdits already tried for this test that did NOT work (they were "
                 "rolled back; do something different):\n" + "\n".join(rejected) + "\n")
    user = f"""Failing test: {test_meta.get('id')}
Requirement: {test_meta.get('pass_condition')}
Observed failure: {failure_reason}
Diagnosis: {diagnosis}
{observed}
{tried}
Specification:
---
{spec_section}
---

RTL (the relevant part of the file):
---
{rtl_excerpt}
---"""
    if evidence:
        user += ("\n\nFrom the knowledge base: previously confirmed fixes and related spec "
                 "text. A past fix was confirmed on another run; copy it only if this file "
                 "and this failure really match, and always take <find> lines from the RTL "
                 "shown above, never from here:\n---\n" + evidence_block(evidence) + "\n---")
    if max_pairs > MAX_PAIRS:
        user += (f"\n\nThis adds new behaviour, so you may use up to {max_pairs} <find>/<replace> "
                 f"pairs (for example: a new register's declaration, its reset and write, its "
                 f"read-back, and the logic that uses it). Each pair is still small and local.")
    resp = client.complete(_SYSTEM, user, max_tokens=600, temperature=temperature)
    pairs = _PAIR.findall(resp.text)
    tags = {k: v for k, v in _TAG.findall(resp.text)}
    if not pairs:
        raise LLMError(f"RTL agent answer not in find/replace form: {resp.text[:300]}")
    if len(pairs) > max_pairs:
        raise LLMError(f"RTL agent proposed {len(pairs)} separate changes; at most {max_pairs}")
    return RtlEdit(test_meta.get("id", "?"), pairs[0][0], pairs[0][1],
                   tags.get("why", "").strip(), evidence=cite(evidence or []),
                   more=[list(p) for p in pairs[1:]])


def _code(text: str) -> str:
    """The text with comments and whitespace removed: what synthesis sees."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def _norm(s: str) -> list[str]:
    return [l.rstrip() for l in s.strip("\n").splitlines()]


def _apply_one(lines: list[str], find: str, replace: str,
               limits: tuple[int, int]) -> tuple[list[str] | None, str]:
    want = [l.strip() for l in _norm(find)]
    if not want or not any(want):
        return None, "empty find block"
    if len(want) > limits[0] or len(_norm(replace)) > limits[1]:
        return None, (f"edit too large ({len(want)} lines -> {len(_norm(replace))}; "
                      f"limit {limits[0]} -> {limits[1]})")
    hits = [i for i in range(len(lines) - len(want) + 1)
            if [l.strip() for l in lines[i:i + len(want)]] == want]
    if len(hits) != 1:
        return None, f"find text matched {len(hits)} times (must be exactly 1)"
    i = hits[0]
    # Keep the file's indentation for the first replaced line.
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    new = [(indent + l.lstrip()) if l.strip() else l for l in _norm(replace)]
    return lines[:i] + new + lines[i + len(want):], ""


# A feature request legitimately touches more than a bug fix. Screening and the
# full regression, not the size cap, are what keep a large change honest.
FEATURE_LIMITS = (20, 32)


def apply(rtl_text: str, edit: RtlEdit, filename: str = "design.sv",
          limits: tuple[int, int] = (MAX_FIND_LINES, MAX_REPLACE_LINES)) -> str:
    """Apply every find/replace pair, each matching exactly once, or none of
    them. Returns the new text (unchanged on any failure, with edit.error set)."""
    lines = rtl_text.splitlines()
    for n, (find, replace) in enumerate([(edit.find, edit.replace)] + list(edit.more or []), 1):
        lines, err = _apply_one(lines, find, replace, limits)
        if lines is None:
            edit.error = err if n == 1 else f"change {n}: {err}"
            return rtl_text
    new_text = "\n".join(lines) + ("\n" if rtl_text.endswith("\n") else "")
    edit.applied = _code(new_text) != _code(rtl_text)
    if not edit.applied:
        # Comment- or whitespace-only "fixes" (seen 1 Oct, with the model
        # claiming the code was corrected) cannot change behaviour.
        edit.error = ("replacement identical to original" if new_text == rtl_text
                      else "edit changes only comments/whitespace, not logic")
        new_text = rtl_text
    edit.diff = "".join(difflib.unified_diff(
        rtl_text.splitlines(True), new_text.splitlines(True),
        f"a/{filename}", f"b/{filename}"))
    return new_text


def locate(chunk_text: str, rtl_text: str, margin: int = 4) -> tuple[int, int] | None:
    """Where a retrieved RTL passage sits in THIS file (0-based line range).

    The index holds the project's RTL as ingested; a run edits its own copy,
    so passages are found by content, not by stored line numbers. Uses the
    passage's most distinctive lines (longest, non-comment) as anchors.
    """
    lines = [l.strip() for l in rtl_text.splitlines()]
    cand = sorted({l.strip() for l in chunk_text.splitlines()
                   if len(l.strip()) > 18 and not l.strip().startswith("//")}, key=len, reverse=True)
    pos = []
    for c in cand[:8]:
        idx = [i for i, l in enumerate(lines) if l == c]
        if len(idx) == 1:
            pos.append(idx[0])
    if not pos:
        return None
    n = len(chunk_text.splitlines())
    lo = max(0, min(pos) - margin)
    hi = min(len(lines), max(max(pos) + margin + 1, lo + n))
    return lo, hi
