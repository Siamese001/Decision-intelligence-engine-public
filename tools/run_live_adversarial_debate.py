#!/usr/bin/env python3
"""
tools/run_live_adversarial_debate.py

Live Adversarial Debate Runner across Pipelines A–D.
Enforces the Operator-Directed Live Execution Invariant (.agents/rules/tool-routing.md):
- Sets allow_mock=False
- Connects directly to OpenAI API using credentials resolved from env_di
- Invokes gpt-5.6-sol with reasoning_effort='high'
- Emits full live debate transcript to governance/decision_intelligence/ADVERSARIAL_EVAL_HARNESS_DEBATE_V1.md
- Emits RFC-8259 live receipt to reports/adversarial_debate_live_receipt.json with token telemetry
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.credentials import get_openai_client, resolve_openai_credentials
from dual_pipeline.model_registry import ModelRegistry, ModelTier

DEBATE_OUTPUT_PATH = ROOT / "governance" / "decision_intelligence" / "ADVERSARIAL_EVAL_HARNESS_DEBATE_V1.md"
RECEIPT_OUTPUT_PATH = ROOT / "reports" / "adversarial_debate_live_receipt.json"

PROSECUTION_PROMPT = """You are the Lead Adversarial Quality Auditor prosecuting the technical debt in our evaluation harness across Pipelines A–D.
Target codebase under audit:
- Pipeline A: pipeline_a_evaluator.py (Acoustic evidence, Gaussian noise vs real DSP, speaker locking, Ambiguity Lock Register)
- Pipeline B: pipeline_b_evaluator.py (C-13 prompt injection defense, tautological check in line 71, C-19 Career Leverage Firewall isolation)
- Pipeline C: pipeline_c_evaluator.py (VectorCache generational invalidation, 500-query steady-state retrieval SLA vs in-memory numpy BLAS, Law 6 quarantine)
- Pipeline D: pipeline_d_evaluator.py (HITL 15% ambiguity margin gating precision, synthetic stop hook rejection, Bayesian priors)
- Unified Harness: tools/run_agentic_eval_harness.py (Pyramid Tiers 1-5 mocks and tautological lambdas)

Deliver a rigorous, adversarial prosecution detailing:
1. The exact low-signal, tautological, or synthetic checks that mask failures.
2. The architectural risks of relying on these vanity metrics.
3. Specific charges for Pipelines A, B, C, and D.
Be direct, forensic, and unsparing."""

DEFENSE_PROMPT = """You are the Pipeline & Systems Architect defending the engineering choices while conceding verified defects.
Review the prosecution's charges regarding Pipelines A–D:
1. Acknowledge valid engineering tradeoffs (speed, determinism, hermetic CI).
2. Concede where metrics are genuinely tautological (e.g. Pipeline B injection check) or low-signal (in-memory numpy dot products for retrieval SLA).
3. Propose realistic, high-signal, contract-grounded alternatives that verify real behavior without unneeded bloat.
Be constructive, technically precise, and grounded in the repository's unidirectional L0-L6 architecture."""

JUDGE_PROMPT = """You are the Dialectical Synthesis Judge adjudicating the adversarial debate between Prosecution and Defense across Pipelines A–D.
Synthesize the arguments and issue binding judicial findings:
1. Formal rulings on each pipeline (A, B, C, D) and the unified harness.
2. Official high-signal metric definitions and strict pass/fail criteria:
   - Pipeline A: S_noise, A_locking, P_enrolled, L_chunk.
   - Pipeline B: R_c13, C_c19, R_repair, Q_dlq.
   - Pipeline C: I_cache, T_retrieval (<7500ms over SQLite sidecars), P_law6.
   - Pipeline D: A_margin (Delta=0.25 precision), R_synthetic (100% stop hook defense), G_prior.
3. Binding implementation orders for Waves 2 through 6.
Conclude with an immutable judicial order."""


def run_live_debate(model: Optional[str] = None, reasoning_effort: Optional[str] = None) -> Dict[str, Any]:
    tier_model, tier_effort = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    resolved_model = model or tier_model
    resolved_effort = reasoning_effort or tier_effort or "high"

    print("=" * 80)
    print("  LIVE ADVERSARIAL EVALUATION HARNESS DEBATE")
    print(f"  Target Model:     {resolved_model}")
    print(f"  Reasoning Effort: {resolved_effort}")
    print("  Credential SSOT:  env_di")
    print("=" * 80)

    # 1. Resolve live credentials (fail-closed)
    creds = resolve_openai_credentials(allow_mock=False, model=resolved_model, reasoning_effort=resolved_effort)

    if not creds.has_live_key:
        print("❌ FATAL: Live API key missing from env_di. Fail-closed.")
        sys.exit(1)

    print(f"✅ Credentials resolved from {creds.source} (Key: {creds.masked_key})")
    client, creds = get_openai_client(allow_mock=False, model=model, reasoning_effort=reasoning_effort)

    rounds: List[Dict[str, Any]] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_start_time = time.time()

    conversation_history = [
        {"role": "system", "content": "You are participating in a formal adversarial governance debate on AI evaluation rigor."}
    ]

    steps = [
        ("Round 1: Prosecution (Quality Auditor)", PROSECUTION_PROMPT, "prosecution"),
        ("Round 2: Defense (Pipeline Architect)", DEFENSE_PROMPT, "defense"),
        ("Round 3: Adjudication (Synthesis Judge)", JUDGE_PROMPT, "judge"),
    ]

    full_transcript_parts: List[str] = [
        "# Live Dialectical Adversarial Evaluation Debate (Pipelines A–D)\n",
        f"**Model**: `{model}` ({reasoning_effort} reasoning)  \n",
        f"**Date**: {dt.datetime.now(dt.timezone.utc).strftime('%m/%d/%Y %H:%M:%S UTC')}  \n",
        "**Credential Source**: `env_di` (Active OpenAI Provider Endpoint)  \n\n",
        "---\n",
    ]

    for label, prompt_text, role_key in steps:
        print(f"\n>>> Dispatched {label} to {model} ({reasoning_effort})...", flush=True)
        step_start = time.time()

        conversation_history.append({"role": "user", "content": prompt_text})

        response = client.chat.completions.create(
            model=creds.model,
            reasoning_effort=creds.reasoning_effort,
            messages=conversation_history,
        )

        step_elapsed = round(time.time() - step_start, 2)
        content = response.choices[0].message.content or ""
        conversation_history.append({"role": "assistant", "content": content})

        p_tokens = response.usage.prompt_tokens if response.usage else 0
        c_tokens = response.usage.completion_tokens if response.usage else 0
        total_prompt_tokens += p_tokens
        total_completion_tokens += c_tokens

        print(f"    Completed in {step_elapsed}s | Tokens: {p_tokens} prompt, {c_tokens} completion")

        rounds.append({
            "round": label,
            "role": role_key,
            "duration_seconds": step_elapsed,
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "content": content,
        })

        full_transcript_parts.append(f"\n## {label}\n\n{content}\n\n---\n")

    total_duration = round(time.time() - total_start_time, 2)

    # 2. Write full live debate transcript
    DEBATE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEBATE_OUTPUT_PATH.write_text("".join(full_transcript_parts), encoding="utf-8")
    print(f"\n✅ Live debate transcript saved to: {DEBATE_OUTPUT_PATH}")

    # 3. Write execution receipt
    RECEIPT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    receipt_data = {
        "status": "SUCCESS",
        "model_id": model,
        "reasoning_effort": reasoning_effort,
        "credential_source": creds.source,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "duration_seconds": total_duration,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "debate_transcript_path": str(DEBATE_OUTPUT_PATH),
        "rounds": [
            {
                "round": r["round"],
                "role": r["role"],
                "duration_seconds": r["duration_seconds"],
                "tokens": {"prompt": r["prompt_tokens"], "completion": r["completion_tokens"]},
            }
            for r in rounds
        ],
    }
    canonical_receipt = json.dumps(receipt_data, sort_keys=True, indent=2)
    receipt_digest = hashlib.sha256(canonical_receipt.encode("utf-8")).hexdigest()
    receipt_data["receipt_digest"] = receipt_digest

    with open(RECEIPT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(receipt_data, f, indent=2)

    print(f"✅ Live execution receipt saved to: {RECEIPT_OUTPUT_PATH}")
    print("=" * 80)
    print(f"  TOTAL DEBATE TOKENS: {total_prompt_tokens + total_completion_tokens} | TIME: {total_duration}s")
    print(f"  RECEIPT DIGEST:      {receipt_digest}")
    print("=" * 80)
    return receipt_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Adversarial Debate Runner")
    parser.add_argument("--model", default=None, help="Target model ID (defaults to ModelTier.DIALECTICAL_JUDGE)")
    parser.add_argument("--reasoning-effort", default=None, help="Reasoning effort")
    args = parser.parse_args()

    run_live_debate(model=args.model, reasoning_effort=args.reasoning_effort)

