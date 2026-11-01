"""NEE Repository Acceptance Suites & 2026+ Tiered Verification Architecture.

Implements the Tiered Testing Trophy across 5 primary verification tiers:
- tests/unit: pure, isolated, provider-free unit tests with mock boundaries.
- tests/integration: cross-subsystem, storage, concurrency, and inter-process tests.
- tests/e2e: complete pipeline lifecycles, multi-agent workflows, and judge calibrations.
- tests/governance: architectural invariant shields, compliance gates, and policy enforcements.
- tests/benchmarks: latency, performance budgets, and memory stability soak tests.

Provides 100% backward-compatible dynamic module resolution via PEP-562 __getattr__
and package __path__ extension, ensuring that historical flat imports (e.g.,
`tests.test_governance_rules` or `from tests import test_governance_rules`) resolve
transparently to their tiered location.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, List

_TESTS_ROOT = Path(__file__).resolve().parent

# Canonical verification tiers and legacy subpackages
TIER_SUBDIRS: list[str] = [
    "unit",
    "property",
    "fakes",
    "integration",
    "end_to_end",
    "adversarial",
    "governance",
    "benchmarks",
    "concurrency",
    "l0",
    "retrieval",
    "handoffs",
    "characterization",
    "agent_framework",
    "security",
]

# Dynamically extend package __path__ so that Python import machinery
# can resolve `import tests.<module_name>` even if located in a subpackage.
for _subdir in TIER_SUBDIRS:
    _p = _TESTS_ROOT / _subdir
    if _p.is_dir() and str(_p) not in __path__:
        __path__.append(str(_p))


def _find_tiered_module(name: str) -> tuple[str, str] | None:
    """Scan tier subdirectories for a matching test module file."""
    # Check direct file in root first
    if (_TESTS_ROOT / f"{name}.py").is_file():
        return ("", f"tests.{name}")

    # Check tier subdirectories
    for tier in TIER_SUBDIRS:
        cand = _TESTS_ROOT / tier / f"{name}.py"
        if cand.is_file():
            return (tier, f"tests.{tier}.{name}")
    return None


def __getattr__(name: str) -> Any:
    """PEP-562 dynamic attribute lookup for backward-compatible test module resolution."""
    found = _find_tiered_module(name)
    if found is not None:
        tier, full_mod_path = found
        mod = importlib.import_module(full_mod_path)
        # Register in sys.modules under both flat and tiered paths for seamless interop
        flat_path = f"tests.{name}"
        if flat_path not in sys.modules:
            sys.modules[flat_path] = mod
        globals()[name] = mod
        return mod

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> List[str]:
    """Expose all discoverable test modules across root and tier subdirectories."""
    modules: set[str] = set(globals().keys())
    for p in _TESTS_ROOT.glob("test_*.py"):
        modules.add(p.stem)
    for tier in TIER_SUBDIRS:
        tier_dir = _TESTS_ROOT / tier
        if tier_dir.is_dir():
            modules.add(tier)
            for p in tier_dir.glob("test_*.py"):
                modules.add(p.stem)
    return sorted(modules)
