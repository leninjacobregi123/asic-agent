"""
Convert a PULP IP reference spreadsheet (APB_*_reference.xlsx) to Markdown.

The chunker only indexes text formats, and for most PULP peripherals the
register-level spec lives only in this spreadsheet. This turns it into one
Markdown section per register, so each register retrieves as its own chunk.

Text is copied verbatim: typos, copy-paste errors and contradictions in the
source are kept. They are real properties of the spec the agents work from,
and the spec agent is expected to find them — silently tidying them here
would hide exactly the ambiguities diagnose-and-route exists to catch.

Usage:
    python -m asic_agent.knowledge.xlsx_to_md designs/apb_gpio/docs/APB_GPIO_reference.xlsx \
        --out designs/apb_gpio/docs/apb_gpio_regs.md
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path



def _sheet(wb, prefix: str):
    for ws in wb:
        if ws.title.startswith(prefix):
            return ws
    return None


def _rows(ws) -> list[dict]:
    rows = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    if not rows:
        return []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    out = []
    for r in rows[1:]:
        out.append({h: ("" if v is None else str(v).strip()) for h, v in zip(header, r) if h})
    return out


def _norm(name: str) -> str:
    return name.upper().replace("_", "")


def convert(xlsx: Path) -> str:
    try:
        import openpyxl   # optional: only this converter needs it
    except ImportError:
        sys.exit("openpyxl is required for spreadsheet specs: pip install openpyxl")
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    parts: list[str] = []

    title = xlsx.stem.replace("_reference", "").replace("_", " ")
    parts.append(f"# {title} register reference\n")
    parts.append(
        f"Generated from `{xlsx.name}` by `rag/xlsx_to_md.py`. Text is verbatim "
        "from the spreadsheet, including its errors.\n"
    )

    descr = _sheet(wb, "IPDESCR")
    if descr is not None:
        text = "\n".join(
            str(c) for r in descr.iter_rows(values_only=True) for c in r if c is not None
        )
        parts.append("## Overview\n")
        parts.append(text.removeprefix("Description").strip() + "\n")

    reglist = _sheet(wb, "IPREGLIST")
    regmap = _sheet(wb, "IPREGMAP")
    regs = _rows(reglist) if reglist is not None else []
    fields = _rows(regmap) if regmap is not None else []

    if regs:
        parts.append("## Register map\n")
        parts.append("| Register | Offset | Access | Reset | Description |")
        parts.append("|---|---|---|---|---|")
        for r in regs:
            parts.append(
                f"| {r.get('Register Name','')} | {r.get('Address','')} | "
                f"{r.get('Host Access Type','')} | {r.get('Default Value','')} | "
                f"{r.get('Description','')} |"
            )
        parts.append("")

    # Bit fields are keyed by the register column of IPREGMAP, which in the
    # source does not always match IPREGLIST spelling (e.g. "INTTYPE0" vs
    # "INTTYPE_00_15"). Match exactly first; unmatched fields are listed
    # under their own heading rather than guessed onto a register.
    by_reg: dict[str, list[dict]] = defaultdict(list)
    for f in fields:
        by_reg[_norm(f.get("Register", ""))].append(f)

    reg_keys = {_norm(r.get("Register Name", "")) for r in regs}
    inferred: dict[str, list[dict]] = defaultdict(list)
    for key in [k for k in by_reg if k not in reg_keys]:
        for f in list(by_reg[key]):
            target = _infer_register(f, regs)
            if target is not None:
                inferred[_norm(target)].append(f)
                by_reg[key].remove(f)

    used: set[str] = set()
    for r in regs:
        name = r.get("Register Name", "")
        key = _norm(name)
        parts.append(f"### {name}\n")
        parts.append(
            f"Offset {r.get('Address','')}, {r.get('Size','')} bits, "
            f"host access {r.get('Host Access Type','')}, "
            f"reset value {r.get('Default Value','')}. {r.get('Description','')}\n"
        )
        for f in by_reg.get(key, []):
            parts.append(_field(f))
        for f in inferred.get(key, []):
            parts.append(_field(f, inferred_for=name))
        used.add(key)

    leftovers = [f for k, fs in by_reg.items() if k not in used for f in fs]
    if leftovers:
        parts.append("### Bit fields with no matching register name\n")
        parts.append(
            "These rows in the spreadsheet name a register that does not appear "
            "in the register list, so which offset they describe is not stated.\n"
        )
        for f in leftovers:
            parts.append(_field(f))

    return "\n".join(parts).rstrip() + "\n"


def _infer_register(f: dict, regs: list[dict]) -> str | None:
    """Place a bit field whose register name is not in the register list.

    Only when two independent pieces of the source agree: the field's register
    name is a prefix of exactly one listed register (ignoring a trailing bank
    digit, e.g. INTTYPE0), and that register's name suffix `_lo_hi` equals the
    GPIO[hi:lo] range the field's own description states. Anything else stays
    unplaced — a wrong guess here would be retrieved as authoritative.
    """
    import re

    m = re.search(r"GPIO\[(\d+):(\d+)\]", f.get("Description", ""))
    if not m:
        return None
    hi, lo = int(m.group(1)), int(m.group(2))
    stem = re.sub(r"\d+$", "", _norm(f.get("Register", "")))
    hits = []
    for r in regs:
        name = r.get("Register Name", "")
        rm = re.match(r"(.+)_(\d+)_(\d+)$", name)
        if rm and _norm(rm.group(1)) == stem and (int(rm.group(2)), int(rm.group(3))) == (lo, hi):
            hits.append(name)
    return hits[0] if len(hits) == 1 else None


def _field(f: dict, inferred_for: str | None = None) -> str:
    pos, size = f.get("Bit Position", ""), f.get("Size", "")
    head = (
        f"- **{f.get('Bit field','')}** (register `{f.get('Register','')}`, "
        f"bit {pos}, width {size}, host {f.get('Host Access Type','') or '-'}, "
        f"reset {f.get('Reset Value','') or '-'}):"
    )
    body = f.get("Description", "").replace("\n", "\n  ")
    note = (
        f"\n  _(The spreadsheet names this register `{f.get('Register','')}`; placed "
        f"under {inferred_for} by name prefix and the GPIO range above.)_"
        if inferred_for else ""
    )
    return f"{head}\n  {body}{note}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.write_text(convert(args.xlsx))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
