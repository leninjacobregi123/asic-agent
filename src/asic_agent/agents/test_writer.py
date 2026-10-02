"""
Test agent — writes the test for a change request before the RTL is touched.

The test is written in the project's own testbench, from what retrieval returns
for it: the testbench's helper tasks and existing tests (so the new test follows
the house style without being told it), and the specification passages that
define the behaviour. The pipeline then requires the test to FAIL on the
unchanged RTL: a test that already passes cannot tell whether the change was
made.

Testbench contract (project.json): one task per test selected with +TEST=<name>,
'@agent-tests' and '@agent-dispatch' marker comments for insertion.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .. import settings
from ..knowledge.client import cite, evidence_block
from ..llm import ChatClient, LLMError

_SYSTEM = """You write SystemVerilog verification tests in an existing testbench.
Write ONE new test as a task, in the same style as the testbench passages shown.
Use only the helper tasks, constants and signals that appear in those passages or
in the notes. Do not declare a module, a clock, a reset, or new ports. Check every
expected value with the testbench's check helpers, with a message that says what
the specification requires.

Answer in EXACTLY this format:
<name>short_snake_case_name</name>
<task>
task automatic t_short_snake_case_name();
  ...
endtask
</task>
<why>(one sentence: what the test proves)</why>"""

# The skills library: the test-writing rules of the verification agent.
_SKILL = settings.SKILLS_DIR / "verification-agent" / "SKILL.md"
if _SKILL.exists():
    _SYSTEM += ("\n\nRules (skills/verification-agent/SKILL.md):\n"
                + _SKILL.read_text().split("## Test-writing rules", 1)[-1].strip())

_TAG = re.compile(r"<(name|task|why)>\n?(.*?)\n?</\1>", re.S)
NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


@dataclass
class GeneratedTest:
    name: str
    task: str
    why: str
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def write_test(client: ChatClient, summary: str, acceptance: str, spec_hits: list[dict],
               tb_hits: list[dict], helpers: list[str], note: str, existing: list[str],
               feedback: str = "", temperature: float = 0.0) -> GeneratedTest:
    user = f"""Change to verify: {summary}
Behaviour the test must check: {acceptance}

Testbench notes: {note}
Helper tasks available: {", ".join(helpers)}
Existing test names (do not reuse): {", ".join(existing)}

Specification passages:
---
{evidence_block(spec_hits, max_chars=600)}
---
Testbench passages (helpers and existing tests to imitate):
---
{evidence_block(tb_hits, max_chars=900)}
---"""
    if feedback:
        user += f"\n\nYour previous attempt was rejected: {feedback}\nWrite a corrected test."
    # Reasoning models (gpt-oss) think first; 4096 tokens was cut off on 2 Oct.
    resp = client.complete(_SYSTEM, user, max_tokens=8000, temperature=temperature)
    tags = {k: v for k, v in _TAG.findall(resp.text)}
    name = tags.get("name", "").strip().strip("`")
    task = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", tags.get("task", "").strip()).strip()
    # Accept the equivalent "task automatic t_x;" header and normalise it.
    task = re.sub(rf"^(task\s+automatic\s+t_{re.escape(name)})\s*;", r"\1();", task) if name else task
    if not NAME.match(name) or name in existing:
        raise LLMError(f"test agent gave an unusable test name: {name!r}")
    if not re.match(rf"task\s+automatic\s+t_{re.escape(name)}\s*\(\s*\)\s*;", task) \
            or not task.rstrip().endswith("endtask"):
        raise LLMError("test agent's task does not match 'task automatic t_<name>(); ... endtask'")
    if re.search(r"^\s*(module|endmodule|initial|always)\b", task, re.M):
        raise LLMError("test agent wrote structural code; only a task is allowed")
    return GeneratedTest(name, task, tags.get("why", "").strip(), cite(spec_hits + tb_hits))


# One check statement anywhere on a line (testbenches often write
# `apb_read(X, rd); check_eq(rd, 1, "msg");` on one line). Group 1: message.
_CHECK_CALL = re.compile(r'\bcheck(?:_eq)?\s*\((?:[^;"]|"[^"]*")*?"([^"]+)"\s*\)\s*;')


def drop_check(task: str, failed_check: str) -> str | None:
    """The test minus the one check statement whose message the failure reports.

    Used after a person agreed a check is not required (new test) or is made
    obsolete by an approved change (existing test): a mechanical, auditable
    edit. Only the check statement goes; reads and waits around it stay, so the
    bus timing of the test is unchanged. None unless exactly one check matches
    and at least one check remains."""
    lines = task.splitlines()
    calls = [(i, m) for i, l in enumerate(lines) for m in _CHECK_CALL.finditer(l)]
    hits = [(i, m) for i, m in calls if failed_check.startswith(m.group(1))]
    if len(hits) != 1 or len(calls) - 1 < 1:
        return None
    i, m = hits[0]
    rest = (lines[i][:m.start()] + lines[i][m.end():]).rstrip()
    note = "// check removed after test review: " + m.group(1)[:60]
    indent = re.match(r"\s*", lines[i]).group(0)
    lines[i] = f"{rest}  {note}" if rest.strip() else f"{indent}{note}"
    return "\n".join(lines)


def remove(tb_text: str, name: str, task: str) -> str:
    """Take a generated test (its task, comment line and dispatcher entry) out."""
    lines = tb_text.splitlines()
    out, skip, after = [], False, False
    for l in lines:
        if l.strip().startswith(f"task automatic t_{name}("):
            if out and "Generated by the request pipeline" in out[-1]:
                out.pop()
            skip = True
        if skip:
            if l.strip() == "endtask":
                skip, after = False, True
            continue
        if after:                     # the blank line insert() put after the task
            after = False
            if not l.strip():
                continue
        if re.match(rf'^\s*"{re.escape(name)}"\s*:\s*t_{re.escape(name)}\s*\(\s*\)\s*;', l):
            continue
        out.append(l)
    return "\n".join(out) + "\n"


def insert(tb_text: str, test: GeneratedTest) -> str:
    """Place the task and its dispatcher entry at the testbench's markers."""
    if "@agent-tests" not in tb_text or "@agent-dispatch" not in tb_text:
        raise ValueError("testbench lacks the @agent-tests / @agent-dispatch markers")
    lines = tb_text.splitlines()
    ti = next(i for i, l in enumerate(lines) if "@agent-tests" in l)
    body = ["  // Generated by the request pipeline's test agent. " + test.why] + \
        ["  " + l if l.strip() else l for l in test.task.splitlines()] + [""]
    lines[ti:ti] = body
    di = next(i for i, l in enumerate(lines) if "@agent-dispatch" in l)
    indent = lines[di][: len(lines[di]) - len(lines[di].lstrip())]
    lines[di:di] = [f'{indent}"{test.name}": t_{test.name}();']
    return "\n".join(lines) + "\n"


_REVIEW = """You review one failing check in a hardware test against the specification.
Several different correct-looking RTL changes all fail this same check. Decide
whether the check itself is right.

Respond with ONLY a JSON object:
{"check_is_required": true or false,
 "reason": "<one or two sentences, quoting the specification sentence that decides it>",
 "revised_task": "<if false: the whole corrected task, task automatic t_<name>(); ... endtask,
                  keeping every other check; else empty>"}"""


_REVIEW_EXISTING = """A person has approved a change to a hardware design's specification.
An EXISTING test, written for the behaviour before the change, now fails one
check on every candidate design that implements the change. Decide whether that
check tests behaviour the approved change replaces (then the check is obsolete),
or behaviour the change keeps (then the check is still required and the
candidates are wrong).

Respond with ONLY a JSON object:
{"check_is_obsolete": true or false,
 "reason": "<one or two sentences naming the old and the new behaviour>"}"""


def review_existing(client: ChatClient, task_code: str, failing_check: str,
                    approved_change: str, request: str) -> dict:
    """Does an approved spec change make one check of an existing test obsolete?"""
    from ..llm import extract_json
    user = f"""Approved change request: {request}

Approved specification change: {approved_change}

Existing test:
{task_code}

The check every candidate fails: {failing_check}"""
    d = extract_json(client.complete(_REVIEW_EXISTING, user, max_tokens=1500).text)
    return {"obsolete": bool(d.get("check_is_obsolete", False)),
            "reason": str(d.get("reason", "")).strip()}


def task_code(tb_text: str, name: str) -> str | None:
    """The text of `task automatic t_<name>(); ... endtask` in a testbench."""
    m = re.search(rf"^[ \t]*task\s+automatic\s+t_{re.escape(name)}\s*\(\s*\)\s*;.*?^[ \t]*endtask",
                  tb_text, re.S | re.M)
    return m.group(0) if m else None


def review(client: ChatClient, test: dict, failing_check: str, spec_hits: list[dict],
           request: str) -> dict:
    """Is a check that no RTL change can satisfy actually required by the spec?"""
    from ..llm import extract_json
    user = f"""Request: {request}

Test:
{test.get("code")}

The check that keeps failing: {failing_check}

Specification passages:
---
{evidence_block(spec_hits, max_chars=700)}
---"""
    d = extract_json(client.complete(_REVIEW, user, max_tokens=1500).text)
    out = {"required": bool(d.get("check_is_required", True)),
           "reason": str(d.get("reason", "")).strip(), "revised": None}
    rev = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", str(d.get("revised_task") or "").strip()).strip()
    name = test.get("name", "")
    rev = re.sub(rf"^(task\s+automatic\s+t_{re.escape(name)})\s*;", r"\1();", rev)
    if not out["required"] and re.match(rf"task\s+automatic\s+t_{re.escape(name)}\s*\(\s*\)\s*;", rev) \
            and rev.rstrip().endswith("endtask"):
        out["revised"] = rev
    return out
