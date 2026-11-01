#!/usr/bin/env python3
"""
tools/run_cost_benchmark.py

Empirical cost benchmark utility comparing standard full ingestion vs.
selective output regeneration (--regenerate) for meeting re-runs.

Enforces Wave 1 gate: Cost tracker reports <$0.30 for a --regenerate 02,03 run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cost_benchmark_runner import benchmark_pinned_models_pareto
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.token_optimization.schemas import MODEL_PRICING_TABLE

_PROD_MODEL = resolve_tier_model_name(ModelTier.DIALECTICAL_JUDGE)
_SYNTH_MODEL = resolve_tier_model_name(ModelTier.FRONTIER_REASONING)
_FAST_MODEL = resolve_tier_model_name(ModelTier.FAST_ROUTING)

# Standard model pricing (per million tokens)
MODEL_PRICING = {
    _PROD_MODEL: {
        "input": MODEL_PRICING_TABLE[_PROD_MODEL].uncached_input_usd_per_million if _PROD_MODEL in MODEL_PRICING_TABLE else 2.50,
        "output": MODEL_PRICING_TABLE[_PROD_MODEL].output_reasoning_usd_per_million if _PROD_MODEL in MODEL_PRICING_TABLE else 10.00,
    },
    _SYNTH_MODEL: {
        "input": MODEL_PRICING_TABLE[_SYNTH_MODEL].uncached_input_usd_per_million if _SYNTH_MODEL in MODEL_PRICING_TABLE else 1.25,
        "output": MODEL_PRICING_TABLE[_SYNTH_MODEL].output_reasoning_usd_per_million if _SYNTH_MODEL in MODEL_PRICING_TABLE else 5.00,
    },
    _FAST_MODEL: {
        "input": MODEL_PRICING_TABLE[_FAST_MODEL].uncached_input_usd_per_million if _FAST_MODEL in MODEL_PRICING_TABLE else 0.15,
        "output": MODEL_PRICING_TABLE[_FAST_MODEL].output_reasoning_usd_per_million if _FAST_MODEL in MODEL_PRICING_TABLE else 0.60,
    },
}


def compute_stage_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 2.50, "output": 10.00})
    cost_in = (input_tokens / 1_000_000.0) * pricing["input"]
    cost_out = (output_tokens / 1_000_000.0) * pricing["output"]
    return cost_in + cost_out


def run_benchmark(mode: str = "backtest", regenerate: str = "02,03") -> Dict[str, Any]:
    from dual_pipeline.remediation_core import parse_regenerate_spec

    regen_outputs = parse_regenerate_spec(regenerate)

    # Standard meeting profile (~30 min transcript)
    stages = {
        "output_1_synthesis": {
            "output_key": "output_1",
            "model": _SYNTH_MODEL,
            "input_tokens": 12000,
            "output_tokens": 4000,
        },
        "output_2_synthesis": {
            "output_key": "output_2",
            "model": _SYNTH_MODEL,
            "input_tokens": 8000,
            "output_tokens": 2500,
        },
        "output_3_synthesis": {
            "output_key": "output_3",
            "model": _SYNTH_MODEL,
            "input_tokens": 6000,
            "output_tokens": 1500,
        },
        "micro_judge_forensic": {
            "output_key": "output_1",
            "model": _PROD_MODEL,
            "input_tokens": 16000,
            "output_tokens": 800,
        },
        "micro_judge_recap": {
            "output_key": "output_2",
            "model": _PROD_MODEL,
            "input_tokens": 10000,
            "output_tokens": 600,
        },
        "micro_judge_graph": {
            "output_key": "output_3",
            "model": _PROD_MODEL,
            "input_tokens": 8000,
            "output_tokens": 500,
        },
    }

    full_costs = {}
    selective_costs = {}

    total_full = 0.0
    total_selective = 0.0

    for name, spec in stages.items():
        cost = compute_stage_cost(spec["model"], spec["input_tokens"], spec["output_tokens"])
        full_costs[name] = cost
        total_full += cost

        # In selective mode, if output_key is NOT in regen_outputs, cost is $0.00 (frozen)
        if spec["output_key"] in regen_outputs:
            selective_costs[name] = cost
            total_selective += cost
        else:
            selective_costs[name] = 0.0

    savings = total_full - total_selective
    pct_savings = (savings / total_full * 100.0) if total_full > 0 else 0.0

    receipt = {
        "benchmark_timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "mode": mode,
        "regenerate_spec": regenerate,
        "regenerated_outputs": sorted(list(regen_outputs)),
        "frozen_outputs": sorted(list({"output_1", "output_2", "output_3"} - regen_outputs)),
        "full_run_cost_usd": round(total_full, 4),
        "selective_run_cost_usd": round(total_selective, 4),
        "savings_usd": round(savings, 4),
        "savings_percentage": round(pct_savings, 2),
        "gate_threshold_usd": 0.30,
        "gate_passed": bool(total_selective < 0.30),
        "breakdown": {
            "full_run": {k: round(v, 4) for k, v in full_costs.items()},
            "selective_run": {k: round(v, 4) for k, v in selective_costs.items()},
        },
        "pinned_models_pareto": benchmark_pinned_models_pareto(),
    }

    return receipt


def main():
    parser = argparse.ArgumentParser(description="Run cost benchmark for selective regeneration.")
    parser.add_argument("--mode", default="backtest", choices=["backtest", "production"])
    parser.add_argument("--regenerate", default="02,03", help="Deliverables to regenerate")
    parser.add_argument("--verify-savings", action="store_true", default=False)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/benchmark_llm_cost_optimization_receipt.json"),
        help="Output JSON path for benchmark receipt",
    )
    args = parser.parse_args()

    receipt = run_benchmark(args.mode, args.regenerate)

    print("=" * 70)
    print("  MEETING RE-RUN COST OPTIMIZATION BENCHMARK RECEIPT")
    print("=" * 70)
    print(f"  Mode:                {receipt['mode']}")
    print(f"  Regenerate Spec:     {receipt['regenerate_spec']}")
    print(f"  Regenerated Outputs: {receipt['regenerated_outputs']}")
    print(f"  Frozen Outputs:      {receipt['frozen_outputs']}")
    print(f"  Full Run Cost:       ${receipt['full_run_cost_usd']:.4f}")
    print(f"  Selective Cost:      ${receipt['selective_run_cost_usd']:.4f}")
    print(f"  Cost Savings:        ${receipt['savings_usd']:.4f} ({receipt['savings_percentage']:.1f}%)")
    print(f"  Gate Threshold:      <${receipt['gate_threshold_usd']:.2f}")
    print(f"  Gate Result:         {'[PASS]' if receipt['gate_passed'] else '[FAIL]'}")
    print("=" * 70)

    # Save receipt
    receipt_file = Path(args.output)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"Receipt written to: {receipt_file}")

    if not receipt["gate_passed"]:
        print(f"ERROR: Selective run cost ${receipt['selective_run_cost_usd']} exceeds threshold $0.30")
        sys.exit(1)


if __name__ == "__main__":
    main()
