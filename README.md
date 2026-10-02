# ASIC agent

**From a hardware change request in plain English to a verified, signed-off
design — with AI agents doing the routine work and engineers approving every
step.**

You describe the change ("the interrupt output must stay asserted until
software reads INTSTATUS"). The pipeline checks it against the design's own
specification, RTL and tests, writes a test that proves the change is needed,
edits the RTL until that test and every existing test pass, and carries the
result through synthesis, gate-level simulation, place-and-route and signoff.

When a test fails, the agents first work out **why** — a bug in the RTL, a gap
in the specification, or an unclear case — and send it to the right fixer
instead of blindly retrying. That is *diagnose-and-route*, the core of the
project.

```
 change request ─► compare with the knowledge base ─► spec decision (engineer)
                ─► new test, must fail on today's design
                ─► RTL change (each candidate linted and tested first)
                ─► full regression ─► synthesis ─► gate-level tests
                ─► place-and-route + signoff ─► confirmed change learned
```

Open-source EDA throughout: **Verilator**, **Yosys**, **OpenROAD**, **SkyWater sky130**.
Language models are used through provider APIs — bring a key for any of
Groq, OpenAI, Anthropic, Google Gemini, Mistral, OpenRouter, DeepSeek, Together,
Fireworks or Azure OpenAI.

---

## Quick start (Docker — one command)

```bash
git clone https://github.com/leninjacobregi123/asic-agent.git
cd asic-agent
docker compose up -d --build
```

1. Open **http://localhost:8080**
2. Go to **Models**, paste an API key for any provider, press **Save key**
3. Go to **New request**, describe a change, and follow it through to signoff

The first build takes about 10 minutes (it downloads the EDA toolchain once).
Everything runs in one container: the web app, REST API, agents, knowledge
base and EDA tools. Details: [`docs/DOCKER.md`](docs/DOCKER.md).

Requirements: Docker with Compose v2.24+, ~8 GB of disk, 4 CPU cores and
8 GB of memory recommended.

## What you can do

| In the app | What happens |
|---|---|
| **New request** | Plain-English change → analysis against the spec → failing test → RTL change → full verification → signoff |
| **Verification run** | Run the flow on a design (optionally with an injected fault) and watch diagnose-and-route at work |
| **Runs** | Every stage, diagnosis, approval, RTL diff, test result and the signed-off layout, per run |
| **Knowledge** | Search the knowledge base the agents read: spec, RTL, testbench, confirmed past fixes |
| **Models** | Add provider keys, test models, choose which model the agents use |
| **Project** | Point the pipeline at your own design and documents |

Every tool run (Verilator, Yosys, OpenROAD) and every decision that changes
what the chip should do waits for an engineer's approval — or is approved
automatically in *auto* mode, with each approval recorded.

## Using your own design

A design needs RTL, a specification (Markdown or text) and a testbench that
follows a small contract (one task per test selected with `+TEST=<name>`,
`RESULT` lines, two marker comments). See [`designs/README.md`](designs/README.md).
Two designs are included: `apb_gpio` (PULP Platform GPIO controller) and
`apb_timer` (a periodic timer).

## Integrating with other systems

A token-protected REST API (`/api/v1`, OpenAPI spec at `/api/v1/openapi.json`)
lets CI or other tools submit requests, answer approvals and read results.
See [`docs/API.md`](docs/API.md) for a complete Python example.

## Models

The agents only need chat-completion access to one provider. Without an
embeddings provider, the knowledge base searches by words and exact
identifiers (measured as accurate as a local embedding model on the
validation set); with one, it adds meaning-based search.

```bash
docker compose exec asic-agent asic-agent models                    # status
docker compose exec asic-agent asic-agent models use chat openai gpt-4.1
```

Configuration lives in [`config/llm.toml`](config/README.md); keys are never
stored in the repository.

## Documentation

| Document | For |
|---|---|
| [`docs/DOCKER.md`](docs/DOCKER.md) | running with Docker (recommended) |
| [`docs/SETUP.md`](docs/SETUP.md) | installing the toolchain directly on a host instead |
| [`docs/API.md`](docs/API.md) | REST API and integration example |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | serving it to a team: access token, TLS proxy, backups, upgrades |
| [`docs/DEMO.md`](docs/DEMO.md) | a guided demonstration |
| [`docs/reports/`](docs/reports/) | project report and retrieval measurements |
| [`config/README.md`](config/README.md), [`designs/README.md`](designs/README.md) | configuration and design contract |

## How it is built

| Path | Contents |
|---|---|
| `src/asic_agent/agents/` | analysis, test writer, RTL, spec and diagnosis agents (+ prompt skills) |
| `src/asic_agent/orchestrator/` | the flow: stages, approvals, retry cap, run records |
| `src/asic_agent/knowledge/` | knowledge base: chunking, keyword/BM25 and vector retrieval, service |
| `src/asic_agent/llm/` | provider-agnostic model clients, configuration, fallback |
| `src/asic_agent/web/` | web app and REST API |
| `src/asic_agent/eda/`, `eda/` | test runner; lint, synthesis and place-and-route scripts |
| `designs/`, `config/`, `data/` | designs, configuration, the record of confirmed fixes |

Rules the code enforces, whatever a model says:

- At most three change attempts per stage, counted by the orchestrator.
- Every EDA tool run is approved, including results reused from the cache.
- A gate-level failure is never treated as a specification question; the netlist is never hand-edited.
- Only fixes confirmed by the full test suite are added to the knowledge base.
- Cached synthesis and layout are reused only if RTL, tool and PDK versions, constraints and clock all match.
- A model that runs out of quota stops the run without spending attempts, or hands over to a configured fallback model.

## Licences

The included `apb_gpio` design is from the [PULP Platform](https://github.com/pulp-platform/apb_gpio)
under the Solderpad Hardware License 0.51 (`designs/apb_gpio/LICENSE`).
