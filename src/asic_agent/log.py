"""Logging setup shared by every entry point.

The pipeline's user-facing narration (stages, approvals) is printed by the
orchestrator's UI and captured in each run's console log. This module is for
operational logging: provider calls, retries, service events, errors. Level
from ASIC_AGENT_LOG_LEVEL (default INFO); output to stderr.
"""

from __future__ import annotations

import logging
import os

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup(name: str = "asic_agent") -> logging.Logger:
    level = os.environ.get("ASIC_AGENT_LOG_LEVEL", "INFO").upper()
    root = logging.getLogger("asic_agent")
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(_FMT, "%H:%M:%S"))
        root.addHandler(h)
    root.setLevel(getattr(logging, level, logging.INFO))
    return logging.getLogger(name)


def get(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("asic_agent") else f"asic_agent.{name}")
