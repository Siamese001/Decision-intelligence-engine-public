#!/usr/bin/env python3
"""tools/run_adversarial_modularization_debate.py

Live Adversarial 3-Agent Debate: Would Further Modularizing the Codebase Derisk
Issues Encountered Across the Last 150 PRs?

Enforces the Operator-Directed Live Execution Invariant (.agents/rules/tool-routing.md):
- Model: Resolved via ModelRegistry (defaults to ModelTier.DIALECTICAL_JUDGE)
- allow_mock=False
- SSOT credentials from env_di
- Live API telemetry (tokens, latency, model_id, digest) in durable JSON receipt
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

DEBATE_OUTPUT_PATH = ROOT / "governance" / "decision_intelligence" / "ADVERSARIAL_MODULARIZATION_DEBATE_REPORT.md"
RECEIPT_OUTPUT_PATH = ROOT / "reports" / "adversarial_modularization_debate_receipt.json"

CONTEXT_DATA = """
### EMPIRICAL FORENSIC DATA FROM LAST 150 PRs / COMMITS IN REPOSITORY:

1. Commit Distribution across last 150 commits:
   - Prompt & LLM Judge Drift / Threshold Hardening (Gate 2, Gate 3, score 90 floors, prompt contracts): 41 commits (27.3%)
   - Governance Receipts, Main Promotion, & Pre-Push Readiness (23-gate promotion, receipts): 39 commits (26.0%)
   - Other / General Bug Fixes & Chores: 33 commits (22.0%)
   - Speaker Attribution, HITL Disambiguation & Roster Alignment: 12 commits (8.0%)
   - Cognitive Complexity Decomposition & Line-Budget Refactoring: 11 commits (7.3%)
   - Output Hierarchy, Subfolder Architecture & Project Routing: 6 commits (4.0%)
   - Attachment Alignment & Multimodal Extraction: 4 commits (2.7%)
   - Tokenomics, Truncation & Reasoning Budget Expansion: 3 commits (2.0%)
   - Audio Ingestion, Whisper & Live WebSocket Streaming: 1 commit (0.7%)

2. Major Historical RCAs and Root Causes:
   - RCA 1 (RCA NEA Misplacement): Moving project router from monolithic script to decoupled query_router_core caused LLM router to ignore deterministic project prefixes (^NEA), misrouting meetings into enterprise_architecture.
   - RCA 2 (RCA Speaker Attribution & Deck Alignment): Decomposing attachment aligner and transcript formatter stripped markdown turn headers (### [HH:MM:SS]) and keyword matching excluded visual cues, causing 100% missed attachment mentions.
   - RCA 3 (Cognitive Complexity Refactor Thrash): Dozens of commits mechanically splitting functions to satisfy `cognitive_complexity <= 15` dropped default function arguments (e.g. scaffold_meeting_metadata attendee fallback) and broke existing test callers, requiring backward-compatible alias shims.
   - RCA 4 (Pipeline Fallback & Token Truncation): Models failing due to completion token limits (1,500 max completion tokens) and reasoning token starvation under high prompt load, causing JSON truncation and triggering healing mesh fallbacks.

3. Current State of Modularization in Codebase:
   - 25+ packages already decomposed into `*_core/` subpackages (query_core, release_core, uwg_core, completion_core, transcript_pipeline_core, etc.).
   - 71 oversized files remain in `file_budget_burndown_baseline.json` (e.g. query_core/engine.py: 4,138 lines, release_core/manager.py: 2,262 lines, storage/database.py: 1,138 lines).
   - Forensic Churn x Complexity investigation found query.py and validate_dual_pipeline_contracts.py have critical risk scores (>3,600) and zero direct unit test suites.
"""

AGENT_1_PROMPT = f"""You are Agent 1: The Structural Purist & Modularization Evangelist.
Your goal is to argue forcefully that FURTHER MODULARIZING the codebase is an absolute necessity to derisk future engineering and address the issues from the last 150 PRs.

Ground your argument in the following forensic context:
{CONTEXT_DATA}

Address:
1. The remaining 71 oversized modules in `reports/file_budget_burndown_baseline.json` (e.g. query_core/engine.py at 4,138 lines, storage/database.py at 1,138 lines).
2. Why high cognitive complexity and oversized modules make it impossible for LLM agents (and human engineers) to maintain full context, leading directly to hallucinated assumptions and blindspots.
3. The Churn x Complexity report showing query and contract validation modules with risk scores > 3,600 and zero direct unit tests because they are too large and coupled to test hermetically.
4. How strict layer boundaries (L0-L6), micro-modules (<400 lines), and dependency injection would isolate blast radii and make tests faster, parallelizable, and deterministic.
Be uncompromising, technically rigorous, and specific."""

AGENT_2_PROMPT = f"""You are Agent 2: The Systems Pragmatist & Anti-Fragmentation Skeptic.
Your goal is to directly rebut Agent 1 and argue that FURTHER MODULARIZATION is a false panacea that will actively INCREASE risk, fail to solve the primary failure modes of the last 150 PRs, and accelerate architectural rot.

Ground your rebuttal in the following forensic context:
{CONTEXT_DATA}

Address:
1. The empirical data: Over 70% of all issues in the last 150 PRs were NOT caused by file size. They were caused by LLM prompt/judge drift (27.3%), governance receipts & promotion churn (26.0%), token exhaustion/truncation, and regex/ASR boundary edge cases.
2. How modularization itself DIRECTLY CAUSED major RCAs in the last 150 PRs:
   - The NEA misplacement bug was caused by decoupling the project router into an LLM sub-module that lost deterministic prefix matching.
   - Slicing functions to satisfy `cognitive_complexity <= 15` dropped default parameters in scaffold_meeting_metadata and stripped markdown turn headers in attachment aligner.
   - Breaking modules introduced import cycle hell (requiring Tarjan's SCC linter in CI) and broken caller interfaces requiring alias shims.
3. "Death by 1,000 files": When an agent or developer has to navigate 8 files across 4 `*_core` directories just to trace one meeting transcription pass, cognitive load increases, holistic reasoning collapses, and integration testing complexity explodes.
4. Why decomposing files under 400 lines is a cosmetic vanity metric that distracts from genuine domain-driven correctness.
Be sharp, forensic, unsparing, and grounded in the commit history."""

AGENT_3_PROMPT = """You are Agent 3: The Dialectical Arbiter & Chief Governance Architect.
Your task is to adjudicate the debate between Agent 1 (The Modularization Evangelist) and Agent 2 (The Anti-Fragmentation Skeptic), analyze the systems dynamics of the last 150 PRs, and issue the definitive, evidence-backed RECOMMENDATION.

Structure your adjudication as follows:
1. Executive Verdict: Direct answer to "Would further modularizing the codebase help derisk all the issues encountered in the last 150 PRs?"
2. The Empirical 4-Quadrant Impact Matrix:
   - Quadrant I: Issues Where Modularization Directly Helps & Is Warranted (What specific parts of the codebase actually benefit, e.g. pure business logic decoupling, storage isolation).
   - Quadrant II: Issues Where Modularization Directly CAUSED Regressions (Cognitive complexity thrash, parameter dropping, header stripping, import cycles).
   - Quadrant III: Issues Orthogonal to Modularization (Prompt/judge drift, score 90 floors, LLM reasoning token starvation, ASR repetitions, BGE-M3 latency).
   - Quadrant IV: Negative Externalities of Over-Modularization ("Death by 1,000 files", context window fragmentation for coding agents, integration test blindspots).
3. The Root-Cause Pareto Analysis:
   - What actually caused 80% of the regressions across the last 150 PRs?
4. Prescriptive Architectural Recommendation:
   - Stop vs Continue vs Pivot guidelines.
   - Exact criteria for when to modularize vs when to strictly forbid file splitting.
   - Alternative derisking strategies that yield 10x higher ROI than further file decomposition.
Deliver a definitive, executive-grade ruling."""


def run_adversarial_modularization_debate(
    model: Optional[str] = None, reasoning_effort: Optional[str] = None
) -> Dict[str, Any]:
    tier_model, tier_effort = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    resolved_model = model or tier_model
    resolved_effort = reasoning_effort or tier_effort or "xhigh"

    print("=" * 80)
    print("  LIVE ADVERSARIAL 3-AGENT DEBATE: CODEBASE MODULARIZATION DERISKING")
    print(f"  Target Model:     {resolved_model}")
    print(f"  Reasoning Effort: {resolved_effort}")
    print("  Execution Mode:   Operator-Directed Live Provider (allow_mock=False)")
    print("  Credential SSOT:  env_di")
    print("=" * 80)

    # 1. Resolve live credentials (fail-closed)
    creds = resolve_openai_credentials(allow_mock=False, model=resolved_model, reasoning_effort=resolved_effort)
    if not creds.has_live_key:
        print("❌ FATAL: Live API key missing from env_di. Fail-closed under Operator Invariant.")
        sys.exit(1)

    print(f"✅ Credentials resolved from {creds.source} (Key: {creds.masked_key})")
    client, creds = get_openai_client(allow_mock=False, model=model, reasoning_effort=reasoning_effort)
    if client is None:
        print("❌ FATAL: Failed to initialize OpenAI client with live credentials. Fail-closed.")
        sys.exit(1)

    rounds: List[Dict[str, Any]] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_start_time = time.time()

    conversation_history = [
        {
            "role": "system",
            "content": (
                "You are an elite principal systems architect participating in a formal adversarial "
                "engineering debate on codebase modularization, technical debt, and system reliability."
            ),
        }
    ]

    steps = [
        ("Agent 1 (The Structural Purist): Case for Further Modularization", AGENT_1_PROMPT, "agent_1_purist"),
        ("Agent 2 (The Systems Pragmatist): Rebuttal Against Fragmentation", AGENT_2_PROMPT, "agent_2_pragmatist"),
        ("Agent 3 (The Dialectical Arbiter): Synthesis & Final Recommendation", AGENT_3_PROMPT, "agent_3_arbiter"),
    ]

    full_transcript_parts: List[str] = [
        "# Adversarial 3-Agent Debate: Does Further Modularization Derisk the Codebase?\n\n",
        "> **Evaluation Scope:** Forensic Analysis of Issues Encountered Across Last 150 PRs  \n",
        f"> **Model Tier:** Frontier Reasoning (`{model}` with `{reasoning_effort}` reasoning)  \n",
        f"> **Date:** {dt.datetime.now(dt.timezone.utc).strftime('%m/%d/%Y %H:%M:%S UTC')}  \n",
        "> **Execution Mode:** Operator-Directed Live Execution (`allow_mock=False`, SSOT `env_di`)  \n\n",
        "---\n\n",
    ]

    for label, prompt_text, role_key in steps:
        print(f"\n>>> Dispatched [{label}] to {model} ({reasoning_effort})...", flush=True)
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

        full_transcript_parts.append(f"## {label}\n\n{content}\n\n---\n\n")

    total_duration = round(time.time() - total_start_time, 2)

    # 2. Write full live debate report
    DEBATE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEBATE_OUTPUT_PATH.write_text("".join(full_transcript_parts), encoding="utf-8")
    print(f"\n✅ Live debate report saved to: {DEBATE_OUTPUT_PATH}")

    # 3. Write execution receipt
    RECEIPT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    receipt_data = {
        "status": "SUCCESS",
        "topic": "adversarial_modularization_debate_150_prs",
        "model_id": model,
        "reasoning_effort": reasoning_effort,
        "credential_source": creds.source,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "duration_seconds": total_duration,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "debate_report_path": str(DEBATE_OUTPUT_PATH),
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
    parser = argparse.ArgumentParser(description="Live Adversarial Modularization Debate Runner")
    parser.add_argument("--model", default=None, help="Target model ID (defaults to ModelTier.DIALECTICAL_JUDGE)")
    parser.add_argument("--reasoning-effort", default=None, help="Reasoning effort (defaults to tier effort)")
    args = parser.parse_args()

    run_adversarial_modularization_debate(model=args.model, reasoning_effort=args.reasoning_effort)
