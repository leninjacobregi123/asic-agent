"""Content-hash cache for synthesis and place-and-route (README: design rules).

The key covers RTL content, tool versions, PDK version, every constraint and
config file, the design name and its clock — never RTL alone. A hit still
asks for approval (the driver's _cached_tool)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .. import settings
from .manifest import rel

SYNTH_SH = settings.EDA_DIR / "synth.sh"
PNR_SH = settings.EDA_DIR / "pnr.sh"


def _tool_version(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        return "missing"


def cache_key(kind: str, rtl: Path) -> str:
    """RTL + tool version + PDK version + every constraint/config file."""
    h = hashlib.sha256()
    h.update(kind.encode())
    h.update(rtl.read_bytes())
    h.update(os.environ.get("PDK_VERSION", "").encode())
    # The design and its clock come from project.json; a changed clock is a
    # changed constraint and must miss the cache (README: design rules).
    for v in ("TOP", "CLOCK_PORT", "CLOCK_PERIOD_NS"):
        h.update(f"{v}={os.environ.get(v, '')};".encode())
    if kind == "synth":
        h.update(_tool_version(["yosys", "-V"]).encode())
        h.update(os.environ.get("LIB_TT", "").encode())
        h.update(SYNTH_SH.read_bytes())
    else:
        h.update(os.environ.get("ORFS_IMAGE", "").encode())
        for f in (PNR_SH, settings.EDA_DIR / "pnr/config.mk", settings.EDA_DIR / "pnr/constraint.sdc"):
            h.update(f.read_bytes())
    return h.hexdigest()


def cache_dir() -> Path:
    return Path(os.environ["BUILD_DIR"]).parent / "result-cache"


def cache_lookup(kind: str, key: str) -> dict | None:
    meta = cache_dir() / kind / key / "cache.json"
    return json.loads(meta.read_text()) if meta.exists() else None


def cache_store(kind: str, key: str, src: Path, run_id: str) -> None:
    dst = cache_dir() / kind / key
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst / "out")
    (dst / "cache.json").write_text(json.dumps(
        {"run_id": run_id, "stored": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "rel_out": rel(src)}) + "\n")
