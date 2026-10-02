# Configuration

| File | What it controls |
|---|---|
| `llm.toml` | model providers and which model each role uses — no secrets |
| `project.json` | the **active design**: RTL, specification, testbench, knowledge sources, fault scenarios |
| `versions.lock` | the pinned toolchain (Verilator, Yosys, OpenROAD image, sky130 PDK) |

## Models (`llm.toml`)

Roles: **chat** (all agents), **fallback** (used when chat's quota runs out),
**embeddings** (meaning-based knowledge search; `"none"` = words and identifiers only).

Each `[providers.<name>]` table names the endpoint, the wire format (`openai`
or `anthropic`), the environment variable holding the key, and the models
offered in the app. Providers that need a different key header or query
parameters (Azure OpenAI) set `auth_header`, `auth_prefix` and `query` — a
commented template is included. `model_params` adds per-model request options
(e.g. `reasoning_effort`).

Only a key is required: if the `[chat]` provider has no key, the first
provider with a key is used with its first listed model. Set things up from
the app's **Models** page or with:

```bash
asic-agent models                       # status
asic-agent models add-key openai        # typed, not shown
asic-agent models available openai      # models the key can use
asic-agent models test openai gpt-4.1
asic-agent models use chat openai gpt-4.1
```

Keys live in `~/.config/asic-agent/secrets.env` (mode 600), a `.env` file or
the environment — never in this folder. Environment overrides:
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_MODEL`,
`EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`.

## Active design (`project.json`)

Copy a `designs/<name>/project.json` here, or paste one on the app's
**Project** page (validated, then the knowledge base is rebuilt). The fields
are documented at the top of `designs/apb_gpio/project.json` and in
`../designs/README.md`.
