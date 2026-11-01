#!/usr/bin/env python3
"""tools/run_executive_graph_adversarial_debate.py

Live Adversarial 5-Agent Debate: Is GraphDB Set Up to Provide an Asymmetric
Competitive Advantage for a $1.2M+ Executive IT Position?

Evaluates across:
1. Client WOW Factor (Fortune 50 Enterprise CIO/CTO)
2. PartnerAdvisory Practice Leadership & Commercial Leverage (Consulting MD/Partner)
3. Graph Topology, Multi-Hop Mining & Ontological Rigor (Chief Knowledge Architect)
4. Career Strategy & Confidential Advisory Firewall (Rule C-19 Enclave Guardian)
5. Industry Thought Leadership & Market Eminence (Frontier AI Strategist)

Enforces Operator-Directed Live Execution Invariant (.agents/rules/tool-routing.md):
- Model: Resolved via ModelRegistry or operator flag (defaults to gpt-6-astra)
- allow_mock=False (fails closed if live key missing)
- Credentials SSOT from env_di
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

DEBATE_OUTPUT_PATH = ROOT / "governance" / "decision_intelligence" / "EXECUTIVE_GRAPH_ADVERSARIAL_DEBATE_REPORT.md"
RECEIPT_OUTPUT_PATH = ROOT / "reports" / "executive_graph_adversarial_debate_receipt.json"

CONTEXT_DATA = """
### EMPIRICAL BASELINE OF GRAPHDB & DECISION INTELLIGENCE ENGINE IN CODEBASE:

1. Canonical Storage & Graph Schema:
   - `graph/nee_graph.sqlite`: 17MB active graph database storing nodes, sources, extraction runs, evidence items, and assertions.
   - `storage/models.py`: Defines CanonicalGraphNode, AssertionRecord, and InternalAdvisoryNode.
   - `graph/schema.sql`: Contains claim_confidence_hitl_matrix, account_team_next_best_action, briefing_question_catalog, and node/edge tables.
   - Vector Sidecars: Dense vector, sparse weight, colbert token, and retrieval records.

2. Strategic Reasoning & Decision Engine:
   - `dual_pipeline/strategic_reasoning.py`: 18 knowledge roles (current_state_fact, business_problem, target_outcome, capability, option, evaluation_criterion, constraint, risk, dependency, decision, metric, etc.).
   - `dual_pipeline/graph_retrieval.py`: BM25 + dense vector seeding, 3-hop typed expansion policy, degree bounding, and bridge node governance.
   - `dual_pipeline/decision_engine.py`: Provider-free context assembly, multi-criteria trade-off evaluation, and option rank decoupling from vector scores.

3. Rule C-19 Career Leverage & Artifact Independence Firewall:
   - Strict partitioning between team-facing deliverables (Outputs 01 & 02), shared enterprise graph knowledge (Output 03 & CANONICAL_GRAPH.db), and private career leverage (INTERNAL_ADVISORY enclave).
   - Confidential advisory shadow graph (shadow_graph_nodes, shadow_graph_edges, namespace: 'INTERNAL_ADVISORY', is_quarantined: 1, stealth_score: 1.0).
   - Master prompt contract: `governance/decision_intelligence/EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M.md` (10 strategic intelligence dimensions).

4. Current Gaps & Unexploited Capabilities:
   - GraphDB currently centers around meeting ingestion, entity resolution, and assertion tracking rather than high-level TOGAF/BIZBOK enterprise capability value stream mapping.
   - No explicit table or query modeling financialized blast radius ($ cost of delay, SAIDI outage exposure, downtime cost, CapEx/OpEx implications).
   - Informal political power structures (veto holders, champion vs skeptic stances, key-person fragility) exist conceptually in Rule C-19 but lack automated query surfaces.
   - Cross-client pattern abstraction and thought leadership artifact generation are currently ad-hoc rather than first-class materialized views.
"""

AGENT_1_PROMPT = f"""You are Agent 1: The Fortune 50 Enterprise CIO / CTO (The Client Wow Factor).
Your mandate is to evaluate whether this GraphDB architecture would genuinely WOW you as an enterprise client executive or if it looks like just another glorified meeting transcription and Jira tracker.

Ground your audit in the baseline context:
{CONTEXT_DATA}

Address:
1. What truly wows an Enterprise CIO/CTO: We do not care about raw node counts or meeting minutes. We care about:
   - Architectural blast radius: When an API or legacy core system changes, can the graph predict every downstream impact, business process disruption, and regulatory exposure?
   - Unvarnished technical debt & hidden dependencies: Can the graph reveal multi-million dollar cross-silo duplications (e.g. 3 business units independently building GenAI pipelines)?
   - Value stream mapping: Connecting low-level assets (Databricks, Kafka, Maximo) directly to business metrics (SAIDI reliability, working capital, operational margins).
2. Forensic critique of current GraphDB: Where does it fall short of an executive cognitive exoskeleton? What is missing from schemas, queries, and views?
3. Actionable demands: What 3 specific capabilities MUST be added to GraphDB to make you consider the operator an indispensable $1.2M technology visionary?
Be direct, executive-grade, unsparing, and commercially grounded."""

AGENT_2_PROMPT = f"""You are Agent 2: The PartnerAdvisory Senior Managing Director & Practice Leader (Consulting Commercial Leverage).
Your mandate is to evaluate how this GraphDB architecture positions the operator within PartnerAdvisory and against elite Tier-1 strategy firms (McKinsey, BCG, Accenture).

Ground your audit in the baseline context:
{CONTEXT_DATA}

Address:
1. The $1.2M Technology Executive Trajectory at PartnerAdvisory:
   - A $1.2M leader is not just a delivery lead; they generate $10M+ in follow-on pipeline, protect 45%+ gross margin on engagements, prevent scope creep, and create repeatable consulting IP.
   - How can GraphDB be leveraged to detect account expansion triggers (pre-RFP advisory briefs, unaddressed client pain points)?
2. Commercial de-risking:
   - How can the graph model SOW boundaries, capacity allocations (e.g. 106-SWE capacity charters), and fixed-fee delivery risk?
3. Consulting IP harvesting & multi-account leverage:
   - How does the graph harvest cross-client reusable blueprints while strictly upholding NDA/confidentiality barriers?
4. Forensic critique and recommendations:
   - What commercial tables, views, and metrics must GraphDB incorporate to make the operator's consulting leadership undeniably elite?
Deliver an incisive, partner-level commercial analysis."""

AGENT_3_PROMPT = f"""You are Agent 3: The Chief Knowledge Graph Architect & Ontologist (Graph Topology & Rigor).
Your mandate is to evaluate the technical, topological, and ontological foundation of the GraphDB.

Ground your audit in the baseline context:
{CONTEXT_DATA}

Address:
1. Multi-hop latent link discovery:
   - Can the engine traverse 3 to 5 hops across heterogeneous entity types (Initiative -> Workstream -> Dependency -> Blocked ADR -> Political Veto) without combinatorial explosion or semantic drift?
2. Bi-temporal rigor (W3) & epistemic validity:
   - How well does the schema distinguish between physical event time (observed_at), commitment expiration (stale_after), and database ingestion time?
   - Dialectical tension: Does the graph preserve contradictory assertions across silos without premature collapse?
3. Schema completeness:
   - Critique current tables (claim, assertion, node, search_document) vs what is required for TOGAF business capabilities, value streams, architectural decision records (ADRs), and blast radius metrics.
4. Concrete architectural recommendations:
   - Specify exact schema enhancements, index strategies, and traversal algorithms needed to make GraphDB a state-of-the-art executive intelligence engine.
Be uncompromising in theoretical, mathematical, and architectural depth."""

AGENT_4_PROMPT = f"""You are Agent 4: The Career Strategy & Advisory Firewall Enclave Guardian (Rule C-19 Enclave).
Your mandate is to evaluate how the user's private career strategy ($1.2M compensation trajectory, political power maps, negotiation leverage, executive positioning) is operationalized and protected.

Ground your audit in the baseline context:
{CONTEXT_DATA}

Address:
1. The Rule C-19 / Confidential Advisory Firewall:
   - Why the strict separation between public/client deliverables (Outputs 01 & 02), shared enterprise graph (CANONICAL_GRAPH.db / Output 03), and the private INTERNAL_ADVISORY shadow graph is vital.
   - What happens if personal career dynamics or internal PartnerAdvisory account politics leak to client leadership? (Catastrophic breach of trust).
2. Operationalizing the Private Cognitive Exoskeleton:
   - How should the INTERNAL_ADVISORY shadow graph model:
     * Informal power topology: Who possesses informal technical veto power vs nominal title authority?
     * Stakeholder alignment: Champion, Skeptic, Detractor, Gatekeeper, Passive Approver.
     * Key-person operational fragility: Single Points of Failure in human systems.
     * Career advancement milestones: Strategic influence plays, executive sponsor relationships, and compensation milestones.
3. Recommendations for shadow schema and query engine:
   - Recommend concrete schema extensions for stakeholder influence mapping and career leverage tracking that guarantee cryptographic isolation and zero client leakage.
Be politically astute, forensically discreet, and strategically protective."""

AGENT_5_PROMPT = f"""You are Agent 5: The Industry Thought Leadership & Frontier AI Strategist (Market Eminence & Master Verdict).
Your mandate is to evaluate how this engine establishes market eminence (publications, keynotes, board-level decision briefs) and to deliver the MASTER STRATEGIC VERDICT synthesizing all 5 agents.

Ground your evaluation in the baseline context:
{CONTEXT_DATA}

Address:
1. Industry Thought Leadership as a $1.2M Multiplier:
   - How can GraphDB data power top-tier industry whitepapers, Harvard Business Review / MIT Sloan Management Review grade articles, and Gartner/Forrester briefings on Decision Intelligence and Agentic Enterprise Architecture?
   - Automated generation of C-suite executive decision memorandums and board-level risk scorecards.
2. Dialectical Synthesis of Agents 1, 2, 3, and 4:
   - Reconcile Client Wow Factor (Agent 1), Consulting Commercial Edge (Agent 2), Ontological Rigor (Agent 3), and Career Enclave Security (Agent 4).
3. The Definitive Strategic Verdict:
   - Direct answer: Is the current GraphDB set up to provide an asymmetric competitive advantage for a $1.2M+ executive IT position?
   - Overall Readiness Score (0-100%) across the 10 strategic intelligence dimensions.
4. Prioritized Architectural Roadmap (Immediate Waves):
   - Immediate Wave 2 Schema & Model additions (Canonical and Shadow).
   - Immediate Query & Exoskeleton Engine additions.
   - Expected ROI and career leverage timeline.
Deliver a definitive, master-level executive ruling."""


def run_executive_graph_adversarial_debate(
    model: Optional[str] = None, reasoning_effort: Optional[str] = None
) -> Dict[str, Any]:
    tier_model, tier_effort = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
    resolved_model = model or tier_model
    resolved_effort = reasoning_effort or tier_effort or "high"

    print("=" * 80)
    print("  LIVE ADVERSARIAL 5-AGENT DEBATE: GRAPHDB EXECUTIVE COMPETITIVE ADVANTAGE")
    print("  Target Outcome:   $1.2M+ Executive IT Position (Enterprise CTO/CIO/Partner)")
    print(f"  Target Model:     {resolved_model}")
    print(f"  Reasoning Effort: {resolved_effort}")
    print("  Execution Mode:   Operator-Directed Live Provider (allow_mock=False)")
    print("=" * 80)

    # 1. Resolve live credentials (fail-closed)
    creds = resolve_openai_credentials(allow_mock=False, model=resolved_model, reasoning_effort=resolved_effort)
    if not creds.has_live_key:
        print("❌ FATAL: Live API key missing from env_di. Fail-closed.")
        sys.exit(1)

    print(f"✅ Credentials resolved from {creds.source} (Key: {creds.masked_key})")
    client, creds = get_openai_client(allow_mock=False, model=resolved_model, reasoning_effort=resolved_effort)

    rounds: List[Dict[str, Any]] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_start_time = time.time()

    conversation_history = [
        {
            "role": "system",
            "content": (
                "You are participating in an elite, adversarial 5-agent strategic executive panel auditing "
                "the decision-intelligence-engine repository. You must deliver unvarnished, high-stakes, "
                "financially and architecturally rigorous analysis fitting for a $1.2M+ technology executive."
            ),
        }
    ]

    steps = [
        ("Agent 1: Enterprise Client CIO / CTO (Client Wow Factor)", AGENT_1_PROMPT, "client_cio"),
        ("Agent 2: PartnerAdvisory Consulting MD / Partner (Commercial Leverage)", AGENT_2_PROMPT, "consulting_md"),
        ("Agent 3: Chief Knowledge Graph Architect (Ontological Rigor)", AGENT_3_PROMPT, "graph_architect"),
        ("Agent 4: Career Strategy & Firewall Guardian (Rule C-19 Enclave)", AGENT_4_PROMPT, "firewall_guardian"),
        ("Agent 5: Industry Thought Leadership & Frontier Strategist (Master Verdict)", AGENT_5_PROMPT, "master_verdict"),
    ]

    full_transcript_parts: List[str] = [
        "# Live Dialectical 5-Agent Adversarial Debate: GraphDB Executive Competitive Advantage\n\n",
        "**Target Strategic Objective**: $1.2M+ Annual Compensation Executive IT Position  \n",
        f"**Evaluating Model**: `{resolved_model}` ({resolved_effort} reasoning effort)  \n",
        f"**Execution Timestamp**: {dt.datetime.now(dt.timezone.utc).strftime('%m/%d/%Y %H:%M:%S UTC')}  \n",
        f"**Credential Source**: `{creds.source}` (Active OpenAI Endpoint)  \n",
        "**Governance Mandate**: `EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M.md`  \n\n",
        "---\n",
    ]

    for label, prompt_text, role_key in steps:
        print(f"\n>>> Dispatched [{label}] to {resolved_model} ({resolved_effort})...", flush=True)
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
    full_transcript_text = "".join(full_transcript_parts)
    DEBATE_OUTPUT_PATH.write_text(full_transcript_text, encoding="utf-8")
    print(f"\n✅ Live 5-agent debate transcript saved to: {DEBATE_OUTPUT_PATH}")

    # 3. Calculate SHA-256 digest
    transcript_digest = hashlib.sha256(full_transcript_text.encode("utf-8")).hexdigest()

    # 4. Write durable execution receipt
    RECEIPT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    receipt_data = {
        "status": "SUCCESS",
        "debate_topic": "GraphDB Executive Competitive Advantage for $1.2M+ Outcome",
        "model_id": resolved_model,
        "reasoning_effort": resolved_effort,
        "credential_source": creds.source,
        "total_duration_seconds": total_duration,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "transcript_path": str(DEBATE_OUTPUT_PATH.relative_to(ROOT)),
        "transcript_sha256": transcript_digest,
        "rounds": [
            {
                "round": r["round"],
                "role": r["role"],
                "duration_seconds": r["duration_seconds"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
            }
            for r in rounds
        ],
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    RECEIPT_OUTPUT_PATH.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
    print(f"✅ Live execution receipt saved to: {RECEIPT_OUTPUT_PATH}")

    print("=" * 80)
    print(f"  TOTAL DEBATE TOKENS: {total_prompt_tokens + total_completion_tokens} | TIME: {total_duration}s")
    print(f"  TRANSCRIPT DIGEST:   {transcript_digest}")
    print("=" * 80)
    return receipt_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live 5-Agent Adversarial Debate Runner")
    parser.add_argument("--model", default=None, help="Target model ID (defaults to gpt-6-astra)")
    parser.add_argument("--reasoning-effort", default=None, help="Reasoning effort (defaults to high)")
    args = parser.parse_args()

    run_executive_graph_adversarial_debate(model=args.model, reasoning_effort=args.reasoning_effort)
