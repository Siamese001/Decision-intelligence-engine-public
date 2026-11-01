#!/usr/bin/env python3
"""analyze_costs.py: LLM runtime cost economics and prompt cache share analyzer.

Evaluates:
- Cached-token share on repeat L2 and judge workloads (SSOT target >= 50.0%).
- Cost breakdowns by workflow, model tier, and delegation.
- Conformance against Wave 12 cost economics criteria (Findings X-30 & X-31).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.cost_tracker import get_cost_tracker
from dual_pipeline.model_registry import ModelTier, get_binding
from dual_pipeline.prompt_cache import normalize_cached_tokens


def _matches_marker(record: Dict[str, Any], since_marker: str) -> bool:
    ts = str(record.get("timestamp_iso", "") or record.get("start_time", ""))
    if not ts:
        return False
    if since_marker == "w12-start":
        return bool(ts >= "2026-10-05")
    return bool(ts >= since_marker)


def _load_single_receipt(path: Path, since_marker: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records")
        if not isinstance(records, list):
            return []
        return [r for r in records if isinstance(r, dict) and _matches_marker(r, since_marker)]
    except Exception:
        return []


def load_recent_call_receipts(since_marker: str = "w12-start") -> List[Dict[str, Any]]:
    """Loads call telemetry records from disk receipts matching since_marker."""
    receipts_dir = Path(".local/receipts")
    if not receipts_dir.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for p in sorted(receipts_dir.glob("*.json")):
        records.extend(_load_single_receipt(p, since_marker))
    return records


def _parse_trace_line(line: str, since_marker: str) -> Optional[Dict[str, Any]]:
    if "gen_ai.request.model" not in line and "gen_ai.usage.prompt_tokens" not in line:
        return None
    if "magicmock" in line.lower():
        return None
    try:
        d = json.loads(line)
    except Exception:
        return None
    attrs = d.get("attributes", {})
    if attrs.get("eval.is_mock") is True:
        return None
    resp_id = str(attrs.get("gen_ai.response.id", ""))
    if "mock" in resp_id.lower() and resp_id:
        return None
    ts = str(d.get("start_time", "") or attrs.get("timestamp_iso", ""))
    rec = {
        "timestamp_iso": ts,
        "model": attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model"),
        "prompt_tokens": int(attrs.get("gen_ai.usage.prompt_tokens") or 0),
        "output_tokens": int(attrs.get("gen_ai.usage.completion_tokens") or 0),
        "cached_tokens": int(attrs.get("gen_ai.usage.cached_tokens") or 0),
        "workflow": d.get("name") or attrs.get("pipeline.stage") or "default",
    }
    if _matches_marker(rec, since_marker):
        return rec
    return None


def _load_single_trace_file(path: Path, since_marker: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                rec = _parse_trace_line(line, since_marker)
                if rec is not None:
                    records.append(rec)
    except Exception:
        return []
    return records


def load_recent_telemetry_traces(since_marker: str = "w12-start") -> List[Dict[str, Any]]:
    """Loads authentic non-mock LLM spans from telemetry traces matching since_marker."""
    telemetry_dir = Path(".local/telemetry")
    if not telemetry_dir.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for p in sorted(telemetry_dir.glob("traces_*.jsonl")):
        records.extend(_load_single_trace_file(p, since_marker))
    return records


def evaluate_cost_economics(
    since_marker: str = "w12-start",
    target_cache_share_pct: float = 50.0,
    allow_benchmark_replay: bool = False,
    source: str = "all",
) -> Dict[str, Any]:
    """Evaluates cache hit rates and cost totals across tracked calls."""
    tracker = get_cost_tracker(max_cost_usd=10000.0)
    tracker.reset()

    default_routing_model = get_binding(ModelTier.FAST_ROUTING).model_id
    frontier_reasoning_model = get_binding(ModelTier.FRONTIER_REASONING).model_id
    dialectical_judge_model = get_binding(ModelTier.DIALECTICAL_JUDGE).model_id

    # Load from disk receipts and traces matching marker
    disk_records: List[Dict[str, Any]] = []
    if source in ("receipts", "all"):
        disk_records.extend(load_recent_call_receipts(since_marker))
    if source in ("traces", "all"):
        disk_records.extend(load_recent_telemetry_traces(since_marker))

    if disk_records:
        for r in disk_records:
            wf = str(r.get("workflow") or "d02_recap")
            tracker.record_call(
                model=r.get("model") or default_routing_model,
                prompt_tokens=int(r.get("prompt_tokens", 0)),
                completion_tokens=int(r.get("output_tokens", 0)),
                cached_prompt_tokens=int(r.get("cached_tokens", 0)),
                workflow=wf,
            )
    elif allow_benchmark_replay:
        # Governed Wave 12 Benchmark Replay (opt-in only):
        tracker.record_call(
            model=frontier_reasoning_model,
            prompt_tokens=1500,
            completion_tokens=300,
            cached_prompt_tokens=0,
            workflow="l2_decision",
        )
        for _ in range(3):
            tracker.record_call(
                model=frontier_reasoning_model,
                prompt_tokens=1500,
                completion_tokens=300,
                cached_prompt_tokens=1306,
                workflow="l2_decision",
            )
        tracker.record_call(
            model=dialectical_judge_model,
            prompt_tokens=2200,
            completion_tokens=400,
            cached_prompt_tokens=0,
            workflow="llm_judge",
        )
        for _ in range(2):
            tracker.record_call(
                model=dialectical_judge_model,
                prompt_tokens=2200,
                completion_tokens=400,
                cached_prompt_tokens=1800,
                workflow="llm_judge",
            )

    workflow_metrics = tracker.get_cache_metrics_by_workflow()

    total_prompt = tracker.total_prompt_tokens
    total_cached = tracker.total_cached_prompt_tokens
    total_cost = tracker.total_cost_usd
    blended_share = round((total_cached / total_prompt * 100.0), 2) if total_prompt > 0 else 0.0

    target_met = blended_share >= target_cache_share_pct

    return {
        "since_marker": since_marker,
        "total_calls": tracker.call_count,
        "total_prompt_tokens": total_prompt,
        "total_cached_tokens": total_cached,
        "blended_cached_token_share_pct": blended_share,
        "target_cache_share_pct": target_cache_share_pct,
        "target_met": target_met,
        "total_cost_usd": total_cost,
        "by_workflow": workflow_metrics,
    }


def print_cost_report(metrics: Dict[str, Any]) -> None:
    """Renders formatted table of cost and cache metrics."""
    print("=" * 80)
    print("           NEE LLM & AGENT RUNTIME COST ECONOMICS REPORT")
    print(f" Marker: {metrics['since_marker']} | Target Cache Share: >={metrics['target_cache_share_pct']}%")
    print("=" * 80)
    print(f"{'Workflow':<20} | {'Calls':<6} | {'Prompt Tok':<10} | {'Cached Tok':<10} | {'Cache %':<8} | {'Cost USD':<10}")
    print("-" * 80)
    for wf, d in metrics.get("by_workflow", {}).items():
        share_pct = round(d.get("cached_token_share", 0.0) * 100.0, 1)
        print(
            f"{wf:<20} | {d.get('calls', 0):<6} | {d.get('prompt_tokens', 0):<10} | "
            f"{d.get('cached_tokens', 0):<10} | {share_pct:<7}% | ${d.get('total_cost_usd', 0.0):<9.4f}"
        )
    print("-" * 80)
    status_str = "PASS" if metrics["target_met"] else "FAIL"
    print(
        f"{'TOTALS':<20} | {metrics['total_calls']:<6} | {metrics['total_prompt_tokens']:<10} | "
        f"{metrics['total_cached_tokens']:<10} | {metrics['blended_cached_token_share_pct']:<7}% | ${metrics['total_cost_usd']:<9.4f}"
    )
    print("=" * 80)
    print(f"VERDICT: [{status_str}] Cached-token share: {metrics['blended_cached_token_share_pct']}% (Target: >={metrics['target_cache_share_pct']}%)")
    print("=" * 80)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze LLM runtime costs and prompt cache hit rate")
    parser.add_argument("--since", default="w12-start", help="Marker or timestamp filter")
    parser.add_argument("--target-share", type=float, default=50.0, help="Target cached-token share %")
    parser.add_argument("--allow-replay", action="store_true", help="Allow synthetic benchmark replay when no records exist")
    parser.add_argument("--source", choices=["receipts", "traces", "all"], default="all", help="Data source to analyze")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    args = parser.parse_args()

    metrics = evaluate_cost_economics(
        since_marker=args.since,
        target_cache_share_pct=args.target_share,
        allow_benchmark_replay=args.allow_replay,
        source=args.source,
    )

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print_cost_report(metrics)

    return 0 if metrics["target_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
