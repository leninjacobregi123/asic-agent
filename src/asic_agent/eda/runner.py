#!/usr/bin/env python3
"""
Parallel test runner for the active project's testbench (verification loop step 3).

Builds the Verilator testbench once, then runs each test as its own process
across the available cores. Writes a machine-readable results.json that the
verification agent reads for diagnosis, plus one log per test.

Usage:
    source scripts/env.sh
    python3 -m asic_agent.eda.runner --out runs/<run>/sim                 # full suite
    python3 -m asic_agent.eda.runner --out ... --tests int_rise,int_fall  # a subset
    python3 -m asic_agent.eda.runner --out ... --retry-from runs/<prev>/sim/results.json
        # fail-fast retry: previous failures + smoke set first, and the full
        # suite only once those pass (fail-fast retry).

Exit status: 0 if every test that ran passed, 1 if any failed, 2 on build error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .. import settings

# The testbench named in config/project.json (scripts/env.sh exports TB_FILE).
TB = Path(os.environ.get("TB_FILE") or settings.project_path(
    settings.load_project()["testbench"]["file"]))
TB_TOP = "tb"


def tb_top(tb: Path) -> str:
    """The testbench's top module: the first module declared in its file."""
    m = re.search(r"^\s*module\s+(\w+)", tb.read_text(), re.M)
    return m.group(1) if m else TB_TOP

# Smoke set and excluded tests come from project.json (testbench.smoke,
# testbench.regression_exclude), so another design's testbench needs no edit
# here. The default suite is the testbench's own dispatcher (discover()).
def _tb_config() -> dict:
    try:
        return settings.load_project().get("testbench", {})
    except (OSError, ValueError):
        return {}


# Cheap, broad tests run first on every retry to catch a fix that broke
# something unrelated before the full suite is spent.
SMOKE = _tb_config().get("smoke", ["reset_values"])
# Runnable only when named in --tests (e.g. deliberate spec-gap probes used by
# a fault scenario that enables them). Not part of the default suite.
OPTIONAL_TESTS = _tb_config().get("regression_exclude", [])

RESULT_RE = re.compile(r"^RESULT (\S+) (PASS|FAIL)\s*(.*)$")
DISPATCH_RE = re.compile(r'^\s*"([A-Za-z0-9_]+)"\s*:\s*t_[A-Za-z0-9_]+\s*\(\s*\)\s*;', re.M)


def discover(tb: Path) -> list[str]:
    """Test names from the testbench's own dispatcher, so a testbench that
    gained tests (the request pipeline adds them) needs no list edited here."""
    return DISPATCH_RE.findall(tb.read_text())


def env_or_die(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set — run `source scripts/env.sh` first")
    return v


def cell_models(build_dir: Path) -> Path:
    """Standard-cell simulation models generated from the liberty file.

    Not the PDK's sky130_fd_sc_hd.v: its flops are UDPs with gate delays,
    which Verilator ignores, so a 2-flop synchroniser collapses into one stage
    and every edge-detect test fails at gate level although the netlist is
    correct (measured 1 Oct: 5 false failures). Yosys turns each liberty ff
    group into an always_ff with proper non-blocking semantics.
    """
    lib = env_or_die("LIB_TT")
    out = build_dir.parent / f"cells_{Path(lib).stem}.v"
    if not out.exists():
        ys = build_dir.parent / "cells.ys"
        ys.write_text(f'read_liberty -ignore_miss_func "{lib}"\n'
                      f"setattr -mod -unset blackbox -unset whitebox\n"
                      f'write_verilog -noattr "{out}"\n')
        subprocess.run(["yosys", "-q", "-s", str(ys)], check=True)
    return out


def build(rtl: list[Path], build_dir: Path, log: Path, waves: bool = False,
          gate: bool = False) -> bool:
    build_dir.mkdir(parents=True, exist_ok=True)
    top = env_or_die("TOP")
    if gate:
        # A tool-generated netlist is not lint-clean by any standard worth
        # enforcing; it was linted as RTL before synthesis.
        srcs = ["-DGATE_LEVEL", "-Wno-fatal", "-Wno-lint", "-Wno-style",
                "-Wno-MULTIDRIVEN", "-Wno-UNOPTFLAT",
                str(TB), *map(str, rtl), str(cell_models(build_dir))]
    else:
        # RTL waivers are the same file lint uses; the testbench is built
        # warnings-as-errors like everything else.
        vlt = settings.EDA_DIR / "lint" / f"{top}.vlt"   # optional per-design waivers
        srcs = [*([str(vlt)] if vlt.exists() else []), str(TB), *map(str, rtl)]
    cmd = [
        "verilator", "--binary", "--timing", "-j", "0",
        "--top-module", TB_TOP,
        *(["--trace"] if waves else []),   # $dumpvars is a no-op without it
        "-Mdir", str(build_dir), "-o", TB_TOP,
        *srcs,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    log.write_text(" ".join(cmd) + "\n\n" + p.stdout + p.stderr)
    return p.returncode == 0


def run_one(binary: Path, name: str, logdir: Path, waves: bool, timeout_s: int) -> dict:
    args = [str(binary), f"+TEST={name}"] + (["+WAVES"] if waves else [])
    t0 = time.monotonic()
    try:
        p = subprocess.run(args, capture_output=True, text=True, cwd=logdir, timeout=timeout_s)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") + "\nRUNNER: wall-clock timeout\n"
    dur = time.monotonic() - t0
    (logdir / f"{name}.log").write_text(out)

    status, detail = "FAIL", "no RESULT line (crash or early exit)"
    for line in out.splitlines():
        m = RESULT_RE.match(line.strip())
        if m and m.group(1) == name:
            status, detail = m.group(2), m.group(3)
    checks = [l.strip()[len("CHECK FAIL: "):] for l in out.splitlines()
              if l.strip().startswith("CHECK FAIL: ")]
    return {"test": name, "status": status, "detail": detail,
            "failed_checks": checks, "seconds": round(dur, 3),
            "log": f"{name}.log"}


def run_batch(binary: Path, names: list[str], logdir: Path, jobs: int,
              waves: bool, timeout_s: int) -> list[dict]:
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        return list(ex.map(lambda n: run_one(binary, n, logdir, waves, timeout_s), names))


def main() -> int:
    try:
        return _main()
    finally:
        # Each run, attempt and candidate builds into its own directory; left
        # behind they filled ~30 GB in a day (2 Oct). The binary is only
        # needed while the tests run.
        if _BUILT and not os.environ.get("KEEP_BUILD"):
            shutil.rmtree(_BUILT[0], ignore_errors=True)


_BUILT: list[Path] = []


def _main() -> int:
    ap = argparse.ArgumentParser(description="Run the project's testbench in parallel.")
    ap.add_argument("--out", type=Path, required=True, help="results directory")
    ap.add_argument("--rtl", type=Path, nargs="+", help="RTL files (default: $RTL_DIR/$TOP.sv)")
    ap.add_argument("--tb", type=Path, default=None, help="testbench file (default: from config/project.json)")
    ap.add_argument("--gate", type=Path, help="synthesised netlist: gate-level run (verification at gate level)")
    ap.add_argument("--tests", help="comma-separated subset (default: all)")
    ap.add_argument("--retry-from", type=Path, help="previous results.json: fail-fast retry order")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--waves", action="store_true", help="dump a VCD per test")
    ap.add_argument("--timeout", type=int, default=60, help="per-test wall-clock seconds")
    args = ap.parse_args()

    global TB, TB_TOP
    if args.tb:
        TB = args.tb.resolve()
    TB_TOP = tb_top(TB)
    rtl = args.rtl or [Path(os.environ.get("RTL_FILE_DEFAULT")
                            or Path(env_or_die("RTL_DIR")) / f"{env_or_die('TOP')}.sv")]
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    build_dir = Path(env_or_die("BUILD_DIR")) / "tb" / out.as_posix().strip("/").replace("/", "_").replace(" ", "_")
    if args.waves:
        build_dir = build_dir.with_name(build_dir.name + "_trace")

    print(f"building {TB_TOP} against {', '.join(p.name for p in rtl)} ...")
    if args.gate:
        rtl = [args.gate.resolve()]
        build_dir = build_dir.with_name(build_dir.name + "_gate")
    _BUILT.append(build_dir)
    if not build(rtl, build_dir, out / "build.log", waves=args.waves, gate=bool(args.gate)):
        print(f"BUILD FAILED — see {out / 'build.log'}")
        _write(out, rtl, [], build_ok=False, phases=[])
        return 2
    binary = build_dir / TB_TOP

    unknown = set((args.tests or "").split(",")) - set(discover(TB)) - {""}
    if unknown:
        sys.exit(f"unknown tests: {', '.join(sorted(unknown))}")
    selected = args.tests.split(",") if args.tests else [t for t in discover(TB) if t not in OPTIONAL_TESTS]

    results: list[dict] = []
    phases: list[dict] = []
    if args.retry_from:
        prev = json.loads(args.retry_from.read_text())
        failed_before = [r["test"] for r in prev["results"] if r["status"] != "PASS"]
        first = [t for t in dict.fromkeys(failed_before + SMOKE) if t in selected]
        print(f"fail-fast phase: {len(first)} tests (previous failures + smoke)")
        results = run_batch(binary, first, out, args.jobs, args.waves, args.timeout)
        phases.append({"phase": "fail_fast", "tests": first})
        _print(results)
        if any(r["status"] != "PASS" for r in results):
            print("fail-fast phase has failures; full suite not run")
            return _write(out, rtl, results, build_ok=True, phases=phases)
        rest = [t for t in selected if t not in first]
        print(f"full-suite phase: {len(rest)} remaining tests")
        more = run_batch(binary, rest, out, args.jobs, args.waves, args.timeout)
        phases.append({"phase": "full_suite", "tests": rest})
        _print(more)
        results += more
    else:
        print(f"running {len(selected)} tests on {min(args.jobs, len(selected))} workers")
        results = run_batch(binary, selected, out, args.jobs, args.waves, args.timeout)
        phases.append({"phase": "full_suite", "tests": selected})
        _print(results)

    return _write(out, rtl, results, build_ok=True, phases=phases)


def _print(results: list[dict]) -> None:
    for r in results:
        line = f"  {r['status']:4}  {r['test']:26} {r['seconds']:6.2f}s"
        if r["status"] != "PASS":
            line += f"  {r['detail']}"
        print(line)


def _write(out: Path, rtl: list[Path], results: list[dict], build_ok: bool,
           phases: list[dict]) -> int:
    n_fail = sum(r["status"] != "PASS" for r in results)
    ver = subprocess.run(["verilator", "--version"], capture_output=True, text=True).stdout.strip()
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "simulator": ver,
        "rtl": [str(p) for p in rtl],
        "level": "gate" if any(p.name.endswith(".netlist.v") for p in rtl) else "rtl",
        "build_ok": build_ok,
        "phases": phases,
        "total": len(results),
        "passed": len(results) - n_fail,
        "failed": n_fail,
        "results": results,
    }
    (out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    if build_ok:
        print(f"{summary['passed']}/{summary['total']} passed — {out / 'results.json'}")
    return 0 if build_ok and n_fail == 0 else (2 if not build_ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
