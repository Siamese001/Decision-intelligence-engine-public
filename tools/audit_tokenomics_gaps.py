#!/usr/bin/env python3
"""tools/audit_tokenomics_gaps.py

Deterministic 8-pillar Tokenomics & Cost Optimization Auditor.
Evaluates model pinning, prompt caching, semantic caching, multi-tier deterministic
caching, healer diffs, judge retries, closed-loop learning, and frontier lab
thought-leadership cascades across the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _audit_pillar_1_model_pinning(repo_root: Path) -> dict[str, Any]:
    """Pillar 1: Model pinning right-sizing & benchmark pricing parity."""
    reg_path = repo_root / "config" / "runtime" / "model_registry.json"
    gaps: list[str] = []
    healer_default = "unknown"
    fast_routing_default = "unknown"
    if reg_path.is_file():
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        roles = data.get("model_tiers", data.get("roles", {}))
        healer_default = roles.get("SURGICAL_HEALER", {}).get("default_model", "unknown")
        fast_routing_default = roles.get("FAST_ROUTING", {}).get("default_model", "unknown")
        if healer_default.endswith("-sol"):
            gaps.append(f"SURGICAL_HEALER over-pinned to frontier model '{healer_default}'")
        google_fallbacks = roles.get("ANTIGRAVITY_AGENT_FLASH", {}).get("allowed_fallbacks", [])
        if not any("flash-lite" in m for m in google_fallbacks):
            gaps.append("flash-lite missing from ANTIGRAVITY_AGENT_FLASH cascade")
    else:
        gaps.append("Missing config/runtime/model_registry.json")

    bench_path = repo_root / "dual_pipeline" / "cost_benchmark_runner.py"
    if bench_path.is_file():
        bench_text = bench_path.read_text(encoding="utf-8")
        if "MODEL_PRICING_TABLE" not in bench_text:
            gaps.append("cost_benchmark_runner.py does not import canonical MODEL_PRICING_TABLE")

    return {
        "pillar": "1_model_pinning_and_benchmarking",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "healer_default_model": healer_default,
        "fast_routing_default_model": fast_routing_default,
        "gaps": gaps,
    }


def _audit_pillar_2_prompt_caching(repo_root: Path) -> dict[str, Any]:
    """Pillar 2: Multi-provider prompt caching (OpenAI, Gemini, Anthropic)."""
    gaps: list[str] = []
    forensic_path = repo_root / "dual_pipeline" / "llm_judge_core" / "judges" / "forensic.py"
    if forensic_path.is_file():
        txt = forensic_path.read_text(encoding="utf-8")
        if "prompt_cache_key" not in txt:
            gaps.append("forensic.py missing official OpenAI prompt_cache_key")

    gemini_cache = repo_root / "dual_pipeline" / "transcript_stages_core" / "gemini_context_cache.py"
    if gemini_cache.is_file():
        gtxt = gemini_cache.read_text(encoding="utf-8")
        if "100_000" in gtxt:
            gaps.append("gemini_context_cache.py uses legacy 100k char floor instead of 1024/4096 token floor")

    prompt_cache = repo_root / "dual_pipeline" / "prompt_cache.py"
    if prompt_cache.is_file():
        ptxt = prompt_cache.read_text(encoding="utf-8")
        if "build_anthropic_multi_breakpoint_payload" not in ptxt:
            gaps.append("prompt_cache.py missing 4-slot Anthropic multi-breakpoint & 1h TTL helper")

    return {
        "pillar": "2_prompt_caching",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def _audit_pillar_3_semantic_caching(repo_root: Path) -> dict[str, Any]:
    """Pillar 3: Generation-bound semantic caching across query, L2, judge, and healer."""
    gaps: list[str] = []
    sc_path = repo_root / "dual_pipeline" / "semantic_cache_core" / "cache.py"
    if sc_path.is_file():
        txt = sc_path.read_text(encoding="utf-8")
        if "SUPPORTED_SEMANTIC_NAMESPACES" not in txt:
            gaps.append("SemanticCache restricted to query use_case; missing l2_extraction/micro_judge/l2_healer namespaces")
    else:
        gaps.append("Missing dual_pipeline/semantic_cache_core/cache.py")

    return {
        "pillar": "3_semantic_caching",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def _audit_pillar_4_multi_tier_caching(repo_root: Path) -> dict[str, Any]:
    """Pillar 4: Exact-response digest, turn-window incremental, negative failure, and subgraph caches."""
    gaps: list[str] = []
    mt_path = repo_root / "dual_pipeline" / "token_optimization" / "multi_tier_cache.py"
    if not mt_path.is_file():
        gaps.append("Missing dual_pipeline/token_optimization/multi_tier_cache.py")
    else:
        txt = mt_path.read_text(encoding="utf-8")
        for cls_name in (
            "ExactResponseDigestCache",
            "TurnWindowIncrementalCache",
            "PersistentNegativeFailureCache",
            "SubgraphReadMemoCache",
        ):
            if cls_name not in txt:
                gaps.append(f"multi_tier_cache.py missing {cls_name}")

    return {
        "pillar": "4_other_deterministic_caches",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def _audit_pillar_5_healer_agents(repo_root: Path) -> dict[str, Any]:
    """Pillar 5: Surgical diff healers, deterministic pre-pass, and cost tracker recording."""
    gaps: list[str] = []
    healer_path = repo_root / "dual_pipeline" / "l2_healer_service.py"
    if healer_path.is_file():
        txt = healer_path.read_text(encoding="utf-8")
        if "get_cost_tracker" not in txt:
            gaps.append("UnifiedL2HealerService does not record LLM spend in RunCostTracker")
        if "compact_json" not in txt and 'separators=(",", ":")' not in txt:
            gaps.append("UnifiedL2HealerService serializes full artifacts with indent=2 instead of compact span diffs")
    else:
        gaps.append("Missing dual_pipeline/l2_healer_service.py")

    return {
        "pillar": "5_healer_agents",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def _audit_pillar_6_judge_retries(repo_root: Path) -> dict[str, Any]:
    """Pillar 6: Frozen deliverable bypass, deterministic scorecard sum healing, and judge cascades."""
    gaps: list[str] = []
    orch_path = repo_root / "dual_pipeline" / "llm_judge_core" / "orchestrator.py"
    if orch_path.is_file():
        txt = orch_path.read_text(encoding="utf-8")
        if "frozen_deliverables" not in txt and "frozen_artifacts" not in txt:
            gaps.append("DecomposedMultiJudge.evaluate_parallel re-evaluates frozen deliverables on retry")

    forensic_path = repo_root / "dual_pipeline" / "llm_judge_core" / "judges" / "forensic.py"
    if forensic_path.is_file():
        ftxt = forensic_path.read_text(encoding="utf-8")
        if "_heal_scorecard_arithmetic" not in ftxt:
            gaps.append("ForensicTranscriptJudge triggers 40k-token LLM retry on scorecard arithmetic sum drift")

    return {
        "pillar": "6_judge_retries_and_feedback_loops",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def _audit_pillar_7_system_learning(repo_root: Path) -> dict[str, Any]:
    """Pillar 7: Closed-loop system learning from Judge, Healer, CircuitBreaker, and Cost cohorts."""
    gaps: list[str] = []
    le_path = repo_root / "dual_pipeline" / "learning_engine.py"
    if le_path.is_file():
        txt = le_path.read_text(encoding="utf-8")
        if "ingest_tokenomics_signals" not in txt:
            gaps.append("ClosedLoopLearningEngine missing ingest_tokenomics_signals for judge/healer/cache cohorts")
    else:
        gaps.append("Missing dual_pipeline/learning_engine.py")

    return {
        "pillar": "7_system_learning",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def _audit_pillar_8_thought_leadership(repo_root: Path) -> dict[str, Any]:
    """Pillar 8: FrugalGPT cascades, dynamic thinking budgets, Batch API 50% discount, and PTC."""
    gaps: list[str] = []
    batch_path = repo_root / "dual_pipeline" / "batch_core" / "adapter.py"
    if batch_path.is_file():
        btxt = batch_path.read_text(encoding="utf-8")
        if "anthropic" not in btxt.lower() or "gemini" not in btxt.lower():
            gaps.append("BatchSubmissionAdapter lacks multi-provider (Anthropic/Gemini) 50% batch discount support")

    cc_path = repo_root / "dual_pipeline" / "query_router_core" / "complexity_classifier.py"
    if cc_path.is_file():
        ctxt = cc_path.read_text(encoding="utf-8")
        if "resolve_frugal_cascade_plan" not in ctxt:
            gaps.append("complexity_classifier.py missing FrugalGPT confidence cascade planner")

    agent_path = repo_root / "dual_pipeline" / "token_optimization" / "agent.py"
    if agent_path.is_file():
        atxt = agent_path.read_text(encoding="utf-8")
        if "assigned_skills=" not in atxt:
            gaps.append("TokenOptimizationAgent._build_subagents missing explicit assigned_skills keyword binding")

    return {
        "pillar": "8_frontier_thought_leadership",
        "status": "PASS" if not gaps else "ACTION_REQUIRED",
        "gaps": gaps,
    }


def run_tokenomics_audit(repo_root: Path = ROOT) -> dict[str, Any]:
    """Runs all 8 pillar checks and returns a consolidated report."""
    pillars = [
        _audit_pillar_1_model_pinning(repo_root),
        _audit_pillar_2_prompt_caching(repo_root),
        _audit_pillar_3_semantic_caching(repo_root),
        _audit_pillar_4_multi_tier_caching(repo_root),
        _audit_pillar_5_healer_agents(repo_root),
        _audit_pillar_6_judge_retries(repo_root),
        _audit_pillar_7_system_learning(repo_root),
        _audit_pillar_8_thought_leadership(repo_root),
    ]
    total_gaps = sum(len(p["gaps"]) for p in pillars)
    return {
        "contract_id": "urn:nee:tokenomics-8-pillar-audit:1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_pillars": len(pillars),
        "pillars_passing": sum(1 for p in pillars if p["status"] == "PASS"),
        "total_gaps": total_gaps,
        "terminal_outcome": "PASS" if total_gaps == 0 else "ACTION_REQUIRED",
        "pillars": pillars,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="8-Pillar Tokenomics Cost Optimization Auditor")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "tokenomics_gap_audit.json")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any gaps remain")
    args = parser.parse_args(argv)

    report = run_tokenomics_audit(args.repo_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"[TOKENOMICS AUDIT] {report['pillars_passing']}/{report['total_pillars']} pillars passing "
            f"({report['total_gaps']} gap(s)) -> {report['terminal_outcome']}"
        )
    if args.strict and report["total_gaps"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
