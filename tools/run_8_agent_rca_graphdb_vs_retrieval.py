#!/usr/bin/env python3
"""tools/run_8_agent_rca_graphdb_vs_retrieval.py

Live 8-Agent Adversarial Root Cause Analysis (RCA):
Is the failure to reflect updated/finalized components (e.g. removal of the 77.5% row)
fundamentally a GraphDB Content/Ingestion Issue or a Retrieval Issue in Copilot?

Executes live against configured reasoning model via authentic OpenAI credentials from env_di.

Panel Configuration:
TEAM ALPHA: GRAPHDB CONTENT & INGESTION DEFECT THESIS
1. Agent 1: Ingestion Invalidation & Tombstone Auditor
2. Agent 2: Multimodal / Chat Ingress Blindspot Specialist
3. Agent 3: Temporal Assertion & Currentness State Lead
4. Agent 4: Entity Resolution & Conflicting Fact Reconciler

TEAM BETA: COPILOT RETRIEVAL, RANKING & CONTEXT DEFECT THESIS
5. Agent 5: Retrieval Salience & Transcript Density Bias Analyst
6. Agent 6: Temporal Recency Decay & Version-Aware Ranker
7. Agent 7: Copilot Semantic Cache & Session Invalidation Guard
8. Agent 8: Context Synthesis & Epistemic Uncertainty Arbiter

JUDICIAL SYNTHESIS:
9. Chief Judicial Arbiter & Enterprise Knowledge Systems Architect
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.credentials import (
    get_openai_client,
    resolve_openai_credentials,
    sanitize_model_call_kwargs,
)
from unified_pipeline.model_registry import ModelRegistry, ModelTier

REPORT_OUTPUT_PATH = ROOT / "governance" / "decision_intelligence" / "RCA_GRAPHDB_VS_RETRIEVAL_STALENESS.md"
RECEIPT_OUTPUT_PATH = ROOT / "reports" / "rca_graphdb_vs_retrieval_staleness_receipt.json"
AUDIT_RECEIPT_PATH = ROOT / "reports" / "audit_0924_data_lineage_receipt.json"

CONTEXT_DATA = """
### EMPIRICAL FORENSIC CONTEXT: THE 77.5% ROW STALENESS DEFECT

1. Background & Incident Description:
   - On 09/24/2026, the team held multiple working sessions for Project LENS:
     a) 11:05 AM - 01:27 PM ET: Waterfall_Jen_Jake_Amit (142 min, 100KB transcript, 20KB recap).
        During this session, an interim 77.5% metric row was discussed as an open question/transition from 87%.
        It was codified in 02_executive_recap.md as Blocker #5:
        "| 5 | Blocker | Resolve whether the 77.5% row includes additional model, prompt, or rule changes and document why the percentage decreased. | Open |".
     b) 02:53 PM - 03:56 PM ET: Slides_Jen_Amit (50 min, architecture & delivery charter). 77.5% was not mentioned.
     c) 04:00 PM - 04:30 PM ET: Sprint_Mandy (harper Hoffman rules of thumb: 150 assets/turbine, 5 sites, 68 WOs completed).
     d) 04:06 PM - 04:27 PM ET: Sprint_PartnerAdvisory_Nea (Joint sprint review with Mandy and Alexandra Coro).
        Presented the curated 83 golden assets (~100% auto-acceptance, 88-90% OCR). The 77.5% row was completely absent.
     e) 04:44 PM - 04:57 PM ET: Mandy_Jen (Field progress catchup).
   - During the afternoon working sessions, the operator (Amit Ayer) was actively updating the slides and tables, pasting screenshots into the chat and iterating until the 77.5% row was completely removed from the finalized table.
   - However, on 09/25/2026, when the operator asked Copilot to "synthesize meeting items to complete SOW from all Lens calls yesterday 9.24.2026" and "copilot all open workstreams from discussions yesterday", Copilot aggregated the information from the graph/recap and presented the 77.5% row as an active open blocker!
   - Defect: The system aggregated interim updates from the morning meeting but failed to reflect the finalized updates where 77.5% was removed.

2. Empirical State in Codebase & Storage:
   - Deliverables on disk:
     - Only `Waterfall_Jen_Jake_Amit/02_Waterfall_..._executive_recap.md` contains "77.5" and "Blocker 5".
     - All 4 subsequent afternoon meeting folders have ZERO mentions of 77.5%.
     - Chat-pasted screenshots were never ingested into Stage U0, leaving no file or evidence item in output/ or storage.
   - Active GraphDB (`.local/unified_pipeline/generations/generation-6b972fcf462da2799e2af1807c0f/graph.sqlite`):
     - Contains `assertion` table with schema:
       `assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state, valid_from, valid_to, superseded_at, superseded_by_assertion_id, canonical_status, retrieval_eligible`.
     - Waterfall assertions were ingested as candidate assertions under `source-b6b729dea4aafaed4dab64b1`.
     - `currentness_state` was left as default ('current' or null); `superseded_at` is null for all Waterfall assertions.
     - No retraction, invalidation, or tombstone assertion was ever written to GraphDB when afternoon sessions ran or when screenshots were pasted in chat.
   - Retrieval Engine (`unified_pipeline/retrieval/`):
     - Uses hybrid BM25 full-text search (`search_fts`) + dense BGE-M3 vectors.
     - Has no intra-day temporal recency decay function: all items dated "09-24-2026" are assigned identical temporal weight regardless of hour (11:05 AM vs 16:44 PM).
     - Does not filter on `currentness_state = 'current'` or check for intra-day supersession.
     - Generative prompt assembly presents high-salience ledger items directly to the model.

3. The Core Adversarial Debate:
   - Is this fundamentally a **GraphDB Content & Ingestion Issue** (stale interim state ingested into GraphDB, failure to ingest chat/screenshots, lack of retraction/tombstoning/invalidation, lack of monotonic entity state versioning)?
   - OR is this fundamentally a **Retrieval Issue in Copilot** (salience scoring bias favoring dense multi-turn transcripts over sparse recaps, lack of intra-day temporal recency decay, semantic cache stale hits, or generative synthesis failing to cross-check downstream sessions)?
"""

AGENTS_CONFIG = [
    {
        "id": "agent_1_tombstone",
        "name": "Agent 1: Ingestion Invalidation & Tombstone Auditor",
        "team": "Team Alpha (GraphDB Content Fault)",
        "role": "Audits the absence of assertion retraction, supersession, and tombstoning in the ingestion lifecycle.",
        "prompt": """You are Agent 1: Ingestion Invalidation & Tombstone Auditor on Team Alpha.
Argue aggressively and forensically why the 77.5% staleness defect is fundamentally a GraphDB Ingestion & Content fault.
Focus on:
1. Why the intake pipeline (pipeline_a_intake, RawTranscriptPipeline, prepare_evidence_envelope_intake) creates immutable assertions from morning interim meetings but provides ZERO mechanism for subsequent intra-day runs or manual edits to invalidate, retract, or tombstone prior assertions.
2. How the assertion table's `superseded_at` and `superseded_by_assertion_id` columns remained entirely unpopulated, causing obsolete interim assertions to remain permanently active in GraphDB.
3. Cite exact architectural modules and explain why retrieval cannot be blamed for retrieving active, valid assertions that GraphDB failed to deprecate."""
    },
    {
        "id": "agent_2_chat_ingress",
        "name": "Agent 2: Multimodal / Chat Ingress Blindspot Specialist",
        "team": "Team Alpha (GraphDB Content Fault)",
        "role": "Audits the complete disconnect between chat UI interactions (pasted screenshots) and the governed ingestion gateway.",
        "prompt": """You are Agent 2: Multimodal / Chat Ingress Blindspot Specialist on Team Alpha.
Argue why the root cause of the staleness defect is an architectural blindspot in Stage U0 ingestion ingress.
Focus on:
1. In reality, working sessions are multimodal: the operator pasted screenshots in chat and updated tables iteratively until 77.5% was removed.
2. The ingestion architecture (nee ingest-meeting, LiveMeetingRecorder) ONLY ingests meeting audio (.wav) or formal transcript text (.md). It has zero ingress listener, webhook, or bridge to capture chat-pasted screenshots, OCR them, and emit evidence envelopes.
3. Because the finalized truth only existed in chat screenshots and ephemeral slides, GraphDB's content boundary was fundamentally incomplete. Prove why this is a Stage U0 ingestion architectural defect."""
    },
    {
        "id": "agent_3_temporal_assertion",
        "name": "Agent 3: Temporal Assertion & Currentness State Lead",
        "team": "Team Alpha (GraphDB Content Fault)",
        "role": "Audits the failure of temporal state resolution and the W3 temporal hygiene invariant.",
        "prompt": """You are Agent 3: Temporal Assertion & Currentness State Lead on Team Alpha.
Argue why the staleness defect is a failure of GraphDB's temporal ontology and state management.
Focus on:
1. Rule C-07 and the W3 Semantic Spine mandate temporal attributes (observed_at, stale_after), and the schema contains `currentness_state` ('current', 'superseded', 'retracted').
2. In practice, the ingestion engine treats all meeting extractions as static, permanent point-in-time facts. It never evaluates whether an assertion made at 11:05 AM has been rendered stale by a session at 16:06 PM on the same date.
3. Explain why the failure to compute and update `currentness_state` at GraphDB mutation time directly corrupts downstream query integrity."""
    },
    {
        "id": "agent_4_entity_resolution",
        "name": "Agent 4: Entity Resolution & Conflicting Fact Reconciler",
        "team": "Team Alpha (GraphDB Content Fault)",
        "role": "Audits how GraphDB models tabular artifacts and reconciles conflicting assertions across sessions.",
        "prompt": """You are Agent 4: Entity Resolution & Conflicting Fact Reconciler on Team Alpha.
Argue why the defect stems from GraphDB's inability to model evolving structured tables as first-class versioned entities.
Focus on:
1. The LENS Waterfall table is a composite entity with evolving iterations (27/130 -> 168/193 -> 64/83 -> 88-90%).
2. Instead of resolving the table as a monotonic versioned entity (`table_lens_waterfall` version 1, 2, 3), GraphDB decomposed the table into fragmented, atomized candidate assertions (`assertion-d1ecc...`).
3. Because there was no entity-level identity binding across iterations, GraphDB never detected that the finalized table in the afternoon was a mutation of the morning draft, making conflict detection impossible."""
    },
    {
        "id": "agent_5_retrieval_density",
        "name": "Agent 5: Retrieval Salience & Transcript Density Bias Analyst",
        "team": "Team Beta (Retrieval & Copilot Fault)",
        "role": "Audits why hybrid retrieval algorithms over-index on massive verbose transcripts over sparse final recaps.",
        "prompt": """You are Agent 5: Retrieval Salience & Transcript Density Bias Analyst on Team Beta.
Counter Team Alpha and argue aggressively why the staleness defect is fundamentally a RETRIEVAL and SCORING BIAS fault.
Focus on:
1. The morning session (Waterfall_Jen_Jake_Amit) was 142 minutes long, yielding a 100KB transcript with repeated dialogue on 77.5%. The afternoon sessions were short (21 min, 13 min) with concise recaps.
2. Hybrid BM25 full-text search and dense vector retrieval (BGE-M3) inherently reward high term frequency, high token volume, and multi-turn repetition.
3. When Copilot queried for 'open workstreams' and 'blockers', the retrieval engine overwhelmingly ranked the dense morning transcript and recap above the afternoon summaries. GraphDB contained the 83 golden assets from the afternoon, but retrieval starved them of relevance because of length and density bias."""
    },
    {
        "id": "agent_6_recency_decay",
        "name": "Agent 6: Temporal Recency Decay & Version-Aware Ranker",
        "team": "Team Beta (Retrieval & Copilot Fault)",
        "role": "Audits the total absence of intra-day temporal recency weighting in Copilot's retrieval algorithm.",
        "prompt": """You are Agent 6: Temporal Recency Decay & Version-Aware Ranker on Team Beta.
Argue why the staleness defect is caused by a missing temporal recency decay function in the retrieval engine.
Focus on:
1. Modern enterprise retrieval for fast-paced working days requires exponential or linear intra-day time decay: information from 16:44 PM must mathematically outrank information from 11:05 AM when querying for current state.
2. In the current retrieval engine (unified_pipeline/graph_retrieval.py, search_fts), timestamps are treated as coarse dates ('09-24-2026') without sub-day chronological ordering.
3. Even without GraphDB modifications, an intelligent, version-aware retrieval engine should filter out or down-weight earlier sessions in favor of the latest session of the day. The retrieval engine completely failed to do this."""
    },
    {
        "id": "agent_7_semantic_cache",
        "name": "Agent 7: Copilot Semantic Cache & Session Invalidation Guard",
        "team": "Team Beta (Retrieval & Copilot Fault)",
        "role": "Audits whether Copilot cached interim query responses or failed to invalidate session context.",
        "prompt": """You are Agent 7: Copilot Semantic Cache & Session Invalidation Guard on Team Beta.
Argue why Copilot's session memory, semantic cache, and prompt assembly pipeline are responsible for this defect.
Focus on:
1. How Copilot's query routing and semantic cache (unified_pipeline/semantic_cache.py, live_meeting_recorder_core/copilot.py) handle intra-day updates.
2. If an operator asks a question earlier in the day and repeats it later, does the semantic cache return stale interim query responses without validating whether new files or outputs appeared?
3. How Copilot's prompt context assembler indiscriminately concatenated markdown Action & Governance ledgers without performing cross-ledger deduplication or conflict resolution."""
    },
    {
        "id": "agent_8_context_synthesis",
        "name": "Agent 8: Context Synthesis & Epistemic Uncertainty Arbiter",
        "team": "Team Beta (Retrieval & Copilot Fault)",
        "role": "Audits why the LLM generation layer asserted an interim blocker as active truth without epistemic cross-checking.",
        "prompt": """You are Agent 8: Context Synthesis & Epistemic Uncertainty Arbiter on Team Beta.
Argue why the final failure occurred in LLM Context Generation and Synthesis.
Focus on:
1. When Copilot was asked to synthesize all open workstreams from yesterday's calls, it was provided the text of ALL 5 sessions in its context window.
2. In session 1 (Waterfall), it saw Blocker #5 (77.5%). In session 4 (Sprint_PartnerAdvisory_Nea), it saw that the team presented the 83 golden assets with near-100% auto-acceptance and 88-90% OCR accuracy, with ZERO mention of 77.5%.
3. A properly prompted frontier reasoning model should possess chronological discernment: it should recognize that a morning blocker regarding an unproven percentage is rendered moot or superseded when the team presents a different, verified baseline in the afternoon. The generative prompt failed to instruct the model to perform chronological state resolution across sequential meetings."""
    }
]

JUDICIAL_ARBITER_PROMPT = """You are the Chief Judicial Arbiter & Principal Knowledge Systems Architect.
You have observed the exhaustive adversarial debate between Team Alpha (GraphDB Content & Ingestion Fault) and Team Beta (Copilot Retrieval & Generation Fault) regarding the 77.5% metric staleness defect.

Deliver the Authoritative Judicial Verdict and Comprehensive RCA Report in clean GitHub Markdown:
1. Executive Verdict: Answer unequivocally: Is this a GraphDB content issue, a Retrieval issue in Copilot, or a Dual-Fault interaction? Provide exact percentage responsibility (e.g. 60% GraphDB / 40% Retrieval).
2. Forensic Breakdown of the Failure Chain: Map the exact sequence from morning creation to afternoon chat editing to Copilot retrieval.
3. Cross-Examination & Evaluation of Arguments:
   - Critically evaluate Team Alpha's claims (Tombstones, Chat Ingress, Temporal State, Entity Versioning).
   - Critically evaluate Team Beta's claims (Density Bias, Recency Decay, Semantic Cache, Epistemic Synthesis).
4. The Dual-Fault Synthesis: Explain why neither layer alone can solve the problem, and why a disjointed architecture caused the defect.
5. Concrete Engineering Remediations:
   - Provide concrete, non-speculative remediations for GraphDB (Ingestion & Schema).
   - Provide concrete remediations for Retrieval & Copilot (Ranking, Filtering, Chronological Context Assembly).
   - Define exact validation gates to prevent regression.
"""


def execute_agent(client: Any, model: str, agent_cfg: Dict[str, Any], context: str) -> Dict[str, Any]:
    """Executes a single adversarial agent against configured reasoning model."""
    t0 = time.perf_counter()
    four_pillars = (
        "\n\nMANDATORY EXECUTION TRANSPARENCY:\n"
        "Include Four Pillars: 1. Cognitive Breakdown 2. Reasoning Steps 3. Architectural Touchpoints 4. Telemetry Metadata.\n"
    )
    system_msg = f"{agent_cfg['prompt']}{four_pillars}\n\nUse the following empirical forensic context to ground every assertion:\n{context}"
    user_msg = f"Provide your full adversarial testimony as {agent_cfg['name']} ({agent_cfg['team']}). Detail the exact technical mechanisms, codebase files, schema fields, and failure points supporting your thesis."

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    kwargs = {
        "model": model,
        "messages": messages,
    }
    kwargs = sanitize_model_call_kwargs(model, kwargs)

    response = client.chat.completions.create(**kwargs)
    latency_s = time.perf_counter() - t0
    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)

    return {
        "agent_id": agent_cfg["id"], "name": agent_cfg["name"], "team": agent_cfg["team"],
        "latency_s": round(latency_s, 2), "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
        "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content,
    }


def execute_judicial_arbiter(client: Any, model: str, context: str, agent_testimonies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Executes the Chief Judicial Arbiter to synthesize all testimonies into the final verdict."""
    t0 = time.perf_counter()
    testimonies_text = "\n\n".join([
        f"### TESTIMONY: {a['name']} ({a['team']})\n{a['content']}"
        for a in agent_testimonies
    ])
    four_pillars = "\n\nMANDATORY EXECUTION TRANSPARENCY:\nGround verdict in empirical evidence across both teams with Four Pillars of Execution Reflection.\n"
    system_msg = f"{JUDICIAL_ARBITER_PROMPT}{four_pillars}\n\nFORENSIC EVIDENCE BASELINE:\n{context}"
    user_msg = f"Synthesize all 8 adversarial agent testimonies below and deliver the authoritative RCA Verdict and Report:\n\n{testimonies_text}"

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]
    kwargs = {
        "model": model,
        "messages": messages,
    }
    kwargs = sanitize_model_call_kwargs(model, kwargs)

    response = client.chat.completions.create(**kwargs)
    latency_s = time.perf_counter() - t0
    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)

    return {
        "agent_id": "judicial_arbiter", "name": "Chief Judicial Arbiter & Principal Knowledge Systems Architect",
        "latency_s": round(latency_s, 2), "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
        "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 8-Agent Adversarial RCA via frontier reasoning")
    parser.add_argument("--model", default=None, help="Frontier model to evaluate")
    args = parser.parse_args()

    target_model = args.model or ModelRegistry.resolve_model(ModelTier.FRONTIER_HIGH_REASONING)
    print(f"🔒 Resolving OpenAI credentials for live execution ({target_model})...", flush=True)
    creds = resolve_openai_credentials(allow_mock=False, model=target_model)
    client, meta = get_openai_client(model=target_model, allow_mock=False)
    print(f"✅ Credentials verified (source: {creds.source}, mock: {creds.is_mock}). Launching 8 adversarial agents...", flush=True)

    t_start = dt.datetime.now(dt.timezone.utc)
    agent_results: List[Dict[str, Any]] = []

    # Execute all 8 agents concurrently with thread pool
    print("\n⚔️ Executing 8-Agent Adversarial Panel in parallel...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {
            executor.submit(execute_agent, client, target_model, cfg, CONTEXT_DATA): cfg
            for cfg in AGENTS_CONFIG
        }
        for future in concurrent.futures.as_completed(future_map):
            cfg = future_map[future]
            try:
                res = future.result()
                agent_results.append(res)
                print(f"   [DONE] {res['name']} ({res['latency_s']}s, {res['total_tokens']} tokens)", flush=True)
            except Exception as e:
                print(f"   ❌ [FAIL] {cfg['name']}: {e}", file=sys.stderr, flush=True)
                raise

    # Sort agent results by config order
    order_map = {cfg["id"]: i for i, cfg in enumerate(AGENTS_CONFIG)}
    agent_results.sort(key=lambda x: order_map.get(x["agent_id"], 99))

    print("\n⚖️ Executing Chief Judicial Arbiter for final verdict & synthesis...", flush=True)
    judicial_result = execute_judicial_arbiter(client, target_model, CONTEXT_DATA, agent_results)
    print(f"   [DONE] Judicial Arbiter Verdict synthesized ({judicial_result['latency_s']}s, {judicial_result['total_tokens']} tokens)", flush=True)

    # Compile Comprehensive Markdown Report
    report_lines = [
        "# Root Cause Analysis (RCA): GraphDB Content vs. Retrieval Staleness",
        "",
        f"**Evaluating Model:** `{target_model}` (Live OpenAI API, Frontier Reasoning)  ",
        f"**Evaluation Date:** {t_start.strftime('%m/%d/%Y %H:%M:%S UTC')}  ",
        "**Incident Reference:** LENS SOW Waterfall 77.5% Metric Staleness (09/24/2026 -> 09/25/2026)  ",
        "**Panel Composition:** 8 Adversarial Domain Agents + Chief Judicial Arbiter  ",
        "",
        "---",
        "",
        "## 1. Authoritative Judicial Verdict & Root Cause Matrix",
        "",
        judicial_result["content"],
        "",
        "---",
        "",
        "## 2. Adversarial Panel Testimonies",
        "",
    ]

    for a in agent_results:
        report_lines.extend([
            f"### {a['name']}",
            f"**Team:** {a['team']} | **Latency:** {a['latency_s']}s | **Tokens:** {a['total_tokens']} | **Digest:** `{a['content_sha256'][:16]}`",
            "",
            a["content"],
            "",
            "---",
            "",
        ])

    REPORT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    # Compile Durable JSON Execution Receipt
    tot_prompt = sum(a["prompt_tokens"] for a in agent_results) + judicial_result["prompt_tokens"]
    tot_comp = sum(a["completion_tokens"] for a in agent_results) + judicial_result["completion_tokens"]
    tot_tokens = tot_prompt + tot_comp

    receipt = {
        "status": "PASS",
        "command": f"python tools/run_8_agent_rca_graphdb_vs_retrieval.py --model {target_model}",
        "executed_at_utc": t_start.isoformat(),
        "model": target_model,
        "is_mock": False,
        "credential_source": creds.source,
        "total_agents": len(agent_results) + 1,
        "total_tokens": tot_tokens,
        "total_prompt_tokens": tot_prompt,
        "total_completion_tokens": tot_comp,
        "report_path": str(REPORT_OUTPUT_PATH),
        "report_sha256": hashlib.sha256(REPORT_OUTPUT_PATH.read_bytes()).hexdigest(),
        "agent_telemetry": [
            {
                "agent_id": a["agent_id"],
                "name": a["name"],
                "team": a["team"],
                "latency_s": a["latency_s"],
                "tokens": a["total_tokens"],
                "sha256": a["content_sha256"],
            }
            for a in agent_results
        ] + [{
            "agent_id": judicial_result["agent_id"],
            "name": judicial_result["name"],
            "team": "Judicial Synthesis",
            "latency_s": judicial_result["latency_s"],
            "tokens": judicial_result["total_tokens"],
            "sha256": judicial_result["content_sha256"],
        }],
    }

    RECEIPT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RECEIPT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    print(f"\n📄 Comprehensive RCA Report written to: {REPORT_OUTPUT_PATH}")
    print(f"🧾 Durable Execution Receipt written to: {RECEIPT_OUTPUT_PATH}")
    print(json.dumps({
        "status": "PASS",
        "total_agents_executed": len(agent_results) + 1,
        "model": target_model,
        "total_tokens": tot_tokens,
        "report_bytes": REPORT_OUTPUT_PATH.stat().st_size,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
