"""
Diagnose-and-route — the project's actual contribution.

On a failing test, decide WHY it failed before deciding WHERE to send the fix:

    implementation_bug  the RTL does the wrong thing      -> rtl_agent
    spec_ambiguity      the spec never said what is right -> spec_agent
    unknown             genuinely unclear                 -> human

Design: rule-assisted, with the model as a second opinion rather than the sole
judge. Two reasons. First, a deterministic heuristic makes the demo
reproducible and works with a weak local model. Second, it is the documented
contingency in the project plan if LLM classification proves unreliable — so
building it this way means the fallback is already the design, not a retreat.

The heuristic: every test case carries a `spec_ref` and a `pass_condition`. If
the referenced spec section does not actually state the behaviour the test
checks, the spec is ambiguous and no amount of RTL editing will converge. That
is a textual check, not a judgement call.

IMPORTANT: `spec_ambiguity` is only ever valid at RTL level. At gate level the
same RTL already passed against the same spec one stage earlier, so a failure
there is a synthesis artifact or a residual RTL bug — never a fresh spec
question. `diagnose()` enforces this regardless of what the model says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

from ..knowledge.client import cite, evidence_block
from ..llm import ChatClient, LLMError, extract_json

IMPLEMENTATION_BUG = "implementation_bug"
SPEC_AMBIGUITY = "spec_ambiguity"
UNKNOWN = "unknown"

ROUTE = {
    IMPLEMENTATION_BUG: "rtl_agent",
    SPEC_AMBIGUITY: "spec_agent",
    UNKNOWN: "human",
}


@dataclass
class Diagnosis:
    test_id: str
    root_cause: str
    route_to: str
    rationale: str
    method: str  # "heuristic" | "llm" | "heuristic+llm" | "forced"
    confidence: str  # "high" | "medium" | "low"
    evidence: list = field(default_factory=list)  # retrieved chunks shown to the model

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Heuristic layer
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEXISH = re.compile(r"^x[0-9a-f]+$")
_STOP = {
    "the", "a", "an", "is", "are", "of", "to", "in", "on", "and", "or", "not",
    "with", "after", "before", "when", "that", "this", "it", "its", "be", "as",
    "for", "from", "all", "any", "expected", "reads", "read", "write", "writes",
    "written", "value", "values", "bit", "bits", "back", "identically", "both",
    "set", "clear", "cleared", "prior", "retains", "leaves", "state", "pin",
    "waiting", "driving", "lower", "byte", "clocks", "edge", "rising",
}

# RTL spelling suffixes. A test's pass condition names signals (gpio_out_pad);
# the spec names the concept (GPIO_OUT). Without folding these, every test
# looks like it references behaviour the spec never mentions — which would
# diagnose every implementation bug as a spec ambiguity. Same normalisation
# the RAG keyword index uses, and for the same reason.
_SUFFIXES = ("_pad", "_reg", "_next", "_offset", "_q", "_d", "_n", "_i", "_o")

# Banked register names: the PULP spec says PADDIR_00_31 and INTTYPE0 where a
# test says PADDIR and INTTYPE. Same defect the RAG keyword index had on real
# PULP code (runs/m2-retrieval/ANALYSIS.md): without folding the bank suffix,
# every register-level test looks undocumented.
_BANK = re.compile(r"(?:_\d+)+$|(?<=[a-z])\d+$")


def _fold(word: str) -> str:
    w = word.lower()
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _salient_terms(text: str) -> set[str]:
    """Identifier-ish terms worth checking for in the spec section."""
    out: set[str] = set()
    for w in _WORD.findall(text):
        lw = w.lower()
        if len(w) < 3 or lw in _STOP or _HEXISH.match(lw):
            continue
        out.add(_fold(w))
        base = _BANK.sub("", lw)
        if base != lw and len(base) >= 3:
            out.add(_fold(base))
    return out


def _find_spec_section(spec_text: str, spec_ref: str) -> str | None:
    """Return the body of the spec section a test points at.

    `spec_ref` is a heading trail like "Register map > GPIO_IP (offset 0x10)";
    the last component is the heading to find.
    """
    if not spec_ref:
        return None
    leaf = spec_ref.split(">")[-1].strip()
    if not leaf:
        return None

    lines = spec_text.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if m and leaf.lower() in m.group(2).lower():
            start = i
            level = len(m.group(1))
            break
    if start is None:
        return None

    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return "\n".join(lines[start:end])


def heuristic_diagnose(test_id: str, pass_condition: str, spec_ref: str,
                       spec_text: str, failure_reason: str) -> Diagnosis | None:
    """Return a Diagnosis only when the textual evidence is clear.

    None means "heuristic cannot tell" — defer to the model.
    """
    section = _find_spec_section(spec_text, spec_ref)

    if section is None:
        return Diagnosis(
            test_id, SPEC_AMBIGUITY, ROUTE[SPEC_AMBIGUITY],
            f"The test references spec section '{spec_ref}', which does not "
            f"exist in the specification. A test cannot be satisfied against a "
            f"section that was never written.",
            "heuristic", "high",
        )

    want = _salient_terms(pass_condition)
    if not want:
        return None

    # Check the WHOLE spec, not only the referenced section. A single test
    # legitimately spans several sections — "GPIO_IE[0]=1 ... GPIO_IP[0]==1 and
    # irq_o==1" touches three. Scoring it against one section alone reported
    # well-documented behaviour as undocumented, which routes a real RTL bug to
    # the spec agent where it cannot be fixed. That is the expensive direction
    # to get wrong, so the question the heuristic asks is the weaker, safer one:
    # does the specification discuss these things anywhere at all?
    everywhere = _salient_terms(spec_text)
    missing = want - everywhere
    coverage = 1.0 - (len(missing) / len(want))

    # Nothing the test is about appears anywhere in the specification.
    if coverage < 0.34:
        return Diagnosis(
            test_id, SPEC_AMBIGUITY, ROUTE[SPEC_AMBIGUITY],
            f"The specification does not mention {', '.join(sorted(missing)[:6])} "
            f"anywhere ({coverage:.0%} of the pass condition's terms appear in "
            f"the document). The behaviour under test was never specified, so "
            f"editing RTL against it will not converge.",
            "heuristic", "high",
        )

    # Every term the test is about is discussed somewhere in the spec, and the
    # referenced section exists. The requirement is stated; the RTL missed it.
    if coverage >= 0.99:
        return Diagnosis(
            test_id, IMPLEMENTATION_BUG, ROUTE[IMPLEMENTATION_BUG],
            f"Section '{spec_ref}' exists and the specification covers every "
            f"term in the pass condition, so the requirement is stated. The RTL "
            f"did not meet it: {failure_reason}.",
            "heuristic", "medium",
        )

    # Partially covered — the honest answer is that text matching cannot tell.
    return None


# ---------------------------------------------------------------------------
# LLM layer
# ---------------------------------------------------------------------------

_SYSTEM = """You diagnose failing hardware verification tests for an ASIC design pipeline.

Classify the root cause as exactly one of:
  implementation_bug - the specification DOES state the expected behaviour, and
                       the RTL fails to implement it correctly.
  spec_ambiguity     - the specification does NOT state the behaviour under
                       test, or states it ambiguously enough that two correct
                       readings exist. The RTL cannot be fixed without first
                       clarifying the spec.
  unknown            - you cannot tell from the evidence given. Use this rather
                       than guessing.

Judge only from the evidence supplied. Do not assume behaviour the spec does
not state. A test failing is NOT by itself evidence of an implementation bug.

For implementation_bug you MUST copy, word for word, the sentence from the
specification section that states the expected behaviour into "spec_quote".
If no sentence in the section states it, the answer is not implementation_bug.

Respond with ONLY a JSON object:
{"root_cause": "...", "spec_quote": "<exact sentence from the spec, or empty>",
 "rationale": "<one or two sentences>", "confidence": "high|medium|low"}"""


_VERIFY = """You check one piece of evidence. Reply with exactly one word: YES or NO.
A hardware test failed. Answer YES if the specification sentence, read
literally, says what the hardware should have done in the failing situation.
It may leave other details unstated. Answer NO if the sentence is about a
different situation, only names a value as reserved or undefined (such as
"RFU"), or the expected behaviour would have to be guessed from it."""


def _quote_states(client: ChatClient, quote: str, behaviour: str, failure: str = "") -> bool:
    try:
        resp = client.complete(_VERIFY, f'Specification sentence: "{quote}"\n\n'
                                         f"What the test expects: {behaviour}\n"
                                         f"How it failed: {failure}\n\n"
                                         f"Does the sentence say what the hardware should have done?",
                               max_tokens=5)
    except LLMError:
        return False  # unverifiable support is not support
    return resp.text.strip().upper().startswith("YES")


def _quote_in_spec(quote: str, spec: str) -> bool:
    """Is the model's evidence really in the spec? Whitespace/case-insensitive,
    tolerant of a trimmed or lightly re-punctuated sentence."""
    norm = lambda s: re.sub(r"[^a-z0-9\[\]_]+", " ", s.lower()).strip()  # noqa: E731
    q, s = norm(quote), norm(spec)
    if len(q) < 15:
        return False
    if q in s:
        return True
    # Accept if a long run of its words appears contiguously (model trimmed
    # the sentence or merged two adjacent ones).
    words = q.split()
    span = max(6, int(len(words) * 0.8))
    return any(" ".join(words[i:i + span]) in s for i in range(len(words) - span + 1))


def llm_diagnose(client: ChatClient, test_id: str, description: str,
                 pass_condition: str, spec_ref: str, spec_section: str,
                 failure_reason: str, rtl_excerpt: str = "",
                 evidence: list[dict] | None = None) -> Diagnosis:
    user = f"""Failing test: {test_id}
Description: {description}
Pass condition: {pass_condition}
Observed failure: {failure_reason}

Specification section referenced by this test ("{spec_ref}"):
---
{spec_section or "(this section was not found in the specification)"}
---
"""
    if rtl_excerpt:
        user += f"\nRelevant RTL:\n---\n{rtl_excerpt[:4000]}\n---\n"
    evidence = evidence or []
    if evidence:
        user += ("\nFurther material retrieved from the knowledge base (spec and RTL). "
                 "Use it only if it bears on this test; it may be irrelevant:\n---\n"
                 + evidence_block(evidence) + "\n---\n")

    try:
        try:
            resp = client.complete(_SYSTEM, user, max_tokens=500)
        except LLMError as e:
            # Hosted free tiers cap request size (HTTP 413). Retrieved evidence
            # is the optional part of the prompt, so retry once without it.
            if "HTTP 413" not in str(e) or not evidence:
                raise
            user = user.split("\nFurther material retrieved from the knowledge base")[0]
            evidence = []
            resp = client.complete(_SYSTEM, user, max_tokens=500)
        data = extract_json(resp.text)
    except LLMError as e:
        return Diagnosis(
            test_id, UNKNOWN, ROUTE[UNKNOWN],
            f"Could not obtain a diagnosis from the model ({e}). Escalating "
            f"rather than guessing.",
            "llm", "low",
        )

    cause = str(data.get("root_cause", "")).strip().lower()
    if cause not in (IMPLEMENTATION_BUG, SPEC_AMBIGUITY, UNKNOWN):
        cause = UNKNOWN
    rationale = str(data.get("rationale", "")).strip() or "(no rationale given)"
    quote = str(data.get("spec_quote", "")).strip()
    confidence = str(data.get("confidence", "medium")).strip().lower()
    method = "llm"

    # Grounding check. implementation_bug claims "the spec states it"; that
    # claim is checkable, so it is checked. Measured 1 Oct: qwen2.5-coder-7b
    # called an unspecified behaviour an implementation_bug 3/3 times, citing
    # a spec sentence that does not exist.
    if cause == IMPLEMENTATION_BUG:
        spec_evidence = spec_section + "\n" + "\n".join(
            h["text"] for h in evidence if h["kind"] == "doc")
        if _quote_in_spec(quote, spec_evidence):
            # Existence is not support: on 1 Oct the model quoted the real
            # line "2'b11: RFU" as stating a level interrupt. A narrow yes/no
            # question on the sentence alone is something a small model can
            # answer, where it cannot be trusted to judge the whole case.
            if _quote_states(client, quote, pass_condition, failure_reason):
                rationale = f'{rationale} [spec: "{quote[:160]}"]'
                method = "llm+quote"
            else:
                cause, confidence, method = SPEC_AMBIGUITY, "medium", "llm+quote-verify"
                rationale = (f"The model cited \"{quote[:120]}\", but asked on its own, that "
                             f"sentence does not state the behaviour the test checks. "
                             f"Unstated behaviour is a spec ambiguity. Model's rationale: {rationale}")
        else:
            cause, confidence, method = SPEC_AMBIGUITY, "medium", "llm+quote-check"
            rationale = (f"The model called this an implementation bug but could not "
                         f"quote a spec sentence stating the behaviour "
                         f"({'no quote' if not quote else 'quote not found in spec: ' + repr(quote[:100])}). "
                         f"Unstated behaviour is a spec ambiguity. Model's rationale: {rationale}")
    return Diagnosis(test_id, cause, ROUTE[cause], rationale, method, confidence,
                     cite(evidence))


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

def diagnose(client: ChatClient | None, test_meta: dict, spec_text: str,
             failure_reason: str, stage: str = "verify_rtl",
             rtl_excerpt: str = "", evidence: list[dict] | None = None) -> Diagnosis:
    """Diagnose one failing test and decide where its fix should go.

    `stage` must be "verify_rtl" or "verify_gate". At gate level the
    spec_ambiguity route is structurally invalid and is overridden here, not
    left to the model's discretion.
    """
    test_id = test_meta.get("id", "?")
    spec_ref = test_meta.get("spec_ref", "")
    pass_condition = test_meta.get("pass_condition", "")
    description = test_meta.get("description", "")

    d = heuristic_diagnose(test_id, pass_condition, spec_ref, spec_text,
                           failure_reason)

    if d is None:
        if client is None:
            d = Diagnosis(
                test_id, UNKNOWN, ROUTE[UNKNOWN],
                "Heuristic was inconclusive and no model is configured to give "
                "a second opinion. Escalating rather than guessing.",
                "heuristic", "low",
            )
        else:
            section = _find_spec_section(spec_text, spec_ref) or ""
            d = llm_diagnose(client, test_id, description, pass_condition,
                             spec_ref, section, failure_reason, rtl_excerpt, evidence)

    # Structural override: spec_ambiguity cannot arise at gate level.
    if stage == "verify_gate" and d.root_cause == SPEC_AMBIGUITY:
        return Diagnosis(
            test_id, IMPLEMENTATION_BUG, ROUTE[IMPLEMENTATION_BUG],
            f"Diagnosed as spec_ambiguity, but this is the gate-level stage: "
            f"this RTL already passed RTL-level verification against this same "
            f"spec, so the spec cannot be the new problem. Treating as a "
            f"synthesis-introduced or residual RTL issue. Original rationale: "
            f"{d.rationale}",
            "forced", d.confidence, d.evidence,
        )
    return d
