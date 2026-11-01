#!/usr/bin/env python3
"""
tools/snapshot_agent_learning_registry.py

Scans and snapshots all agent, coordinator, and declared role definitions
across intake, extraction, retrieval, decision, freshness, hygiene, and swarms.
Establishes the empirical baseline for agent learning coverage and autonomy.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.snapshot_agent_learning_registry_core.primary_components import (
    PRIMARY_COMPONENTS,
)
from tools.snapshot_agent_learning_registry_core.supporting_components import (
    ENTRY_POINTS,
    SUPPORTING_COMPONENTS,
)
OUTPUT_PATH = ROOT / "reports" / "agent_learning_role_registry_baseline.json"


def build_registry_snapshot() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    components: List[Dict[str, Any]] = list(PRIMARY_COMPONENTS) + list(
        SUPPORTING_COMPONENTS
    )

    total_components = len(components)
    total_declared_roles = sum(c["declared_roles_count"] for c in components)
    durable_count = sum(1 for c in components if c["has_durable_learning"])
    within_run_count = sum(
        1 for c in components if c["adaptation_scope"] == "within_run"
    )
    none_or_simulated_count = sum(
        1 for c in components if c["adaptation_scope"] in ("none", "simulated")
    )

    entry_point_counts: Dict[str, int] = {}
    for c in components:
        ep = c.get("target_entry_point")
        if ep:
            entry_point_counts[ep] = entry_point_counts.get(ep, 0) + 1

    return {
        "metadata": {
            "snapshot_id": "snap-agent-learning-registry-20261007",
            "created_at": now,
            "branch": "feat/agent-learning-opportunities-plan",
            "baseline_date": "2026-10-07",
            "governance_standard": "AGENTS.md Wave 0 Baseline",
        },
        "statistics": {
            "total_registered_components": total_components,
            "total_declared_roles": total_declared_roles,
            "components_with_durable_learning": durable_count,
            "components_with_within_run_adaptation": within_run_count,
            "components_with_static_or_simulated": none_or_simulated_count,
            "durable_learning_coverage_pct": round(
                (durable_count / total_components) * 100.0, 2
            ),
            "mapped_real_entry_points_count": len(entry_point_counts),
            "entry_point_distribution": entry_point_counts,
        },
        "entry_points": ENTRY_POINTS,
        "components": components,
    }


def main() -> int:
    snapshot = build_registry_snapshot()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    print(f"[OK] Wrote agent learning registry baseline snapshot to {OUTPUT_PATH}")
    stats = snapshot["statistics"]
    print(f"     Total components: {stats['total_registered_components']}")
    print(f"     Total declared roles: {stats['total_declared_roles']}")
    print(
        f"     Durable learning: {stats['components_with_durable_learning']} ({stats['durable_learning_coverage_pct']}%)"
    )
    print(
        f"     Within-run adaptation: {stats['components_with_within_run_adaptation']}"
    )
    print(f"     Static or simulated: {stats['components_with_static_or_simulated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
