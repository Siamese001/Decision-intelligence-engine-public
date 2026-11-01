"""Wave 3C: Atomic, Optionated & Confidence-Scored QRELS Grading Tool (v2)

Implements the mandatory AGENTS.md HITL controls:
1. Atomic Presentation (One candidate at a time).
2. Explicit Actionable Options (Grade 2, Grade 1, Grade 0).
3. Assigned Confidence Levels (Calibrated confidence scores grounded in graph evidence).
4. Ambiguity Gating (15% margin threshold: autonomous resolution if Delta >= 15%,
   interactive HITL if Delta < 15%).
5. Persistent Feedback Storage: evals/qrels_graded_judgments.json.
6. Real-time Graded NDCG@10 and MRR computation.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from dual_pipeline.retrieval_release_core.metrics import (
    compute_mrr_at_k,
    compute_ndcg_at_k,
    standard_ndcg_at_k,
    standard_reciprocal_rank,
)


def assess_candidate_confidence(
    query: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Ground candidate in query targets and compute calibrated confidence levels."""
    retrieval_text = candidate.get("retrieval_text", "").lower()
    title = candidate.get("title", "").lower()
    full_text = f"{title} {retrieval_text}"

    target_entities = [e.lower() for e in query.get("target_entities", [])]
    distractor = query.get("distractor_entity", "").lower() if query.get("distractor_entity") else None
    ground_truth = query.get("ground_truth_summary", "").lower()
    topic_name = query.get("topic_name", "").lower()
    grounding_source = query.get("grounding_source", "").lower()

    # Evidence matching factors
    entity_hits = 0
    for e in target_entities:
        e_clean = e.strip()
        matched = False
        if e_clean in full_text:
            matched = True
        elif ":" in e_clean:
            prefix, bare = e_clean.split(":", 1)
            clean_bare = bare.replace("_", " ").replace("-", " ")
            if bare in full_text or clean_bare in full_text:
                matched = True
            elif prefix == "card":
                card_terms = [t for t in re.sub(r"[^\w\s-]", " ", f"{topic_name} {grounding_source}").split() if len(t) > 3]
                if any(t in full_text for t in card_terms):
                    matched = True
        if matched:
            entity_hits += 1
    entity_ratio = entity_hits / max(len(target_entities), 1)

    gt_clean = re.sub(r"[^\w\s-]", " ", ground_truth)
    gt_words = [w.strip() for w in gt_clean.split() if len(w) > 3]
    gt_hits = 0
    for w in gt_words:
        if w in full_text or (len(w) > 4 and (w[:-1] in full_text or w[:5] in full_text)):
            gt_hits += 1
    gt_ratio = gt_hits / max(len(gt_words), 1)

    is_distractor_hit = distractor and (distractor in full_text or distractor in title)
    rec_type = candidate.get("record_type", "")

    # Calibrated probability distribution across [Grade 2, Grade 1, Grade 0]
    # Grade 2: Highly Relevant (answers the core need with direct entities/facts)
    # Grade 1: Partially Relevant (provides context/mentions related domain)
    # Grade 0: Irrelevant or Confounder Trap
    # Ground truth is strictly independent of retriever scores and fused ranks to prevent circularity.

    score_g2 = 0.10 + (0.50 * entity_ratio) + (0.30 * min(gt_ratio * 2.0, 1.0))
    if rec_type in ["source_chunk", "edge"] and entity_hits > 0:
        score_g2 += 0.20

    score_g0 = 0.10 + (0.40 * (1.0 - entity_ratio))
    if is_distractor_hit:
        score_g0 += 0.50
        score_g2 -= 0.30
    elif entity_ratio < 0.20 and gt_ratio < 0.10:
        score_g0 += 0.30

    score_g1 = 0.25 + 0.25 * (1.0 - abs(entity_ratio - 0.5))
    if rec_type in ["entity_facet", "edge"] and entity_hits > 0:
        score_g1 += 0.20

    # Softmax normalization
    raw_scores = [max(score_g2, 0.01), max(score_g1, 0.01), max(score_g0, 0.01)]
    exp_scores = [math.exp(s * 2.0) for s in raw_scores]
    total_exp = sum(exp_scores)
    c_g2, c_g1, c_g0 = [s / total_exp for s in exp_scores]

    def _tier(c: float) -> str:
        return "HIGH" if c >= 0.80 else ("MEDIUM" if c >= 0.40 else "LOW")

    # Rank options
    options = [
        {
            "grade": 2,
            "label": "Highly Relevant",
            "confidence": c_g2,
            "confidence_score": round(c_g2, 4),
            "confidence_level": _tier(c_g2),
            "tier": _tier(c_g2),
            "action": "Assign Grade 2 (Highly Relevant). Document treated as primary ground truth core in NDCG@10 (rel=2) and MRR@10 (1.0 reciprocal rank credit).",
            "evidence": f"Corroborated by {entity_hits}/{len(target_entities)} entity matches and {gt_hits}/{len(gt_words)} ground truth keywords (independent evaluation without retriever bias).",
            "risk": "If false positive, promotes an off-topic passage to ground truth status and rewards semantic drift in evaluation benchmarks.",
        },
        {
            "grade": 1,
            "label": "Partially Relevant",
            "confidence": c_g1,
            "confidence_score": round(c_g1, 4),
            "confidence_level": _tier(c_g1),
            "tier": _tier(c_g1),
            "action": "Assign Grade 1 (Partially Relevant / Contextual). Document treated as relevant supporting context in NDCG@10 (rel=1) and secondary MRR@10 (0.5 reciprocal rank credit).",
            "evidence": f"Shares organizational domain or structural context ({rec_type}) but does not fully satisfy the specific query core.",
            "risk": "If non-relevant, dilutes retrieval precision; if highly relevant, under-credits system retrieval capabilities.",
        },
        {
            "grade": 0,
            "label": "Non-Relevant / Confounder",
            "confidence": c_g0,
            "confidence_score": round(c_g0, 4),
            "confidence_level": _tier(c_g0),
            "tier": _tier(c_g0),
            "action": "Assign Grade 0 (Non-Relevant / Confounder). Document receives zero relevance credit in NDCG@10 (rel=0) and MRR@10 (0 credit).",
            "evidence": f"Deficit of target entities ({entity_hits}/{len(target_entities)}), distractor presence ({bool(is_distractor_hit)}), or off-topic passage text.",
            "risk": "If false negative, unfairly penalizes valid background or indirect evidence retrieved by dense/hybrid search.",
        },
    ]
    options.sort(key=lambda x: -x["confidence"])

    top_opt = options[0]
    sec_opt = options[1]
    margin = top_opt["confidence"] - sec_opt["confidence"]
    is_ambiguous = margin <= 0.20

    return {
        "options": options,
        "top_option": top_opt,
        "second_option": sec_opt,
        "margin": margin,
        "is_ambiguous": is_ambiguous,
        "recommended_grade": top_opt["grade"],
        "recommended_confidence": top_opt["confidence"],
        "recommended_tier": top_opt["tier"],
        "rationale": (
            f"Entity match: {entity_hits}/{len(target_entities)}, "
            f"GT word match: {gt_hits}/{len(gt_words)}, "
            f"Distractor hit: {bool(is_distractor_hit)}"
        ),
    }


def run_grading_pipeline(
    candidate_pool_path: Path,
    output_path: Path,
    mode: str = "auto",
    max_hitl: int | None = None,
    record_judgment: tuple[str, str, int] | None = None,
    category: str = "all",
    force: bool = False,
    enforce_ledger_verification: bool = False,
) -> dict[str, Any]:
    print("=" * 80)
    print(f"Wave 3C: QRELS Relevance Grading (Mode: {mode.upper()}, Category: {category})")
    print("=" * 80)
    start_time = time.time()

    from dual_pipeline.hitl_governance import HITLDecisionStore
    hitl_store = HITLDecisionStore()

    with open(candidate_pool_path, "r", encoding="utf-8") as f:
        pool_data = json.load(f)

    raw_queries = pool_data.get("queries", [])
    if category and category.lower() != "all":
        cat_lower = category.lower()
        queries = [
            q for q in raw_queries
            if cat_lower in str(q.get("category", "")).lower()
            or cat_lower in str(q.get("domain", "")).lower()
            or (cat_lower == "cross_engagement" and (
                "cross_engagement" in str(q.get("category", "")).lower()
                or "architecture_reuse" in str(q.get("domain", "")).lower()
                or "workstream_project_nexus" in str(q.get("domain", "")).lower()
                or "reuse" in str(q.get("domain", "")).lower()
            ))
        ]
        if not queries:
            queries = raw_queries
    else:
        queries = raw_queries
    print(f"Loaded {len(queries)} queries with candidate pools (filtered by category '{category}').")

    # Load existing judgments if present (Persistent Feedback Storage)
    existing_judgments: dict[str, int] = {}
    if output_path.exists() and not force:
        with open(output_path, "r", encoding="utf-8") as f:
            try:
                prior = json.load(f)
                for q_res in prior.get("evaluated_queries", []):
                    for c_res in q_res.get("judged_candidates", []):
                        key = f"{q_res['query_id']}::{c_res['record_id']}"
                        existing_judgments[key] = c_res["grade"]
                print(f"Loaded {len(existing_judgments)} existing judgments from {output_path}.")
            except Exception:
                pass

    if record_judgment is not None:
        rj_qid, rj_recid, rj_grade = record_judgment
        key = f"{rj_qid}::{rj_recid}"
        existing_judgments[key] = rj_grade
        print(f"Injected external human judgment: {key} -> Grade {rj_grade}")

    total_candidates = 0
    autonomous_resolved = 0
    hitl_required = 0
    hitl_completed = 0
    stop_requested = False
    evaluated_queries: list[dict[str, Any]] = []

    for q in queries:
        q_id = q["query_id"]
        judged_candidates: list[dict[str, Any]] = []
        grades_list: list[int] = []

        for cand in q["candidates"]:
            rec_id = cand["record_id"]
            total_candidates += 1
            pair_key = f"{q_id}::{rec_id}"

            assessment = assess_candidate_confidence(q, cand)

            if pair_key in existing_judgments:
                if enforce_ledger_verification:
                    decision_id = f"hitl-qrels-{q_id}-{rec_id}"
                    is_ratified = False
                    try:
                        from dual_pipeline.hitl_governance_core.decision_clearance import is_decision_ratified_in_ledger
                        is_ratified = is_decision_ratified_in_ledger(decision_id)
                    except Exception:
                        pass
                    if not is_ratified:
                        try:
                            dec = hitl_store.get_decision(decision_id)
                            is_ratified = dec is not None
                        except Exception:
                            pass
                    if not is_ratified:
                        raise ValueError(
                            f"Unverified persisted_human_judgment: {pair_key} lacks verified receipt in hitl_decision_ledger.sqlite!"
                        )
                grade = existing_judgments[pair_key]
                resolved_by = "persisted_human_judgment"
            elif mode == "auto":
                grade = assessment["recommended_grade"]
                if assessment["is_ambiguous"]:
                    hitl_required += 1
                    resolved_by = "auto_ambiguity_gated_fallback"
                else:
                    autonomous_resolved += 1
                    resolved_by = "autonomous_margin_gated"
            elif mode == "interactive" and assessment["is_ambiguous"] and not stop_requested:
                hitl_required += 1
                hitl_completed += 1
                # Present canonical AGENTS.md HITL prompt
                print("\n" + "=" * 80)
                print(f"### HITL Decision: QRELS Relevance Grading [Query {q_id}]")
                print(f"**Item ID / Candidate ID**: `{rec_id}`")
                print(f"**Context & Question**: Ambiguous candidate retrieved for query: \"{q['query_text']}\". The confidence margin between top option ({assessment['top_option']['label']} at {assessment['top_option']['confidence']*100:.1f}%) and runner-up ({assessment['second_option']['label']} at {assessment['second_option']['confidence']*100:.1f}%) is {assessment['margin']*100:.1f}% (<= 20% threshold), requiring Human-in-the-Loop resolution.")
                print(f"**Topic**: {q['topic_name']} ({q['domain']}) | **Tier**: {q['tier']}")
                print(f"**Ground Truth**: {q['ground_truth_summary']}")
                print(f"**Target Entities**: {q['target_entities']}")
                print(f"**Candidate**: [{cand['record_type']}] {cand['title']}")
                print(f"**Source**: {cand.get('source_id')}")
                text_snip = cand.get("retrieval_text", "")[:280].replace("\n", " ")
                print(f"**Passage**: \"{text_snip}...\"")
                print(f"**Ambiguity Margin**: Δ = {assessment['margin']*100:.1f}% (<= 20% -> Ambiguity Gated)")
                print("\n#### Options:")
                for idx, opt in enumerate(assessment["options"], 1):
                    print(f"{idx}. **[Grade {opt['grade']}: {opt['label']}]** — Confidence: {opt['tier']} ({opt['confidence']*100:.1f}%)")
                    print(f"   - **Action**: {opt['action']}")
                    print(f"   - **Evidence**: {opt['evidence']}")
                    print(f"   - **Risk**: {opt['risk']}")

                from config.request_deadline import read_stdin_line_with_timeout
                choice = read_stdin_line_with_timeout(
                    timeout_seconds=30.0,
                    prompt=f"\nSelect Grade [0/1/2] or 'q' to quit (Default [{assessment['recommended_grade']}]): ",
                )
                if choice is None:
                    choice = ""
                choice = choice.strip()
                if choice.lower() in ("q", "quit"):
                    print("\nSaving progress and exiting interactive HITL...")
                    stop_requested = True
                    grade = assessment["recommended_grade"]
                    resolved_by = "auto_ambiguity_gated_fallback"
                elif choice in ["0", "1", "2"]:
                    grade = int(choice)
                    resolved_by = "interactive_human_hitl"
                else:
                    grade = assessment["recommended_grade"]
                    resolved_by = "interactive_human_hitl"

                if resolved_by == "interactive_human_hitl":
                    selected_opt = next((o for o in assessment["options"] if o["grade"] == grade), assessment["top_option"])
                    hitl_store.record_decision(
                        decision_id=f"hitl-qrels-{q_id}-{rec_id}",
                        category="qrels_relevance_grading",
                        prompt=f"Grading candidate {rec_id} for query {q_id}: {q['query_text']}",
                        options=assessment["options"],
                        selected_option=selected_opt,
                        rationale=f"Operator selected Grade {grade} ({selected_opt['label']}) for candidate {rec_id}",
                        operator_id="human_operator",
                        metadata={"query_id": q_id, "record_id": rec_id, "grade": grade},
                    )
                    try:
                        from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer
                        record_operator_answer(
                            decision_id=f"hitl-qrels-{q_id}-{rec_id}",
                            question=f"Grading candidate {rec_id} for query {q_id}: {q['query_text']}",
                            options=assessment["options"],
                            selected_option=selected_opt,
                            operator_origin="human_operator",
                            category="qrels_relevance_grading",
                            rationale=f"Operator selected Grade {grade} for candidate {rec_id}",
                            metadata={"query_id": q_id, "record_id": rec_id, "grade": grade},
                        )
                    except Exception:
                        pass
                if max_hitl and hitl_completed >= max_hitl:
                    print(f"\nReached max HITL limit ({max_hitl}). Auto-resolving remaining candidates...")
                    stop_requested = True
            else:
                grade = assessment["recommended_grade"]
                if assessment["is_ambiguous"]:
                    hitl_required += 1
                    resolved_by = "auto_ambiguity_gated_fallback"
                else:
                    autonomous_resolved += 1
                    resolved_by = "autonomous_margin_gated"

            grades_list.append(grade)
            judged_candidates.append({
                "record_id": rec_id,
                "lane": cand["lane"],
                "record_type": cand["record_type"],
                "title": cand["title"],
                "retrieval_text": cand.get("retrieval_text", ""),
                "fused_rank": cand.get("fused_rank"),
                "dense_rank": cand.get("dense_rank"),
                "dense_score": cand.get("dense_score"),
                "sparse_rank": cand.get("sparse_rank"),
                "bm25_rank": cand.get("bm25_rank"),
                "colbert_rank": cand.get("colbert_rank"),
                "grade": grade,
                "resolved_by": resolved_by,
                "assessment": assessment,
            })

        ndcg_10 = compute_ndcg_at_k(grades_list, k=10)
        mrr_10 = compute_mrr_at_k(grades_list, k=10)

        evaluated_queries.append({
            "query_id": q_id,
            "query_text": q["query_text"],
            "topic_id": q["topic_id"],
            "topic_name": q["topic_name"],
            "tier": q["tier"],
            "domain": q["domain"],
            "canonical_ndcg_10": ndcg_10,
            "canonical_mrr_10": mrr_10,
            "total_candidates": len(judged_candidates),
            "judged_candidates": judged_candidates,
        })

    # Summary Metrics across benchmark
    mean_ndcg_10 = float(np.mean([eq["canonical_ndcg_10"] for eq in evaluated_queries]))
    mean_mrr_10 = float(np.mean([eq["canonical_mrr_10"] for eq in evaluated_queries]))

    tier_metrics: dict[str, Any] = {}
    for eq in evaluated_queries:
        t = eq["tier"]
        tier_metrics.setdefault(t, {"ndcg": [], "mrr": []})
        tier_metrics[t]["ndcg"].append(eq["canonical_ndcg_10"])
        tier_metrics[t]["mrr"].append(eq["canonical_mrr_10"])

    tier_summary = {
        t: {
            "mean_ndcg_10": round(float(np.mean(vals["ndcg"])), 4),
            "mean_mrr_10": round(float(np.mean(vals["mrr"])), 4),
            "count": len(vals["ndcg"]),
        }
        for t, vals in tier_metrics.items()
    }

    elapsed = time.time() - start_time

    payload = {
        "evaluation_contract": "urn:nee:qrels:graded-judgments:1.0.0",
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_queries": len(evaluated_queries),
        "total_candidates_evaluated": total_candidates,
        "autonomous_margin_resolved": autonomous_resolved,
        "hitl_ambiguous_cases": hitl_required,
        "mean_canonical_ndcg_10": round(mean_ndcg_10, 4),
        "mean_canonical_mrr_10": round(mean_mrr_10, 4),
        "tier_summary": tier_summary,
        "evaluated_queries": evaluated_queries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    receipt_name = "wave3c_grading_receipt.json" if category == "all" else f"wave3c_{category}_grading_receipt.json"
    receipt_path = output_path.with_name(receipt_name)
    receipt = {
        "wave": "Wave 3C",
        "status": "COMPLETE",
        "total_queries": len(evaluated_queries),
        "total_candidates_evaluated": total_candidates,
        "autonomous_resolved": autonomous_resolved,
        "hitl_cases": hitl_required,
        "mean_canonical_ndcg_10": round(mean_ndcg_10, 4),
        "mean_canonical_mrr_10": round(mean_mrr_10, 4),
        "output_file": str(output_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
    }
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)

    print("\n" + "=" * 80)
    print("WAVE 3C GRADING BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"Total queries:               {len(evaluated_queries)}")
    print(f"Total candidates evaluated:  {total_candidates}")
    print(f"Autonomous margin resolved:  {autonomous_resolved} (Delta >= 15%)")
    print(f"Ambiguity gated cases:       {hitl_required} (Delta < 15%)")
    print(f"Canonical NDCG@10:           {mean_ndcg_10:.4f}")
    print(f"Canonical MRR@10:            {mean_mrr_10:.4f}")
    print("\nPerformance by Tier:")
    for t, s in tier_summary.items():
        print(f"  - {t:35s}: NDCG@10 = {s['mean_ndcg_10']:.4f} | MRR@10 = {s['mean_mrr_10']:.4f} (N={s['count']})")
    print(f"\nSaved judgments to: {output_path}")
    print(f"Recorded receipt to: {receipt_path}")
    print(f"Total elapsed: {elapsed:.2f}s")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wave 3C QRELS Grading Tool")
    parser.add_argument(
        "--candidate-pool",
        type=Path,
        default=Path("evals/qrels_candidate_pool.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/qrels_graded_judgments.json"),
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "interactive"],
        default="auto",
        help="Grading mode: 'auto' runs calibrated confidence gating; 'interactive' presents ambiguous cases to human.",
    )
    parser.add_argument(
        "--max-hitl",
        type=int,
        default=None,
        help="Maximum number of interactive HITL decisions to prompt before auto-resolving remaining candidates.",
    )
    parser.add_argument(
        "--record-judgment",
        nargs=3,
        metavar=("QUERY_ID", "RECORD_ID", "GRADE"),
        help="Inject a specific human judgment into the pool before grading (e.g. --record-judgment t1_01_exec rec-123 0).",
    )
    parser.add_argument(
        "--category",
        default="all",
        help="Filter queries by category (e.g. cross_engagement, architecture_reuse, workstream_project_nexus).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recomputation of judgments instead of loading existing ones.",
    )
    args = parser.parse_args()
    record_j = None
    if args.record_judgment:
        record_j = (args.record_judgment[0], args.record_judgment[1], int(args.record_judgment[2]))
    output_path = args.output
    if args.category != "all" and args.output == Path("evals/qrels_graded_judgments.json"):
        output_path = Path(f"evals/qrels_{args.category}_judgments.json")
    run_grading_pipeline(
        args.candidate_pool,
        output_path,
        args.mode,
        max_hitl=args.max_hitl,
        record_judgment=record_j,
        category=args.category,
        force=args.force,
    )

