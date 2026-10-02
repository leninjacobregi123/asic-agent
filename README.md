# ASIC agent flow

Agents that take a hardware change request — written in plain English — to a
verified, signed-off design, and that work out *why* a test failed before
deciding who should fix it.

```
request ─► compare with knowledge base ─► spec decision (person) ─► new test (must fail)
        ─► RTL change (screened) ─► full regression ─► synthesis ─► gate level
        ─► place-and-route + signoff ─► confirmed change written to the knowledge base
```

Open-source tools only: Verilator, Yosys, OpenROAD (Docker), SkyWater sky130.
Language models are called through an API; nothing runs a model locally.
A person approves every tool run and every decision that changes what the
chip should do.

## Quick start

```bash
source scripts/env.sh                 # toolchain, PDK, active design (see docs/SETUP.md)
python3 -m asic_agent check           # validate configuration, keys, tools, paths
scripts/start_app.sh                  # web app: http://localhost:8080
```

or from the terminal:

```bash
scripts/run_flow.sh --request-file request.txt --auto-approve    # change request
scripts/run_flow.sh --auto-approve --inject int_rise_bug         # verification run
scripts/run_flow.sh --resume RUN --human-fix fix.diff            # after an escalation
```

## Configuration

| What | Where |
|---|---|
| LLM / embedding providers and models | `config/llm.toml` (no secrets); override with `LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, … |
| API keys | environment or `~/.config/asic-agent/secrets.env` (see `.env.example`) |
| The design being worked on | `config/project.json` — copy a `designs/*/project.json` there, or edit it on the app's Project page |
| Toolchain and PDK pins | `scripts/env.sh`, `config/versions.lock` |

Providers speaking the OpenAI chat format (OpenAI, Groq, Gemini, Mistral,
OpenRouter, …) and Anthropic's Messages API are supported. Adding a provider
is a `[providers.<name>]` table; a new wire format is one adapter class in
`src/asic_agent/llm/providers.py`. Set up providers with
`python3 -m asic_agent models` (add-key, available, test, use) or see the
app's Models page.

## Repository layout

| Path | Contents |
|---|---|
| `src/asic_agent/llm/` | provider-agnostic chat and embeddings clients, config, fallback |
| `src/asic_agent/knowledge/` | knowledge base: chunking, identifier + BM25 / vector retrieval, service |
| `src/asic_agent/agents/` | analysis, test writer, RTL, spec, diagnosis; prompt skills in `skills/` |
| `src/asic_agent/orchestrator/` | verification-run driver and change-request flow (retry cap, approvals) |
| `src/asic_agent/eda/` | parallel testbench runner |
| `src/asic_agent/web/` | Flow Console web app (stdlib HTTP, 127.0.0.1) |
| `eda/` | lint, synthesis and place-and-route scripts, constraints |
| `designs/` | `apb_gpio` (PULP, Solderpad licence) and `apb_timer` (second design) |
| `data/knowledge/` | confirmed-fix record the agents learn from (versioned) |
| `docs/` | setup, REST API, deployment, demo runbook, final report |
| `runs/` | run outputs (not versioned) |

## Design rules the code enforces

- The 3-attempt cap is counted by the orchestrator, never by a prompt.
- Every Verilator, Yosys and OpenROAD run waits for approval, including cache hits.
- A gate-level failure is never treated as a spec question; the netlist is never edited.
- Only fixes confirmed by the full test suite enter the knowledge base.
- Synthesis/signoff cache keys cover RTL, tool and PDK versions, constraints and the clock.
- A spent model quota stops the run without spending attempts (or switches to the configured fallback model).

Integrating with other systems (REST API, OpenAPI spec): `docs/API.md`.
Deploying for a team (token, TLS proxy, systemd, backups): `docs/DEPLOYMENT.md`.

Measurements and design decisions: `docs/reports/ASIC_Agent_Final_Report.pdf`
and `docs/reports/M2_retrieval_report.md`.
