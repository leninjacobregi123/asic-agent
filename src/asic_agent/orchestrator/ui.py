"""Approval gates and decisions for a run: terminal, --auto-approve, or web."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


class UI:
    """Approval gates and decisions. Three modes:
    terminal (input()), --auto-approve (printed, not asked), and --web: the
    question is written to runs/<id>/gate.json and the driver waits for the
    browser's answer in gate_answer.json (app/server.py). The gate is never
    skipped silently in any mode."""

    def __init__(self, auto_approve: bool, web: bool = False):
        self.auto_approve = auto_approve
        self.web = web
        self.gate_dir: Path | None = None
        self._n = 0

    def stage(self, name: str, detail: str = "") -> None:
        print(f"\n{'=' * 72}\n  STAGE: {name}" + (f"   {detail}" if detail else "")
              + f"\n{'=' * 72}", flush=True)

    def info(self, msg: str) -> None:
        print(f"  {msg}", flush=True)

    def _ask_web(self, q: dict) -> dict | None:
        """Post a question for the browser and block until it is answered."""
        assert self.gate_dir is not None
        self._n += 1
        q = {**q, "id": self._n, "asked": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        ans_path = self.gate_dir / "gate_answer.json"
        ans_path.unlink(missing_ok=True)
        tmp = self.gate_dir / "gate.json.tmp"
        tmp.write_text(json.dumps(q))
        tmp.replace(self.gate_dir / "gate.json")
        print(f"  [WAITING] {q['title']} — answer in the browser", flush=True)
        while True:
            if ans_path.exists():
                try:
                    a = json.loads(ans_path.read_text())
                except json.JSONDecodeError:
                    time.sleep(0.2)
                    continue
                if a.get("id") == q["id"]:
                    ans_path.unlink(missing_ok=True)
                    (self.gate_dir / "gate.json").unlink(missing_ok=True)
                    return a
            time.sleep(0.5)

    def approve(self, action: str) -> bool:
        """The human approval gate. Never bypassed silently."""
        if self.auto_approve:
            print(f"  [APPROVAL] {action}  -> auto-approved (--auto-approve)", flush=True)
            return True
        if self.web:
            a = self._ask_web({"kind": "approve", "title": "Approve tool run", "action": action})
            ok = bool(a and a.get("approve"))
            print(f"  [APPROVAL] {action}  -> {'approved' if ok else 'declined'} in the browser"
                  + (f" by {a.get('by')}" if a and a.get("by") else ""), flush=True)
            return ok
        try:
            ans = input(f"  [APPROVAL] {action}\n             Run it? [y/N] ").strip().lower()
        except EOFError:
            print("  [APPROVAL] no TTY and --auto-approve not set -> declining")
            return False
        return ans in ("y", "yes")

    def choose(self, prompt: str, options: dict[str, str], default: str,
               context: dict | None = None) -> str | None:
        """A human decision between agent-proposed options (spec readings)."""
        for k, v in options.items():
            print(f"     ({k}) {v}")
        if self.auto_approve:
            print(f"  [DECISION] {prompt}  -> ({default}) agent's recommendation "
                  f"(--auto-approve)", flush=True)
            return default
        if self.web:
            a = self._ask_web({"kind": "choose", "title": prompt, "options": options,
                               "recommended": default, "context": context or {}})
            c = (a or {}).get("choice")
            c = c if c in options else None
            print(f"  [DECISION] {prompt}  -> " + (f"({c}) chosen in the browser" if c
                                                   else "escalated in the browser"), flush=True)
            return c
        try:
            ans = input(f"  [DECISION] {prompt} [{'/'.join(options)}, default {default}, "
                        f"n = escalate] ").strip().lower() or default
        except EOFError:
            return None
        return ans if ans in options else None
