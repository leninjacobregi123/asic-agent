"""The run record: runs/<id>/manifest.json (schema: manifest_schema.json).

Every stage writes its outcome here, and the attempt counter the retry cap is
enforced from lives here (README: design rules)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import settings

ROOT = settings.ROOT
MAX_ATTEMPTS = 3


def _verify_stage() -> dict:
    return {"attempt_count": 0, "max_attempts": MAX_ATTEMPTS, "lint_passed": None,
            "build_ok": None, "test_cases": [], "last_failure": None}


def new_manifest(run_id: str, run_dir: Path, rtl: Path, spec: Path, llm: str) -> dict:
    rel = lambda p: str(p.relative_to(ROOT))  # noqa: E731
    return {
        "run_id": run_id,
        "target_block": settings.load_project()["design"]["top"],
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "upstream_rtl": rel(settings.project_path(settings.load_project()["design"]["rtl"])),
        "working_rtl": rel(rtl),
        "llm": llm,
        "current_stage": "spec",
        "artifacts_dir": rel(run_dir),
        "stages": {
            "spec": {"spec_path": rel(spec), "approved": False, "revision": 0,
                     "clarifications": []},
            "rtl": {"files_changed": [], "edits": []},
            "verify_rtl": _verify_stage(),
            "synth": {"cache_key": None, "cache_hit": None, "reused_from_run": None},
            "verify_gate": _verify_stage(),
            "signoff": {"cache_key": None, "cache_hit": None, "reused_from_run": None},
        },
        "pending": None,   # work handed from a verification stage to an agent
        "history": [],
    }


def log_event(manifest: dict, event: str, detail: dict | None = None) -> None:
    manifest["history"].append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event, **(detail or {})})


def save_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))
