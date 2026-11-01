#!/usr/bin/env python3
"""
tools/tier2_execute_dedup.py (DEPRECATED)

Retired in favor of `nee graph-consolidate {scan,plan,release}`.
"""
from __future__ import annotations

import sys
import warnings


class Tier2Executor:
    """Deprecated shim for legacy callers."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        warnings.warn(
            "Tier2Executor is deprecated and retired. Use `nee graph-consolidate` instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def run(self, *args: object, **kwargs: object) -> int:
        print(
            "ERROR: Tier2Executor has been retired under the Governed Graph Consolidation Engine.\n"
            "Please use the governed entry point:\n"
            "  nee graph-consolidate scan\n"
            "  nee graph-consolidate release --approval-id '<approval>'\n",
            file=sys.stderr,
        )
        return 1


def main() -> int:
    warnings.warn(
        "tools/tier2_execute_dedup.py is deprecated and scheduled for removal. "
        "Use `nee graph-consolidate` or `python -m dual_pipeline.cli graph-consolidate` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    print(
        "ERROR: tier2_execute_dedup.py has been retired under the Governed Graph Consolidation Engine.\n"
        "Please use the governed entry point:\n"
        "  nee graph-consolidate scan\n"
        "  nee graph-consolidate release --approval-id '<approval>'\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
