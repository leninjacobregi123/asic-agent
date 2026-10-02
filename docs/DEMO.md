# Demo runbook

**Lead with a change request** in the app (New request). The pipeline compares
it with the knowledge base, writes a failing test first, changes the RTL and
proves it through signoff. Then show the Project page: "your data goes here".

## Before the session (10 min)

```bash
source scripts/env.sh
python3 -m asic_agent check            # every line [ok]; keys are listed by name only
docker image ls openroad/orfs          # OpenROAD image present
scripts/start_app.sh                   # http://localhost:8080
```

Check the model quota: on Groq's free tier each model allows 200,000 tokens per
rolling 24 hours (about 7–10 request runs). Pick a model with quota left in the
form's model list; a run that hits the limit switches to the configured
fallback model, or stops as "Model quota — rerun later" without spending attempts.

## Requests that work end to end (apb_gpio, `config/project.json`)

| Request | What it shows |
|---|---|
| "The GPIO interrupt output must stay asserted as a level while any interrupt status bit is set, and go low only after software reads the INTSTATUS register." | Verdict *agrees* with a verified spec quote; test written and failing first; one-line fix `interrupt = \|r_status`; 15/15; signoff in under a minute. |
| "Use the reserved interrupt type INTTYPE = 2'b11 as a level-high interrupt: while a GPIO input is high and its interrupt is enabled, its INTSTATUS bit must be set." | Verdict *conflicts* ("RFU"), a person decides; the test review removes one over-specified check; new level-interrupt logic; 16/16 at RTL and gate level; signoff (~10 min). |

Second design: copy `designs/apb_timer/project.json` to `config/project.json`
(or paste it on the Project page — the knowledge base rebuilds), then e.g.
"Add a one-shot mode: CTRL bit 2 ONESHOT; a compare match clears CTRL.EN …".

## Verification runs (diagnose-and-route)

New run → choose a fault scenario from the project (`fault_scenarios` in its
project.json): an injected RTL bug is diagnosed *implementation_bug* and fixed
by the RTL agent; the unmodified apb_gpio fails `irq_held_until_read`, which is
diagnosed *spec_ambiguity* and goes to the spec agent, where a person chooses
the reading. Start an injected-bug run from a signed-off run so the injected
problem is the only one.

## Say plainly

- Switching model or provider is a configuration change (Models page,
  `python3 -m asic_agent models use ...`); every run records which model answered. The diagnosis grounding (verbatim spec quote + a
  second yes/no check) is what keeps a model from inventing requirements.
- Tests are the real specification: a weak test once let a wrong fix pass
  (pads 1–31), and an over-specified test blocked a right one. Both are now checked.
- Keyword-only retrieval (no embeddings API) scored as well as the earlier
  local embedding model on the 12-question validation set.

## If something breaks live

| Symptom | Do this |
|---|---|
| A model's quota is spent | Pick another model in the form, or set `LLM_PROVIDER`/`LLM_MODEL` |
| Provider unreachable | `scripts/run_flow.sh --no-llm …` — rule-based diagnosis, people decide |
| Knowledge service down | Runs continue on the spec section each test names; `scripts/rag_up.sh` restarts it |
