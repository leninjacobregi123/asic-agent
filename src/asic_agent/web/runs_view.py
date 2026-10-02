"""Read-only views over run records, for the web app."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import settings

LAYOUT_IMAGES = [("final_all", "Final layout"), ("final_placement", "Placement"),
                 ("final_worst_path", "Worst timing path")]


def layout_dir(run_dir: Path, top: str) -> Path:
    return run_dir / "pnr" / "reports" / "sky130hd" / top / "base"


def run_record(mpath: Path) -> dict:
    m = json.loads(mpath.read_text())
    run_dir = mpath.parent
    st = m["stages"]
    so = st.get("signoff", {})
    rep = layout_dir(run_dir, m.get("target_block", ""))
    images = [{"label": label, "src": f"/api/runs/{m['run_id']}/image/{stem}"}
              for stem, label in LAYOUT_IMAGES if (rep / f"{stem}.webp.png").exists()]
    diff = ""
    if st.get("rtl", {}).get("diff_path"):
        p = settings.ROOT / st["rtl"]["diff_path"]
        diff = p.read_text() if p.exists() else ""
    inject = next((h.get("kind") for h in m["history"] if h["event"] == "fault_injected"), None)
    # Per-test status at each level from the final attempt that ran.
    tests: dict[str, dict] = {}
    for lvl in ("verify_rtl", "verify_gate"):
        for t in st.get(lvl, {}).get("test_cases", []):
            tests.setdefault(t["id"], {})[lvl] = t["status"]
    return {
        "run_id": m["run_id"],
        "kind": m.get("kind", "verification"),
        "request": m.get("request"),
        "analysis": st.get("analysis"),
        "test": st.get("test"),
        "started": m.get("started"), "finished": m.get("finished"),
        "wall_seconds": m.get("wall_seconds"),
        "result": m.get("current_stage"),
        "llm": m.get("llm"), "inject": inject,
        "rtl_file": Path(m.get("working_rtl", "")).name,
        "spec": {"revision": st["spec"].get("revision"),
                 "clarifications": st["spec"].get("clarifications", [])},
        "edits": [{k: e.get(k) for k in ("test_id", "why", "applied", "error", "diff",
                                         "confirmed", "seconds", "candidate", "screen",
                                         "reverted", "author", "evidence")}
                  for e in st.get("rtl", {}).get("edits", [])],
        "final_diff": diff,
        "verify_rtl": {k: st["verify_rtl"].get(k) for k in ("attempt_count", "max_attempts")},
        "verify_gate": {k: st.get("verify_gate", {}).get(k) for k in ("attempt_count", "max_attempts")},
        "tests": tests,
        "synth": {k: st.get("synth", {}).get(k) for k in
                  ("cells", "area_um2", "cache_hit", "reused_from_run", "cache_key")},
        "signoff": {k: so.get(k) for k in
                    ("setup_wns_ns", "hold_wns_ns", "route_drc_errors", "lec_equivalent",
                     "drv_violations", "stdcells", "die_area_um2", "power_mw",
                     "wirelength_um", "clock_period_ns", "signoff_pass", "cache_hit",
                     "reused_from_run")},
        "images": images,
        "history": m["history"],
    }


def toolchain() -> dict:
    lock = (settings.CONFIG_DIR / "versions.lock").read_text()
    pick = lambda k: (re.search(rf"^{k}\s+(.+)$", lock, re.M) or [None, "?"])[1].strip()  # noqa: E731
    return {"verilator": pick("verilator"), "yosys": pick("yosys"),
            "openroad": pick("openroad-flow"), "pdk": pick("pdk-version")[:12]}
