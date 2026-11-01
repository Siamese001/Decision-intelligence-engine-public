"""
tools/test_discovery_core/quarantine.py

Quarantined test registry with mandatory expiration validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Set

ROOT = Path(__file__).resolve().parents[2]
QUARANTINE_PATH = ROOT / "config" / "governance" / "quarantined_tests.json"


def load_quarantined_tests(path: Path | None = None) -> Dict[str, Dict[str, Any]]:
    """Loads quarantined tests and validates that none have expired."""
    p = path or QUARANTINE_PATH
    if not p.is_file():
        return {}

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

    now = datetime.now(timezone.utc)
    quarantined: Dict[str, Dict[str, Any]] = {}

    for item in data.get("tests", []):
        if not isinstance(item, dict):
            continue
        mod = item.get("module")
        if not mod:
            continue
        expires_at = item.get("expires_at")
        if expires_at:
            exp_date = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp_date.tzinfo is None:
                exp_date = exp_date.replace(tzinfo=timezone.utc)
            if exp_date < now:
                raise ValueError(
                    f"Quarantined test '{mod}' expired on {expires_at}. Fix or remove the test."
                )
        quarantined[mod] = item

    return quarantined


def get_quarantined_modules(path: Path | None = None) -> Set[str]:
    """Returns set of module names currently under valid quarantine."""
    return set(load_quarantined_tests(path).keys())
