"""
Metric Historian (A8) for Graph Freshness (Wave 5).
Persists hygiene metrics into a durable JSONL ledger for trend analysis.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional


class MetricHistorian:
    """A8: Records metric snapshots to track graph hygiene trends over time."""

    def __init__(self, integrator: Optional[Any] = None) -> None:
        self.integrator = integrator

    def record(self, metrics: Dict[str, Any], history_file: Path, run_id: Optional[str] = None) -> None:
        """Appends a timestamped snapshot of the metrics to the history ledger."""
        snapshot = {
            "timestamp": int(time.time()),
            "metrics": metrics
        }

        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "a") as f:
            f.write(json.dumps(snapshot) + "\n")

        if self.integrator:
            from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
            evt = AgentOutcomeAdapter.build_maintenance_finding_event(
                finding_type="GRAPH_HYGIENE_METRICS",
                entity_ids=[],
                description=f"Recorded graph hygiene snapshot: {list(metrics.keys())}",
                source_agent="MetricHistorian",
                run_id=run_id,
            )
            self.integrator.record_outcome(evt)
