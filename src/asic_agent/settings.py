"""Central settings: repository paths, secrets loading, active project.

Everything that locates a file or reads configuration goes through here, so
no module hard-codes a path relative to its own location.

Secrets are read from the process environment only. For convenience,
`load_env()` first fills the environment from (in order, never overriding a
variable that is already set):

    $ASIC_AGENT_ENV_FILE                 an explicit file
    ~/.config/asic-agent/secrets.env     per-user, outside the repository
    <repo>/.env                          git-ignored

Files hold KEY=VALUE lines; values are never logged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = ROOT / "config"
LLM_CONFIG = CONFIG_DIR / "llm.toml"
PROJECT_FILE = Path(os.environ.get("ASIC_AGENT_PROJECT") or CONFIG_DIR / "project.json")
DESIGNS_DIR = ROOT / "designs"
EDA_DIR = ROOT / "eda"
SKILLS_DIR = Path(__file__).resolve().parent / "agents" / "skills"

# Runtime data. RUNS_DIR is output only (git-ignored); the knowledge record is
# product data the agents learn from, so it is versioned.
RUNS_DIR = Path(os.environ.get("ASIC_AGENT_RUNS") or ROOT / "runs")
CONSOLE_DIR = RUNS_DIR / ".console"
DATA_DIR = ROOT / "data"
KNOWLEDGE_RECORD = DATA_DIR / "knowledge" / "known_failure_patterns.jsonl"
PATTERNS_DIR = DATA_DIR / "knowledge" / "patterns"          # generated
INDEX_DIR = Path(os.environ.get("ASIC_AGENT_INDEX") or DATA_DIR / "index")  # generated

USER_ENV_FILE = Path.home() / ".config" / "asic-agent" / "secrets.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def load_env() -> list[Path]:
    """Fill os.environ from the env files that exist. Returns the files used."""
    used = []
    for p in (os.environ.get("ASIC_AGENT_ENV_FILE"), USER_ENV_FILE, ROOT / ".env"):
        if not p:
            continue
        p = Path(p).expanduser()
        if p.is_file():
            for k, v in _parse_env_file(p).items():
                os.environ.setdefault(k, v)
            used.append(p)
    return used


def load_project() -> dict:
    """The active project (config/project.json). Paths in it are relative to
    the repository root. `_root` is added for callers that resolve them."""
    p = json.loads(PROJECT_FILE.read_text())
    p["_root"] = ROOT
    return p


def project_path(rel: str) -> Path:
    return (ROOT / rel).resolve()
