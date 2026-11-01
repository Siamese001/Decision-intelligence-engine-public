#!/usr/bin/env python3
"""
scripts/audit_and_judge_project_nexus.py

DEPRECATED: Use the canonical CLI entrypoint instead:
    python nee.py ingest-meeting [OPTIONS]
or programmatic invocation via CanonicalAgenticPipeline / RawTranscriptPipeline.

Direct execution of this custom judge script is strictly prohibited by Master Invariant 10
and the Single Governed Entrypoint standard.
"""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] scripts/audit_and_judge_project_nexus.py has been decommissioned.\n"
        "Direct script execution is strictly prohibited by Master Invariant 10.\n"
        "Use the canonical CLI entrypoint instead: 'python nee.py ingest-meeting ...'\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
