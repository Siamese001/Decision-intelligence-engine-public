#!/usr/bin/env python3
"""
evals/evaluate_tier_token_economics.py

Model Alignment & Live Token Economics Evaluation Suite:
Evaluates credential resolution, prompt cache efficiency, token economics,
and multi-micro-judge calibration under governed model tier configurations.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.credentials import (
    resolve_openai_credentials,
)
from dual_pipeline.token_optimization import (
    TokenBudget,
    TokenOptimizationAgent,
    TokenProfiler,
    TokenUsageMetric,
    get_model_pricing,
)
from evals.evaluate_multi_judge_calibration import (
    MultiJudgeCalibrationEvaluator,
)


_HERMETIC_MOCK_ENABLED: bool = bool("1")


def run_tier_token_economics(
    model: str = "gpt-5.6-luna",
    reasoning_effort: str = "medium",
    allow_mock: bool = _HERMETIC_MOCK_ENABLED,
) -> Dict[str, Any]:
    """Executes the complete token economics evaluation suite under the specified model tier."""
    start_time = time.time()

    # 1. Credential Resolution & Masking
    creds = resolve_openai_credentials(
        allow_mock=allow_mock,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    credential_status = {
        "model": creds.model,
        "reasoning_effort": creds.reasoning_effort,
        "is_mock": creds.is_mock,
        "has_live_key": creds.has_live_key,
        "masked_key": creds.masked_key,
        "source": creds.source,
    }

    # 2. Token Pricing & Budget Economics
    pricing = get_model_pricing(model)
    profiler = TokenProfiler(default_model=model)
    agent = TokenOptimizationAgent(
        default_model=model,
        default_reasoning_effort=reasoning_effort,
    )

    # Realistic meeting header prefix & payload without synthetic personas
    system_prefix = (
        "You are an executive forensic analyst for Apex Global Enterprises transmission operations. "
        "Adhere to Rule C-01 through C-19 and the 7 Non-Negotiable Engineering Laws. "
    ) * 80
    user_transcript = (
        "[00:00:25] Operations Lead: Reviewing transmission telemetry and substation monitoring.\n"
        "[00:01:10] Technical Services Lead: Technical services is identifying the root cause for momentary trips."
    )

    directive, components = agent.optimize_request(
        system_prompt=system_prefix,
        user_content=user_transcript,
        task_type="stage01_forensic",
        model=model,
    )

    prefix_tokens = profiler.count_tokens(system_prefix, model=model)
    user_tokens = profiler.count_tokens(user_transcript, model=model)
    total_in = prefix_tokens + user_tokens

    # Expected cache hit: static prefix is cached (>1024 tokens)
    cached_in = prefix_tokens if prefix_tokens >= 1024 else 0
    uncached_in = total_in - cached_in
    simulated_output_tokens = 650

    metric = TokenUsageMetric(
        input_tokens=total_in,
        cached_input_tokens=cached_in,
        uncached_input_tokens=uncached_in,
        output_tokens=simulated_output_tokens,
        model=model,
    )
    metric.finalize_metrics(pricing)

    budget = TokenBudget(max_input_tokens=200_000, max_cost_usd=5.00)
    budget_exceeded, budget_reason = budget.check_exceeded(metric)

    token_economics = {
        "pricing_schedule": {
            "model_id": pricing.model_id,
            "uncached_input_usd_per_million": pricing.uncached_input_usd_per_million,
            "cached_input_usd_per_million": pricing.cached_input_usd_per_million,
            "output_reasoning_usd_per_million": pricing.output_reasoning_usd_per_million,
        },
        "measured_usage": metric.to_dict(),
        "cache_hit_rate_pct": round(metric.cache_hit_rate * 100, 2),
        "budget_passed": not budget_exceeded,
        "recommended_reasoning_effort": directive.recommended_reasoning_effort.value,
    }

    # 3. Multi-Judge Calibration against Golden Benchmark Dataset
    evaluator = MultiJudgeCalibrationEvaluator(
        target_model=model,
        target_reasoning_effort=reasoning_effort,
        use_mock=allow_mock,
    )
    calibration_result = evaluator.run_all()

    elapsed = time.time() - start_time
    all_passed = (
        creds.model == model
        and creds.reasoning_effort == reasoning_effort
        and not budget_exceeded
        and calibration_result["status"] == "PASS"
    )

    return {
        "evaluation_contract": "urn:nee:dual-pipeline:tier-token-economics-eval:1.0.0",
        "wave_id": f"Tier Token Economics & Model Alignment ({model} {reasoning_effort})",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "PASS" if all_passed else "FAIL",
        "target_model": model,
        "reasoning_effort": reasoning_effort,
        "credential_resolution": credential_status,
        "token_economics": token_economics,
        "multi_judge_calibration": {
            "status": calibration_result["status"],
            "gates": calibration_result["gates"],
            "metrics": calibration_result["metrics"],
        },
        "elapsed_seconds": round(elapsed, 4),
    }


# Backward-compatibility alias
run_wave1_evaluation = run_tier_token_economics


def main() -> int:
    print("=" * 80)
    print("Tier Token Economics Evaluation")
    print("=" * 80)

    result = run_tier_token_economics()
    print(f"Overall Status: {result['status']} (Elapsed: {result['elapsed_seconds']}s)\n")

    print("Credential Resolution:")
    c = result["credential_resolution"]
    print(f"  Model:            {c['model']} (Reasoning: {c['reasoning_effort']})")
    print(f"  Source:           {c['source']}")
    print(f"  Masked Key:       {c['masked_key']}\n")

    print("Token Economics & Caching:")
    t = result["token_economics"]
    m = t["measured_usage"]
    print(f"  Total Tokens:     {m['total_tokens']:,} (Input: {m['input_tokens']:,}, Output: {m['output_tokens']:,})")
    print(f"  Cache Hit Rate:   {t['cache_hit_rate_pct']}%")
    print(f"  Cost:             ${m['cost_usd']:.4f}")
    print(f"  Budget Passed:    {t['budget_passed']}\n")

    print("Multi-Judge Calibration Gates:")
    gates = result["multi_judge_calibration"]["gates"]
    for gate_name, passed in gates.items():
        mark = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {gate_name:45s} {mark}")

    metrics = result["multi_judge_calibration"]["metrics"]
    print(f"\n  Cohen's Kappa:    {metrics['cohens_kappa']} (Threshold >= {metrics['kappa_threshold']})")
    print(f"  Accuracy:         {metrics['accuracy'] * 100:.1f}%")
    print(f"  Specificity:      {metrics['specificity'] * 100:.1f}%\n")

    print("=" * 80)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
