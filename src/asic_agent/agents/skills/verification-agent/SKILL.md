# Verification agent skill — procedure, diagnosis rules, test-writing rules

The verification agent is split across code (under `src/asic_agent/`):
`orchestrator/driver.py` (`stage_verify`: lint, run, diagnose, route),
`agents/diagnose.py` (classification), `agents/test_writer.py` (new tests for
change requests, test review) and `eda/runner.py` (parallel runner). This file
is the procedure they implement. The **Test-writing rules** section is loaded
into the test agent's system prompt by `agents/test_writer.py`; the rest is
reference — the diagnosis prompt is kept as benchmarked.

Provenance: written 2 Oct 2026. The test-writing rules come from the
INTTYPE=11 feature request, where an agent-written test expected INTSTATUS
to clear when the pin went low; the spec says it clears when it is read.
Correct RTL could never pass that test. Reported as a prompt intervention.

## Procedure (one verification attempt)

| Step | What | Rule |
|---|---|---|
| 0 Lint | `verilator --lint-only` | Failure stops here; counts as an attempt, `implementation_bug`. |
| 1 Tests | Written before the RTL changes | A new test must FAIL on the unchanged RTL, or it proves nothing. |
| 2 Testbench | One task per test, `+TEST=<name>`, `RESULT` lines | Pass/fail is automatic; no human reading of waves. |
| 3 Run | Parallel across cores | Fail-fast: failing tests + smoke set first, full suite before the stage passes. |
| 4 Diagnose | Classify each failure, then route | See below. |
| 5 Retry | Cap 3 | Enforced by the driver from `attempt_count`, never by a prompt. |
| 6 Write back | Confirmed fixes only | Never escalated or unresolved failures. |

## Diagnosis rules

- `implementation_bug` -> RTL agent: the spec **states** the expected
  behaviour and the RTL does something else. Requires a verbatim spec quote
  that (a) exists in the spec and (b) a separate check agrees states the
  failing behaviour. Either check failing -> not `implementation_bug`.
- `spec_ambiguity` -> spec agent: the spec does not state the behaviour, or
  two correct readings exist. "RFU"/"reserved" states nothing.
- `unknown` -> human. Prefer it to a guess.
- At gate level `spec_ambiguity` is never valid (same spec passed at RTL
  level): gate failures route to RTL agent or human only. Fixes are made in
  RTL and re-synthesised; the netlist is never patched.
- A failing test is not evidence of a bug by itself. Judge from the spec.

## Test review (inside a change request)

When three or more compiling RTL candidates all fail the **same check**,
suspect the check before spending another attempt:

- **The new test:** ask whether the spec requires that check. If not, and a
  person agrees, rewrite the test (it must still fail on the unchanged RTL) or
  remove just that check.
- **An existing test, after a person approved a spec change:** ask whether the
  check tests behaviour the approved change replaces (e.g. "CTRL keeps only
  bits[1:0]" when the request adds CTRL bit 2). If so, and a person agrees,
  remove just that check from the run's testbench copy, recorded as
  `existing_test_updated`. Without an approved spec change an existing test is
  the specification and is never touched.

Either revision gives the change stage one attempt back.

## Test-writing rules

- Check only behaviour the specification or the change request states.
  Do not add checks for behaviour neither of them mentions.
- Keep existing semantics the request does not change. If the spec says a
  status bit is cleared when its register is read, do not expect it to clear
  any other way; clear it by reading the register.
- Check a requested behaviour on more than one instance when the design has
  several (pads, channels): a fix for instance 0 alone must fail the test.
- Each check's message says what is expected and why, in the spec's terms.
- Declare every local variable at the top of the task, before the first
  statement (SystemVerilog does not allow declarations after statements).
  Use the testbench's scratch variables where they exist.
- Leave the design in a clean state at the end of the test (disable what you
  enabled, read-clear what you set), so later tests are not affected.
