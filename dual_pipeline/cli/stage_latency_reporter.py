"""
dual_pipeline/cli/stage_latency_reporter.py

Extracts and reports per-stage wall-clock latency (U0, L1, L2, Judge, Total)
from OpenTelemetry trace logs and pipeline execution receipts.
Grounded in Wave 4 of INGEST_PNG_LATENCY_AND_APPROVAL_GATE_WAVE_PLAN.md.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("nee.cli.latency")


def _read_recent_trace_lines(t_dir: Path, max_lines: int = 400) -> list[str]:
    if not t_dir.is_dir():
        return []
    t_files = sorted(t_dir.glob("traces_*.jsonl"))
    if not t_files:
        return []
    try:
        raw = t_files[-1].read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return []
        lines = raw.splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _extract_from_spans(lines: list[str]) -> Optional[Dict[str, Any]]:
    run_span = None
    for line in reversed(lines):
        try:
            d = json.loads(line)
            if d.get("name") == "pipeline.run":
                run_span = d
                break
        except Exception:
            continue

    if not run_span:
        return None

    tid = run_span.get("trace_id") or ""
    total_ms = float(run_span.get("duration_ms") or 0.0)
    u0_ms = l1_ms = l2_ms = judge_ms = 0.0

    for line in reversed(lines):
        try:
            d = json.loads(line)
            if d.get("trace_id") != tid:
                continue
            name = d.get("name")
            dur = float(d.get("duration_ms") or 0.0)
            if name == "stage.U0" and not u0_ms:
                u0_ms = dur
            elif name == "stage.L1" and not l1_ms:
                l1_ms = dur
            elif name == "stage.L2" and not l2_ms:
                l2_ms = dur
            elif name == "child.judge" and not judge_ms:
                judge_ms = dur
        except Exception:
            continue

    return {
        "trace_id": tid,
        "U0": round(u0_ms / 1000.0, 2),
        "L1": round(l1_ms / 1000.0, 2),
        "L2": round(l2_ms / 1000.0, 2),
        "judge": round(judge_ms / 1000.0, 2),
        "total": round(total_ms / 1000.0, 2),
    }


def _extract_from_receipt(receipt_path_val: Any) -> Optional[Dict[str, Any]]:
    if not receipt_path_val or not Path(str(receipt_path_val)).is_file():
        return None
    try:
        data = json.loads(Path(str(receipt_path_val)).read_text(encoding="utf-8"))
        stages = data.get("stages", {})
        u0_s = round((stages.get("U0", {}).get("duration_ms") or 0.0) / 1000.0, 2)
        l1_s = round((stages.get("L1", {}).get("duration_ms") or 0.0) / 1000.0, 2)
        l2_s = round((stages.get("L2", {}).get("duration_ms") or 0.0) / 1000.0, 2)
        tb = data.get("timing_breakdown", {})
        judge_s = round(tb.get("child_spans_s", {}).get("judge", 0.0), 2)
        total_s = round(float(data.get("monotonic_duration_s") or sum([u0_s, l1_s, l2_s, judge_s])), 2)
        return {
            "trace_id": data.get("correlation", {}).get("trace_id") or "receipt",
            "U0": u0_s,
            "L1": l1_s,
            "L2": l2_s,
            "judge": judge_s,
            "total": total_s,
        }
    except Exception:
        return None


def extract_stage_latencies(
    result: Dict[str, Any], traces_dir: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Extracts per-stage wall-clock seconds from OTel traces, falling back to receipt."""
    from dual_pipeline.core import ROOT
    t_dir = traces_dir or (ROOT / ".local" / "telemetry")
    lines = _read_recent_trace_lines(t_dir)
    latencies = _extract_from_spans(lines) if lines else None
    if not latencies:
        latencies = _extract_from_receipt(result.get("execution_receipt"))
    return latencies


def format_latency_table(latencies: Dict[str, Any]) -> str:
    """Formats per-stage latencies into a standardized summary table."""
    return (
        f"\nStage Latency Breakdown:\n"
        f"  U0 (Ingress):   {latencies['U0']:.1f}s\n"
        f"  L1 (Identity):  {latencies['L1']:.1f}s\n"
        f"  L2 (Synthesis): {latencies['L2']:.1f}s\n"
        f"  Judge:          {latencies['judge']:.1f}s\n"
        f"  Total:          {latencies['total']:.1f}s"
    )


def report_stage_latencies(
    result: Dict[str, Any],
    traces_dir: Optional[Path] = None,
    stream: Any = None,
) -> Optional[Dict[str, Any]]:
    """Prints per-stage wall-clock seconds from OTel spans and attaches to result."""
    latencies = extract_stage_latencies(result, traces_dir=traces_dir)
    if not latencies:
        return None

    if result.get("status") == "PACKAGE_SEALED" or result.get("attach_latencies"):
        result["stage_latencies"] = latencies
    table = format_latency_table(latencies)
    logger.info(table.strip())
    out_stream = stream or sys.stderr
    print(table, file=out_stream, flush=True)
    return latencies
