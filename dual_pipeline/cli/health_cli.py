"""
dual_pipeline/cli/health_cli.py

CLI handler for 'nee backtest-health'.
Audits graph health, counts ghost duplicate assertions, orphaned evidence,
and calculates retrieval index bloat ratios.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.core import ROOT
from dual_pipeline.graph_maintenance_core.health import (
    calculate_graph_health_metrics,
    render_health_report_markdown,
)
from storage.connection import create_sqlite_conn

logger = logging.getLogger(__name__)


def _backtest_health(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute backtest-health CLI command."""
    graph_arg = getattr(args, "graph", None)
    if graph_arg:
        graph_path = Path(graph_arg).expanduser().resolve()
    else:
        graph_path = ROOT / "graph" / "nee_graph.sqlite"

    if not graph_path.is_file():
        raise FileNotFoundError(f"Graph database file not found: {graph_path}")

    conn = create_sqlite_conn(graph_path, readonly=True)
    metrics = calculate_graph_health_metrics(conn)
    conn.close()

    fmt = getattr(args, "format", "markdown")
    if fmt == "json":
        print(json.dumps(metrics, indent=2))
    else:
        md = render_health_report_markdown(metrics, target_graph=str(graph_path))
        print("\n" + md + "\n")

    if getattr(args, "recommend_gc", False) and metrics["needs_gc"]:
        print("⚠️ Graph health check recommends running 'python tools/graph_gc.py' to clean up bloat.")

    return metrics
