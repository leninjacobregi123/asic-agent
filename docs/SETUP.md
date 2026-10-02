# Setup

Everything installs under `~/eda` with no `sudo`. Versions are pinned in
`config/versions.lock`; `scripts/env.sh` points at exactly these paths
(set `OSS_CAD_SUITE`, `PDK_ROOT`, `BUILD_DIR` or `ORFS_IMAGE` before sourcing
it if yours differ).

## 1. Python

Python 3.11+ (standard-library `tomllib`) and `numpy`. Nothing else is needed
at run time; no model runs on this machine.

```bash
python3 -c "import numpy, tomllib"      # usually already present; otherwise:
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```

`openpyxl` is optional, only for converting spreadsheet specifications
(`python3 -m asic_agent.knowledge.xlsx_to_md`).

## 2. OSS CAD Suite (Verilator, Yosys)

```bash
mkdir -p ~/eda && cd ~/eda
curl -L -o oss-cad-suite.tgz \
  https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-09-25/oss-cad-suite-linux-x64-20260925.tgz
tar xzf oss-cad-suite.tgz && mv oss-cad-suite oss-cad-suite-20260925 && rm oss-cad-suite.tgz
```

~700 MB. Do not add it to PATH in `~/.bashrc` — its bundled Python shadows the
system one. `source scripts/env.sh` adds it for the current shell only.

## 3. sky130 PDK (standard cells only)

```bash
python3 -m venv ~/eda/ciel-venv && ~/eda/ciel-venv/bin/pip install ciel
~/eda/ciel-venv/bin/ciel enable --pdk-root ~/eda/pdk --pdk-family sky130 \
  --include-libraries sky130_fd_sc_hd 1689ac3f2dc763876eaf967227c7dfe831b031ae
```

~770 MB on disk (liberty, LEF and tech files).

## 4. OpenROAD (place-and-route and signoff)

Runs in Docker; the image tag is pinned in `scripts/env.sh`:

```bash
docker pull openroad/orfs:26Q3-705-gecb3cfdeb
```

## 5. Model provider keys

The agents call a language model through a provider's API. Add a key and
choose models with the `models` command (keys are typed without echo and
stored in `~/.config/asic-agent/secrets.env`, mode 600, outside the project):

```bash
python3 -m asic_agent models                          # providers and key status
python3 -m asic_agent models add-key groq             # or openai, anthropic, gemini, ...
python3 -m asic_agent models available groq           # models this key can use
python3 -m asic_agent models test groq qwen/qwen3.8-27b
python3 -m asic_agent models use chat groq qwen/qwen3.8-27b
```

Providers are listed in `config/llm.toml` (no secrets there); any service
speaking the OpenAI or Anthropic API, Azure OpenAI included, is one table.

Without any key the flow still runs with rule-based diagnosis (`--no-llm`).
Without an embeddings provider, retrieval is keyword-only (BM25 + identifiers).

## 6. Check it

```bash
source scripts/env.sh
python3 -m asic_agent check                 # config, keys (names only), tools, paths
python3 -m asic_agent kb-reindex            # build the knowledge index
```

## Why builds go to ~/.cache

GNU Make, which Verilator uses, refuses to build in a directory whose path
contains a space, so `BUILD_DIR` defaults to `~/.cache/asic-agent/build` and the
repository can live anywhere. Each test run deletes its build directory when it
finishes (`KEEP_BUILD=1` keeps it).
