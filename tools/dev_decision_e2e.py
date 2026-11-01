#!/usr/bin/env python3
"""Run the canonical DecisionService through the public ``nee ask`` path.

This wrapper deliberately contains no retrieval, routing, or decision logic.
Every argument after the script name is forwarded to the public CLI so local
E2E execution cannot drift from the installed entrypoint.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["ask", *sys.argv[1:]]))
