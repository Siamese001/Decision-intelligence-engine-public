#!/usr/bin/env python3
"""Stage-level profiler and SLA benchmark analyzer for QueryEngine (Wave 10).

Wraps QueryEngine execution with microsecond timing instrumentation across all 14 stages,
evaluates against config/performance/wave_4_benchmarks.yaml, and emits human-readable
dashboards and machine-readable query_perf_profile.json receipts.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json, write_json  # noqa: E402

logger = logging.getLogger("query_profiler")

QUERY_STAGES = [
    "_stage_validate_and_normalize",
    "_stage_resolve_routing",
    "_stage_initialize_run",
    "_stage_determine_mode",
    "_stage_check_cache",
    "_stage_baseline_retrieval",
    "_stage_coverage_augmentation",
    "_stage_prepare_l2",
    "_stage_execute_l2",
    "_stage_l1_post_l2_review",
    "_stage_challenger_review",
    "_stage_l2d_and_repair",
    "_stage_finalize_gates",
    "_stage_build_receipt",
]

DEFAULT_STAGE_BUDGETS_MS: Dict[str, float] = {
    "_stage_validate_and_normalize": 100.0,
    "_stage_resolve_routing": 5000.0,
    "_stage_initialize_run": 100.0,
    "_stage_determine_mode": 3000.0,
    "_stage_check_cache": 500.0,
    "_stage_baseline_retrieval": 7500.0,
    "_stage_coverage_augmentation": 2000.0,
    "_stage_prepare_l2": 1000.0,
    "_stage_execute_l2": 30000.0,
    "_stage_l1_post_l2_review": 1000.0,
    "_stage_challenger_review": 10000.0,
    "_stage_l2d_and_repair": 1000.0,
    "_stage_finalize_gates": 500.0,
    "_stage_build_receipt": 500.0,
}


@dataclass
class StageProfile:
    stage_name: str
    duration_ms: float
    budget_ms: float
    status: str  # PASS, WARN, FAIL
    percentage_of_total: float


@dataclass
class QueryProfileReceipt:
    timestamp: str
    question: str
    query_type: Optional[str]
    total_duration_ms: float
    retrieval_duration_ms: float
    overall_status: str  # PASS, FAIL
    stages: List[StageProfile] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_sla_budgets(benchmarks_yaml_path: Optional[Path] = None) -> Dict[str, float]:
    """Loads stage budgets from wave_4_benchmarks.yaml or default fallbacks."""
    budgets = dict(DEFAULT_STAGE_BUDGETS_MS)
    if benchmarks_yaml_path is None:
        benchmarks_yaml_path = ROOT / "config" / "performance" / "wave_4_benchmarks.yaml"

    if benchmarks_yaml_path.is_file():
        try:
            import yaml
            with open(benchmarks_yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            retrieval_cfg = data.get("retrieval_benchmarks", {})
            hard_ceiling = retrieval_cfg.get("hard_ceiling_p95_ms", 7500.0)
            budgets["_stage_baseline_retrieval"] = float(hard_ceiling)
        except Exception as exc:
            logger.warning(f"Could not parse benchmarks YAML: {exc}; using default budgets")
    return budgets


def profile_query_engine(
    engine: Any,
    question: str,
    *,
    query_type: Optional[str] = None,
    budgets: Optional[Dict[str, float]] = None,
    **ask_kwargs: Any,
) -> tuple[Dict[str, Any], QueryProfileReceipt]:
    """
    Executes QueryEngine.ask() with stage-level timing wraps and generates a receipt.
    """
    if budgets is None:
        budgets = load_sla_budgets()

    stage_timings: Dict[str, float] = {}
    original_methods: Dict[str, Callable] = {}

    for stage_name in QUERY_STAGES:
        if hasattr(engine, stage_name):
            original = getattr(engine, stage_name)
            original_methods[stage_name] = original

            def _make_wrapper(s_name: str, orig_fn: Callable) -> Callable:
                def _timed_stage(state: Any) -> None:
                    t0 = time.monotonic()
                    try:
                        orig_fn(state)
                    finally:
                        stage_timings[s_name] = (time.monotonic() - t0) * 1000.0
                return _timed_stage

            setattr(engine, stage_name, _make_wrapper(stage_name, original))

    t_start = time.monotonic()
    try:
        result = engine.ask(question, query_type=query_type, **ask_kwargs)
    finally:
        total_wall_ms = (time.monotonic() - t_start) * 1000.0
        for s_name, orig_fn in original_methods.items():
            setattr(engine, s_name, orig_fn)

    profiles: List[StageProfile] = []
    overall_pass = True

    for s_name in QUERY_STAGES:
        dur = stage_timings.get(s_name, 0.0)
        budget = budgets.get(s_name, 5000.0)
        pct = (dur / total_wall_ms * 100.0) if total_wall_ms > 0 else 0.0

        if dur > budget:
            status = "FAIL"
            overall_pass = False
        elif dur > budget * 0.8:
            status = "WARN"
        else:
            status = "PASS"

        profiles.append(
            StageProfile(
                stage_name=s_name,
                duration_ms=round(dur, 2),
                budget_ms=budget,
                status=status,
                percentage_of_total=round(pct, 1),
            )
        )

    retrieval_dur = stage_timings.get("_stage_baseline_retrieval", 0.0)
    receipt = QueryProfileReceipt(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        question=question,
        query_type=query_type,
        total_duration_ms=round(total_wall_ms, 2),
        retrieval_duration_ms=round(retrieval_dur, 2),
        overall_status="PASS" if overall_pass and retrieval_dur <= 7500.0 else "FAIL",
        stages=profiles,
        metadata={"query_id": result.get("query_id") if isinstance(result, dict) else None},
    )

    return result, receipt


def render_profile_table(receipt: QueryProfileReceipt) -> str:
    """Formats the profile receipt into an aligned ASCII dashboard."""
    lines = [
        "=" * 82,
        "  QUERY PIPELINE LATENCY PROFILE & SLA VERIFICATION",
        "=" * 82,
        f"  Question:     {receipt.question[:60]}",
        f"  Query Type:   {receipt.query_type or 'automatic'}",
        f"  Total Time:   {receipt.total_duration_ms:.2f} ms",
        f"  Retrieval:    {receipt.retrieval_duration_ms:.2f} ms (Budget: 7,500.0 ms)",
        f"  SLA Status:   [{receipt.overall_status}]",
        "-" * 82,
        f"  {'Stage Name':<36} | {'Duration (ms)':>13} | {'Budget (ms)':>11} | {'Share':>6} | {'Status':>6}",
        "-" * 82,
    ]
    for sp in receipt.stages:
        clean_name = sp.stage_name.replace("_stage_", "")
        lines.append(
            f"  {clean_name:<36} | {sp.duration_ms:>13.2f} | {sp.budget_ms:>11.1f} | {sp.percentage_of_total:>5.1f}% | [{sp.status}]"
        )
    lines.append("=" * 82)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile QueryEngine and enforce SLA budgets")
    parser.add_argument("--runtime-root", default=str(ROOT / ".local" / "dual_pipeline"))
    parser.add_argument("--question", default="What is the architecture of Project Nexus?")
    parser.add_argument("--query-type", default=None)
    parser.add_argument("--output-receipt", default="reports/query_perf_profile.json")
    parser.add_argument("--assert-sla", action="store_true", help="Fail if any SLA is violated")
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.is_file():
        logger.error(f"Active pair not found at {active_pair_path}")
        return 1

    pair = read_json(active_pair_path)
    from dual_pipeline.cli.engine import _backend  # noqa: lazy-decouple
    from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: lazy-decouple
    from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config
    from dual_pipeline.query import QueryEngine

    cli_args = argparse.Namespace(
        runtime_root=runtime_root,
        model_path="BAAI/bge-m3",  # noqa: SSOT_EXEMPT
        model_manifest=None,
        model_revision=None,
        embedding_backend=pair.get("backend", "bge-m3-dev"),
        device="cpu",
        fp16=False,
        assurance_tier="internal_operational",
        allow_dev_bge_m3=True,
        allow_internal_operational=False,
        internal_operational_policy=None,
    )
    auto_resolve_backend_config(cli_args)
    backend = _backend(cli_args)
    l2_config = load_l2_config()
    graph_path = runtime_root / pair["graph_path"]

    engine = QueryEngine(
        runtime_root,
        graph_path,
        backend,
        provider_factory=OpenAIProviderFactory(l2_config),
        l2_config=l2_config,
    )
    engine.mock_mode = True

    _, receipt = profile_query_engine(engine, args.question, query_type=args.query_type)
    table_str = render_profile_table(receipt)
    print(table_str)

    if args.output_receipt:
        out_path = Path(args.output_receipt).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(out_path, receipt.to_dict())
        print(f"\n[RECEIPT] Saved profile receipt to {out_path}")

    if args.assert_sla and receipt.overall_status != "PASS":
        print("[FAIL] SLA verification failed!")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
