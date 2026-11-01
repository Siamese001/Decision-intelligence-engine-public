"""Facade and entry points for Answer Evaluation Harness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .answer_eval_core import AnswerEvalHarness


def _load_attempt_rows(attempts_path: Path, session_id: Optional[str]) -> list[Dict[str, Any]]:
    rows = []
    for line in attempts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if not session_id or rec.get("session_id") == session_id:
                rows.append(rec)
        except Exception:
            continue
    return rows


def summarize_copilot_attempts(
    runtime_root: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluates copilot_attempts.jsonl to compute attempt-level success, abstention, and latency telemetry."""
    root = Path(runtime_root).resolve() if runtime_root else Path.cwd().resolve()
    candidates = [root / "learning" / "copilot_attempts.jsonl", root / "copilot_attempts.jsonl"]
    attempts_path = next((p for p in candidates if p.is_file()), None)
    if not attempts_path:
        return {"total_attempts": 0, "success_count": 0, "fail_count": 0, "failure_categories": {}}
    rows = _load_attempt_rows(attempts_path, session_id)
    succ = sum(1 for r in rows if r.get("terminal_outcome") == "SUCCESS")
    cats: Dict[str, int] = {}
    for r in rows:
        if r.get("terminal_outcome") != "SUCCESS":
            c = str(r.get("failure_category") or "unknown")
            cats[c] = cats.get(c, 0) + 1
    return {
        "total_attempts": len(rows),
        "success_count": succ,
        "fail_count": len(rows) - succ,
        "success_rate": round(succ / len(rows), 4) if rows else 0.0,
        "failure_categories": cats,
    }


def evaluate_ledger_answers(
    runtime_root: Optional[Path] = None,
    pipeline: str = "both",
    since: Optional[str] = None,
    session_id: Optional[str] = None,
    candidate_params: Optional[Dict[str, Any]] = None,
    llm_judge: bool = False,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Evaluates answer records in AnswerLedger and returns sealed receipt dict."""
    harness = AnswerEvalHarness(runtime_root=runtime_root)
    res = harness.evaluate(
        pipeline=pipeline,
        since=since,
        session_id=session_id,
        candidate_params=candidate_params,
        llm_judge=llm_judge,
        output_path=output_path,
    )
    res["copilot_attempt_summary"] = summarize_copilot_attempts(runtime_root=runtime_root, session_id=session_id)
    return res


def run_answer_eval(args: argparse.Namespace) -> Dict[str, Any]:
    """CLI execution handler for `nee answer-eval`."""
    runtime_root = getattr(args, "runtime_root", None)
    if runtime_root:
        runtime_root = Path(runtime_root).resolve()

    candidate_params = None
    candidate_path = getattr(args, "candidate_params", None)
    if candidate_path:
        cp = Path(candidate_path).resolve()
        if cp.is_file():
            with open(cp, "r", encoding="utf-8") as f:
                candidate_params = json.load(f)

    receipt = evaluate_ledger_answers(
        runtime_root=runtime_root,
        pipeline=getattr(args, "pipeline", "both"),
        since=getattr(args, "since", None),
        session_id=getattr(args, "session", None),
        candidate_params=candidate_params,
        llm_judge=bool(getattr(args, "llm_judge", False)),
        output_path=getattr(args, "output", None),
    )

    metrics = receipt.get("metrics", {})
    records_count = receipt.get("ledger_range", {}).get("record_count", 0)

    print("\n" + "=" * 60)
    print(f"  ANSWER EVALUATION RECEIPT: {receipt.get('eval_id')}")
    print("=" * 60)
    print(f"  Pipeline:          {receipt.get('pipeline')}")
    print(f"  Evaluated Answers: {records_count}")
    print(f"  Groundedness:      {metrics.get('groundedness', 0):.2%}")
    print(f"  Durability:        {metrics.get('citation_durability', 0):.2%}")
    print(f"  Staleness Rate:    {metrics.get('staleness_rate', 0):.2%}")
    print(f"  Conflict-Miss:     {metrics.get('conflict_miss_rate', 0):.2%}")
    print(f"  Context Recall:    {metrics.get('context_recall', 0):.2%}")
    op_use = metrics.get("operator_usefulness", {})
    print(f"  Operator Beta Mean:{op_use.get('beta_posterior_mean', 0.5):.4f} (useful: {op_use.get('useful_count', 0)}, not_useful: {op_use.get('not_useful_count', 0)}, wrong: {op_use.get('wrong_count', 0)})")
    lat = metrics.get("latency", {})
    print(f"  Latency (p50/p95): {lat.get('p50_ms', 0):.1f}ms / {lat.get('p95_ms', 0):.1f}ms")
    print(f"  Fail-Closed Rate:  {metrics.get('fail_closed_rate', 0):.2%}")

    if receipt.get("replay_comparison"):
        rep = receipt["replay_comparison"]
        print(f"  Replay Exact Match:{rep.get('reproduced_citation_exact_match_rate', 1.0):.2%}")
        print(f"  Groundedness Delta:{rep.get('groundedness_delta', 0.0):+.4f}")

    if receipt.get("llm_judge"):
        jdg = receipt["llm_judge"]
        print(f"  LLM Judge Score:   {jdg.get('average_relevance_score', 0):.2f}")

    print(f"  Receipt Digest:    {receipt.get('receipt_digest')}")
    if receipt.get("_output_file"):
        print(f"  Saved To:          {receipt.get('_output_file')}")
    print("=" * 60 + "\n")
    sys.stdout.flush()

    return receipt
