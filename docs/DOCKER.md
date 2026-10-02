# Running in Docker

One command starts the whole application in one container: the web app
(frontend and REST API), the orchestrator and agents, the knowledge (RAG)
service, and the EDA tools — OpenROAD, Yosys and the sky130hd files from the
pinned OpenROAD-flow-scripts image, Verilator from the pinned OSS CAD Suite.
Models are called through provider APIs; nothing else needs installing.

## Start

```bash
docker compose up -d --build        # first build ~10 min (downloads ~1.5 GB once)
```

Open **http://localhost:8080**, go to **Models**, paste a key for any one
provider (Groq, OpenAI, Anthropic, Gemini, Mistral, OpenRouter, DeepSeek,
Together, Fireworks) and press *Save key*. That is all: the agents use that
provider's recommended model, or the one you pick with *Use for chat*.

Alternatively put the key in a `.env` file before starting (see
`.env.example`), e.g. `OPENAI_API_KEY=...`.

```bash
docker compose ps                   # STATUS (healthy) when ready
docker compose logs -f              # server, knowledge service, runs
docker compose down                 # stop (data volumes are kept)
```

## Requirements

Docker with Compose v2.24+; about 8 GB of disk for the image plus run
outputs; 4 cores and 8 GB of memory are comfortable (place-and-route is the
heaviest step; limits are in `docker-compose.yml`).

## Your own design

Designs, configuration and data live in Docker volumes that are filled from
the image on first start. To work on your own block, copy it in and select it:

```bash
docker compose cp ./my_block asic-agent:/app/designs/my_block
# then paste its project.json on the app's Project page (or edit
# /app/config/project.json); the knowledge base rebuilds from it.
```

The contract for a design (RTL, spec, testbench markers) is described at the
top of `designs/apb_gpio/project.json`.

## Data

| Volume | Holds |
|---|---|
| `config` | `llm.toml` (providers, models per role), `project.json` (active design) |
| `data` | `knowledge/known_failure_patterns.jsonl` — what the agents learned; back it up |
| `designs` | the designs |
| `runs` | every run's record, logs, netlists and layouts |
| `secrets` | provider keys (file mode 600, never shown in the app) |

Volumes are filled from the image only when first created; after upgrading
the image, existing configuration and data are kept.

## Access

The port is published on this machine's loopback only, so no token is
needed. To let others use it: set `ASIC_AGENT_TOKEN` in `.env`, remove
`ASIC_AGENT_PUBLISHED_LOCALLY` from `docker-compose.yml`, change the port
mapping, and put a TLS reverse proxy in front (`DEPLOYMENT.md`). Users sign
in once with `/?token=…`; API clients send `Authorization: Bearer <token>`.

## From a terminal

```bash
docker compose exec asic-agent asic-agent check
docker compose exec asic-agent asic-agent models
docker compose exec asic-agent scripts/run_flow.sh --request-file /app/runs/req.txt --auto-approve
```

The sky130 standard-cell library comes from the OpenROAD-flow-scripts image
(`platforms/sky130hd`), the same library place-and-route uses.
