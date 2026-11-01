"""
tools/test_discovery_core

Wave 5 Automated Test Discovery, Selection & Resolution Engine.
"""

from __future__ import annotations

from .discovery import (
    categorize_test_file,
    discover_all_test_files,
    resolve_all_tests,
)
from .quarantine import (
    get_quarantined_modules,
    load_quarantined_tests,
)
from .resolution_report import generate_resolution_report

__all__ = [
    "categorize_test_file",
    "discover_all_test_files",
    "resolve_all_tests",
    "get_quarantined_modules",
    "load_quarantined_tests",
    "generate_resolution_report",
]
