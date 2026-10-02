"""
Spec agent — the escalation target for spec_ambiguity.

Given a test the verification agent diagnosed as spec_ambiguity, it names the
ambiguous sentence, states the two readings, recommends one, and drafts the
clarifying sentence. A human approves the reading (the driver's gate); the
clarification is then written into the run's working copy of the spec, so the
next diagnosis sees a spec that DOES state the behaviour.

The agent never edits RTL or tests. What the clarification implies for them is
reported as `consequence` (derived from the chosen reading, see CONSEQUENCE):
    rtl_must_change   the RTL does not do what the clarified spec says
    test_must_change  the RTL is right under the clarified spec; the test is not
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..knowledge.client import cite, evidence_block
from ..llm import ChatClient, LLMError, extract_json

_SYSTEM = """You are the specification agent in an ASIC design pipeline.
A verification test failed because the specification is ambiguous about the
behaviour under test. Reading (a) is what the test expects; it is given to you.
Describe reading (b): what the current RTL actually does in that situation.
Then decide which reading the spec author meant.

Respond with ONLY a JSON object:
{"ambiguous_text": "<the exact spec sentence that allows two readings>",
 "reading_b": "<what the current RTL does, one sentence>",
 "recommended": "a" or "b",
 "why": "<one sentence: why that reading is what the spec author meant>",
 "clarification": "<one or two sentences to add to the spec stating the recommended
                    reading unambiguously, including cycle timing>"}"""

# Reading (a) is the test's own pass condition, verbatim — never the model's
# paraphrase. On 1 Oct the model labelled the two readings the wrong way round
# ("RFU means no interrupt" as what the test assumes), which inverted the
# derived consequence.
# The consequence is derived, not asked for. On 1 Oct the model recommended the
# reading the test assumes and in the same answer said the test must change.
# A failing test proves test and RTL disagree, so the choice decides it:
#   chose (a), the test's reading -> rtl_must_change
#   chose (b), the RTL's reading  -> test_must_change
CONSEQUENCE = {"a": "rtl_must_change", "b": "test_must_change"}


@dataclass
class SpecResolution:
    test_id: str
    ambiguous_text: str
    reading_a: str
    reading_b: str
    recommended: str
    why: str
    clarification: str
    consequence: str
    chosen: str = ""  # filled by the approval gate: "a" | "b"
    draft: str = ""   # the model's drafted wording, for the person only
    evidence: list = None  # retrieved chunks shown to the model

    def to_dict(self) -> dict:
        return asdict(self)


def resolve(client: ChatClient, test_meta: dict, spec_section: str,
            failure_reason: str, rtl_excerpt: str,
            evidence: list[dict] | None = None) -> SpecResolution:
    user = f"""Failing test: {test_meta.get('id')}
Reading (a), what the test expects: {test_meta.get('pass_condition')}
Observed: {failure_reason}

Specification section:
---
{spec_section}
---

RTL (current behaviour):
---
{rtl_excerpt}
---"""
    if evidence:
        user += ("\n\nRelated material from the knowledge base (other spec sections and "
                 "previously confirmed resolutions). It may or may not apply:\n---\n"
                 + evidence_block(evidence) + "\n---")
    resp = client.complete(_SYSTEM, user, max_tokens=700)
    d = extract_json(resp.text)
    rec = str(d.get("recommended", "a")).strip().lower()[:1]
    reading_b = str(d.get("reading_b", "")).strip()
    if rec not in ("a", "b") or not reading_b:
        raise LLMError(f"spec agent returned an incomplete resolution: {resp.text[:300]}")
    res = SpecResolution(
        test_meta.get("id", "?"), str(d.get("ambiguous_text", "")).strip(),
        str(test_meta.get("pass_condition", "")).strip(), reading_b,
        rec, str(d.get("why", "")).strip(), "", CONSEQUENCE[rec])
    res.draft = str(d.get("clarification", "")).strip()
    res.evidence = cite(evidence or [])
    return res


def clarification_for(res: SpecResolution, choice: str) -> str:
    """The text written into the spec is the chosen reading itself.

    On 1 Oct the model chose "INTTYPE=11 is a level interrupt" and drafted
    "INTTYPE=11 generates no interrupt" in the same answer. A clarification
    that contradicts the decision is worse than none, so the model's draft is
    shown to the person but never written into the spec.
    """
    return res.reading_a if choice == "a" else res.reading_b


def apply_clarification(spec_text: str, section_heading: str, revision: int,
                        res: SpecResolution) -> str:
    """Insert the approved clarification at the end of the referenced section."""
    note = (f"\n> **Clarification (spec rev {revision}, from test `{res.test_id}`):** "
            f"{res.clarification}\n")
    lines = spec_text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("#") and section_heading.lower() in l.lower()), None)
    if start is None:
        return spec_text.rstrip() + "\n" + note
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = next((j for j in range(start + 1, len(lines))
                if lines[j].startswith("#") and
                len(lines[j]) - len(lines[j].lstrip("#")) <= level), len(lines))
    return "\n".join(lines[:end] + note.splitlines() + [""] + lines[end:]) + "\n"
