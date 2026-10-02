#!/usr/bin/env python3
"""Summarise an OpenROAD-flow-scripts run as one signoff record.

Usage: eda/pnr_summary.py <pnr_out_dir>      (prints JSON on stdout)

Signoff passes only if every check below holds. Thresholds are written here,
not left to a reader of the raw reports:
  setup and hold worst slack >= 0, zero DRV violations, zero routing DRC
  errors, and the post-route netlist logically equivalent to the synthesised
  one (ORFS's own LEC step).
"""

import os
import json
import sys
from pathlib import Path

PLATFORM, DESIGN, VARIANT = "sky130hd", os.environ.get("TOP", "apb_gpio"), "base"


def main() -> int:
    out = Path(sys.argv[1])
    logs = out / "logs" / PLATFORM / DESIGN / VARIANT
    reports = out / "reports" / PLATFORM / DESIGN / VARIANT
    results = out / "results" / PLATFORM / DESIGN / VARIANT

    fin = json.loads((logs / "6_report.json").read_text())
    route = json.loads((logs / "5_2_route.json").read_text())
    lec_log = logs / "6_final_lec_check.log"
    lec_ok = lec_log.exists() and "Circuits are IDENTICAL" in lec_log.read_text()

    s = {
        "clock_period_ns": float((results / "clock_period.txt").read_text().split()[0])
        if (results / "clock_period.txt").exists() else None,
        "setup_wns_ns": round(fin["finish__timing__setup__ws"], 3),
        "setup_tns_ns": fin["finish__timing__setup__tns"],
        "hold_wns_ns": round(fin["finish__timing__hold__ws"], 3),
        "hold_tns_ns": fin["finish__timing__hold__tns"],
        "drv_violations": int(fin.get("finish__timing__drv__max_slew", 0)
                              + fin.get("finish__timing__drv__max_cap", 0)
                              + fin.get("finish__timing__drv__max_fanout", 0)),
        "route_drc_errors": int(route["detailedroute__route__drc_errors"]),
        "lec_equivalent": lec_ok,
        "stdcells": int(fin["finish__design__instance__count__stdcell"]),
        "stdcell_area_um2": round(fin["finish__design__instance__area__stdcell"], 1),
        "die_area_um2": round(fin["finish__design__die__area"], 1),
        "power_mw": round(fin["finish__power__total"] * 1e3, 3),
        "wirelength_um": int(route["detailedroute__route__wirelength"]),
    }
    s["signoff_pass"] = bool(
        s["setup_wns_ns"] >= 0 and s["hold_wns_ns"] >= 0 and s["drv_violations"] == 0
        and s["route_drc_errors"] == 0 and s["lec_equivalent"])
    s["reports"] = {
        "gds": str(results / "6_final.gds"),
        "layout_png": str(reports / "final_all.webp.png"),
        "routing_png": str(reports / "final_routing.webp.png"),
        "finish_rpt": str(reports / "6_finish.rpt"),
    }
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
