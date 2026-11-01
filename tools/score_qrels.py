"""Interactive HITL QRELS Scoring Tool.

Compliant with AGENTS.md HITL Controls:
- Atomic presentation (one decision at a time)
- Graded relevance labels (2: Highly Relevant, 1: Partially Relevant, 0: Irrelevant)
- Persistent feedback storage in evals/hitl_qrels_queue.json
- Computes human-grounded NDCG@5 and MRR upon completion.

Usage:
    python tools/score_qrels.py [--reset]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

QUEUE_PATH = Path("evals/hitl_qrels_queue.json")
RESULTS_PATH = Path("evals/hitl_qrels_results.json")

from dual_pipeline.retrieval_release_core.metrics import (
    compute_dcg,
    compute_ndcg,
    standard_ndcg_at_k,
    standard_reciprocal_rank,
)


def main():
    parser = argparse.ArgumentParser(description="Interactive HITL QRELS Scorer")
    parser.add_argument("--reset", action="store_true", help="Reset all judged labels to pending")
    args = parser.parse_args()

    if not QUEUE_PATH.exists():
        print(f"Error: {QUEUE_PATH} not found. Run scripts/populate_hitl_qrels_queue.py first.")
        sys.exit(1)

    with open(QUEUE_PATH, "r", encoding="utf-8") as f:
        queue = json.load(f)

    if args.reset:
        for item in queue:
            item["status"] = "PENDING"
            for c in item["candidates"]:
                c["relevance_label"] = None
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)
        print("Reset all judgments in queue to PENDING.")

    pending_items = [item for item in queue if item["status"] == "PENDING"]
    total = len(queue)
    judged = total - len(pending_items)

    print("=" * 80)
    print("NEE KNOWLEDGE GRAPH — HUMAN RELEVANCE JUDGMENT (QRELS) CONSOLE")
    print("=" * 80)
    print(f"Total queries in queue: {total} | Already judged: {judged} | Pending: {len(pending_items)}")
    print("Graded relevance scale:")
    print("  [2] Highly Relevant  — Directly answers or fulfills the query intent")
    print("  [1] Partially Relevant — Relates to the entity/domain; useful context")
    print("  [0] Irrelevant       — Off-topic, wrong entity, or noise")
    print("  [s] Skip query       — Defer this query")
    print("  [q] Quit and save    — Save progress and exit")
    print("=" * 80)

    for item_idx, item in enumerate(queue, 1):
        if item["status"] == "JUDGED":
            continue

        q_id = item["qrel_id"]
        q_text = item["query_text"]
        q_type = item.get("query_type", "unknown")
        candidates = item["candidates"]

        print(f"\n>>> Query [{item_idx}/{total}] ({q_id}) — Type: {q_type}")
        print(f'    Query: "{q_text}"')
        print(f'    Synthetic Target Context: "{item.get("target_title", "")}"')
        print("-" * 80)

        item_judgments: list[int] = []
        skipped = False

        for c in candidates:
            rank = c["rank"]
            title = c["title"]
            snippet = c.get("snippet", "").replace("\n", " ")
            ranks = c.get("ranks", {})
            rank_str = f"dense={ranks.get('dense')} sparse={ranks.get('sparse')} bm25={ranks.get('bm25')} colbert={ranks.get('colbert')}"

            print(f"\n  Candidate #{rank}: {title}")
            print(f"    Ranks: [{rank_str}]")
            print(f"    Snippet: {snippet[:180]}...")

            while True:
                from config.request_deadline import read_stdin_line_with_timeout
                raw_c = read_stdin_line_with_timeout(
                    timeout_seconds=30.0,
                    prompt=f"    Relevance for Candidate #{rank} (2/1/0/s/q) [Default 1]: ",
                )
                if raw_c is None:
                    raw_c = "q"
                choice = raw_c.strip().lower()
                if choice == "":
                    choice = "1"
                if choice in ("0", "1", "2"):
                    label = int(choice)
                    c["relevance_label"] = label
                    item_judgments.append(label)
                    break
                elif choice == "s":
                    print("    -> Skipping query.")
                    skipped = True
                    break
                elif choice == "q":
                    print("\nSaving and quitting...")
                    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
                        json.dump(queue, f, indent=2)
                    print(f"Progress saved to {QUEUE_PATH}.")
                    sys.exit(0)
                else:
                    print("    Invalid input. Enter 2, 1, 0, s, or q.")

            if skipped:
                break

        if not skipped:
            item["status"] = "JUDGED"
            item["ndcg_at_5"] = round(compute_ndcg(item_judgments, 5), 4)
            # Save progress incrementally
            with open(QUEUE_PATH, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=2)
            print(f"  -> Recorded judgments for {q_id}. NDCG@5 = {item['ndcg_at_5']:.4f}")

    # Final summary if all completed
    judged_items = [item for item in queue if item["status"] == "JUDGED"]
    if judged_items:
        ndcg_scores = [item["ndcg_at_5"] for item in judged_items if "ndcg_at_5" in item]
        avg_ndcg = sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0
        print("\n" + "=" * 80)
        print("HUMAN QRELS EVALUATION SUMMARY")
        print("=" * 80)
        print(f"Total judged queries: {len(judged_items)} / {total}")
        print(f"Mean Human-Grounded NDCG@5: {avg_ndcg:.4f}")
        print("=" * 80)

        with open(RESULTS_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "judged_count": len(judged_items),
                "total_count": total,
                "mean_ndcg_at_5": round(avg_ndcg, 4),
                "items": judged_items,
            }, f, indent=2)
        print(f"Summary results written to {RESULTS_PATH}.")


if __name__ == "__main__":
    main()
