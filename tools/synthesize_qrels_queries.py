import json
from pathlib import Path
import random
from typing import Any, Dict, List, Set


def _load_judged_ids(benchmark_file: Path) -> Set[str]:
    if not benchmark_file.exists():
        return set()
    bench = json.loads(benchmark_file.read_text(encoding="utf-8"))
    judged: Set[str] = set()
    for q in bench.get("evaluated_queries", []):
        for j in q.get("judgments", []):
            judged.add(j["record_id"])
    return judged


def _synthesize_action_query(text: str) -> tuple[str, str]:
    parts = text.split("|")
    if len(parts) >= 5:
        owner = parts[4].strip()
        action = parts[3].strip()[:30]
        return f"What is {owner} accountable for regarding {action}?", "topic_" + owner.split()[0].lower() + "_action"
    return "What is the status of this action item?", "topic_general_action"


def _synthesize_keypoint_query(text: str) -> tuple[str, str]:
    if "—" in text:
        subject = text.split("—")[0].replace("- **", "").replace("**", "").strip()
        return f"What was discussed regarding {subject}?", "topic_" + subject.lower().replace(" ", "_")
    return "What was the main finding here?", "topic_general_finding"


def _synthesize_pair(cand: Dict[str, Any]) -> Dict[str, Any]:
    text = cand["text"]
    lane = cand["lane"]
    if lane == "action_item":
        query_text, topic_id = _synthesize_action_query(text)
    else:
        query_text, topic_id = _synthesize_keypoint_query(text)
    return {
        "query_text": query_text,
        "topic_id": topic_id,
        "candidate": cand,
    }


def run() -> None:
    pool_file = Path("evals/authentic_candidate_pool.json")
    benchmark_file = Path("evals/qrels_authentic_benchmark.json")
    out_file = Path("evals/qrels_synthesis_queue.json")

    if not pool_file.exists():
        print("Candidate pool not found.")
        return

    pool = json.loads(pool_file.read_text(encoding="utf-8"))
    judged = _load_judged_ids(benchmark_file)
    unjudged_pool = [p for p in pool if p["record_id"] not in judged]

    if not unjudged_pool:
        print("No remaining candidates to synthesize.")
        return

    random.seed(42)
    sample_size = min(20, len(unjudged_pool))
    sample = random.sample(unjudged_pool, sample_size)

    synthesized = [_synthesize_pair(cand) for cand in sample]
    out_file.write_text(json.dumps(synthesized, indent=2))
    print(f"Synthesized {len(synthesized)} query-candidate pairs to {out_file}")


if __name__ == "__main__":
    run()
