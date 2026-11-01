#!/usr/bin/env python3
"""
scripts/live_audio_transcriber.py

DEPRECATED: Use the canonical CLI entrypoint instead:
    python nee.py record-meeting [OPTIONS]

This script is maintained exclusively as a backward-compatible shim delegating
directly to `dual_pipeline.cli.main(["record-meeting", ...])`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

venv_bin = str(ROOT / ".venv" / "bin")
if venv_bin not in os.environ.get("PATH", "").split(":"):
    os.environ["PATH"] = venv_bin + ":" + os.environ.get("PATH", "")


def main() -> int:
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] scripts/live_audio_transcriber.py has been decommissioned.\n"
        "Direct script execution is strictly prohibited by Master Invariant 10.\n"
        "Use the canonical CLI entrypoint instead: 'python nee.py record-meeting ...'\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
