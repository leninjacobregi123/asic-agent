"""
Analysis agent — compares a change request with the knowledge base.

Input: a request in plain language, and what retrieval returned for it (spec
passages, RTL passages, previously confirmed changes). Output: a verdict on how
the request relates to the specification, the observable behaviour a test must
check, and which retrieved RTL passages implement the behaviour to change.

    agrees     the spec already requires this; the design should already do it
    extends    the spec does not cover it; the spec needs an addition
    conflicts  the spec says something different; a person must decide

Grounded like the diagnosis: 'agrees' and 'conflicts' are claims about what the
spec says, so the model must quote the sentence, and the quote must exist in a
retrieved spec passage. A claim without a real quote becomes 'extends' — the
spec does not demonstrably say it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .diagnose import _quote_in_spec
from ..knowledge.client import cite, evidence_block
from ..llm import ChatClient, LLMError, extract_json

VERDICTS = ("agrees", "extends", "conflicts")

_SYSTEM = """You analyse change requests for a hardware design against its documentation.
You are given a request and numbered passages retrieved from the project's knowledge
base: specification text, RTL source, and previously confirmed changes.

Decide how the request relates to the specification:
  agrees     a specification passage already requires exactly this behaviour
  extends    no specification passage covers this behaviour
  conflicts  a specification passage requires different behaviour

Respond with ONLY a JSON object:
{"verdict": "agrees" | "extends" | "conflicts",
 "spec_quote": "<for agrees/conflicts: the exact sentence copied from a specification passage; else empty>",
 "summary": "<one sentence: the change the design needs>",
 "acceptance": "<the observable behaviour a test must check: which registers are written,
                which inputs change, what outputs or register values are expected and when>",
 "rtl_targets": [<numbers of the RTL passages that implement the behaviour to change>],
 "spec_addition": "<for extends/conflicts: one or two sentences to add to the specification>"}"""


@dataclass
class Analysis:
    request: str
    verdict: str
    spec_quote: str
    summary: str
    acceptance: str
    rtl_targets: list = field(default_factory=list)   # chunk_ids
    spec_addition: str = ""
    method: str = "llm"
    note: str = ""
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def analyse(client: ChatClient, request: str, hits: list[dict]) -> Analysis:
    rtl_hits = [h for h in hits if h["kind"] == "rtl"]
    user = f"""Change request:
{request}

Retrieved passages:
---
{evidence_block(hits, max_chars=700)}
---
RTL passages are numbered as above; use those numbers in rtl_targets."""
    try:
        resp = client.complete(_SYSTEM, user, max_tokens=900)
    except LLMError as e:
        if "HTTP 413" not in str(e):
            raise
        # Free-tier request size limit: keep the spec and the top RTL passages.
        hits = [h for h in hits if h["kind"] == "doc"][:3] + rtl_hits[:2]
        rtl_hits = [h for h in hits if h["kind"] == "rtl"]
        user = user.split("Retrieved passages:")[0] + "Retrieved passages:\n---\n" + \
            evidence_block(hits, max_chars=500) + "\n---"
        resp = client.complete(_SYSTEM, user, max_tokens=900)
    d = extract_json(resp.text)

    verdict = str(d.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        verdict = "extends"
    quote = str(d.get("spec_quote", "")).strip()
    a = Analysis(request, verdict, quote, str(d.get("summary", "")).strip(),
                 str(d.get("acceptance", "")).strip(),
                 spec_addition=str(d.get("spec_addition", "")).strip(), evidence=cite(hits))

    # rtl_targets are passage numbers in the evidence block (1-based over all hits).
    targets = []
    for n in d.get("rtl_targets") or []:
        try:
            h = hits[int(n) - 1]
        except (ValueError, TypeError, IndexError):
            continue
        if h["kind"] == "rtl":
            targets.append(h["chunk_id"])
    if not targets and rtl_hits:
        targets = [rtl_hits[0]["chunk_id"]]
        a.note += "No RTL passage named; using the best-matching one. "
    a.rtl_targets = targets

    # Grounding: a claim about what the spec says needs a real quote.
    if verdict in ("agrees", "conflicts"):
        spec_text = "\n".join(h["text"] for h in hits if h["kind"] == "doc")
        if not _quote_in_spec(quote, spec_text):
            a.note += (f"Claimed '{verdict}' but the quote is not in any retrieved spec passage; "
                       f"treated as 'extends'. ")
            a.verdict, a.method = "extends", "llm+quote-check"
            if not a.spec_addition:
                a.spec_addition = a.summary
        else:
            a.method = "llm+quote"
    if not a.acceptance:
        raise LLMError("analysis gave no acceptance criterion; a test cannot be written")
    return a
