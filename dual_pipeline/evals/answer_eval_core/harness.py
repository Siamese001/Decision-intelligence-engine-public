"""Execution harness for offline sealed Answer Evaluation."""
from __future__ import annotations

import datetime
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import create_sqlite_conn, sqlite3
from dual_pipeline.answer_ledger import AnswerLedger

from .judge import evaluate_with_llm_judge, evaluate_with_semantic_judge
from .metrics import compute_aggregate_metrics
from .models import AnswerEvalReceipt, compute_eval_receipt_digest, validate_eval_receipt
from .replay import replay_candidate_parameters

logger = logging.getLogger("nee.answer_eval.harness")


class AnswerEvalHarness:
    """Offline sealed evaluation harness for AnswerLedger answers across both pipelines."""

    def __init__(self, runtime_root: Optional[Path] = None) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent.parent
        self.runtime_root = Path(runtime_root).resolve() if runtime_root else self.repo_root
        self.ledger_path = self.runtime_root / "learning" / "answer_ledger.sqlite"
        self.reports_dir = self.runtime_root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def evaluate(
        self,
        pipeline: str = "both",
        since: Optional[str] = None,
        session_id: Optional[str] = None,
        candidate_params: Optional[Dict[str, Any]] = None,
        llm_judge: bool = False,
        use_semantic_judge: bool = False,
        output_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Runs evaluation over ledger answers producing a sealed AnswerEvalReceipt."""
        eval_id = f"eval-ans-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if not self.ledger_path.is_file():
            receipt = self._build_empty_receipt(eval_id, now_iso, pipeline)
            return self._finalize_receipt(receipt, output_path)

        ledger = AnswerLedger(self.ledger_path)
        is_chain_valid, tamper_msg = ledger.verify_chain()

        records = self._load_filtered_answers(ledger, pipeline, since, session_id)
        feedback_list = self._load_filtered_feedback(ledger, records)

        graph_con, graph_sha, gen_id = self._open_graph_read_only()

        try:
            metrics = compute_aggregate_metrics(records, feedback_list, graph_con)

            replay_result = None
            if candidate_params is not None:
                replay_comp = replay_candidate_parameters(
                    records,
                    candidate_params,
                    runtime_root=self.runtime_root,
                    con=graph_con,
                )
                replay_result = replay_comp.to_dict()

            judge_result = None
            if use_semantic_judge:
                judge_score = evaluate_with_semantic_judge(records)
                judge_result = judge_score.to_dict()
            elif llm_judge:
                judge_score = evaluate_with_llm_judge(records)
                judge_result = judge_score.to_dict()

            start_id = records[0]["answer_id"] if records else None
            end_id = records[-1]["answer_id"] if records else None

            receipt_obj = AnswerEvalReceipt(
                eval_id=eval_id,
                timestamp=now_iso,
                pipeline=pipeline,
                ledger_range={
                    "start_answer_id": start_id,
                    "end_answer_id": end_id,
                    "record_count": len(records),
                    "chain_valid": is_chain_valid,
                },
                graph_sha256=graph_sha,
                generation=gen_id,
                parameter_snapshot_digest=None,
                metrics=metrics,
                replay_comparison=replay_result,
                llm_judge=judge_result,
            )

            receipt_dict = receipt_obj.to_dict()
            receipt_dict["receipt_digest"] = compute_eval_receipt_digest(receipt_dict)

            validate_eval_receipt(receipt_dict)
            return self._finalize_receipt(receipt_dict, output_path)
        finally:
            if graph_con is not None:
                graph_con.close()

    def _load_filtered_answers(
        self,
        ledger: AnswerLedger,
        pipeline: str,
        since: Optional[str],
        session_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        answers = [r.to_dict() for r in ledger.list_answers(limit=5000)]
        filtered: List[Dict[str, Any]] = []
        for a in answers:
            if pipeline != "both" and a.get("pipeline") != pipeline:
                continue
            if session_id and a.get("session_id") != session_id:
                continue
            if since:
                ts = a.get("timestamp", "")
                if ts and not self._is_on_or_after(ts, since):
                    continue
            filtered.append(a)
        return filtered

    def _load_filtered_feedback(
        self,
        ledger: AnswerLedger,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        all_feedback = [fb.to_dict() for fb in ledger.list_feedback()]
        ans_ids = {r["answer_id"] for r in records}
        return [fb for fb in all_feedback if fb.get("answer_id") in ans_ids]

    def _is_on_or_after(self, timestamp_str: str, since_str: str) -> bool:
        norm_since = since_str.replace("/", "-")
        norm_ts = timestamp_str[:10].replace("/", "-")
        return norm_ts >= norm_since

    def _open_graph_read_only(self) -> tuple[Optional[sqlite3.Connection], Optional[str], Optional[Any]]:
        active_pair_path = self.runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            return None, None, None
        try:
            with open(active_pair_path, "r", encoding="utf-8") as f:
                pair = json.load(f)
            rel_graph = pair.get("graph_path")
            graph_sha = pair.get("graph_sha256")
            gen_id = pair.get("generation_id") or pair.get("generation")
            if not rel_graph:
                return None, graph_sha, gen_id
            graph_path = (self.runtime_root / rel_graph).resolve()
            if not graph_path.is_file():
                return None, graph_sha, gen_id

            con = create_sqlite_conn(graph_path, readonly=True)
            return con, graph_sha, gen_id
        except Exception as exc:
            logger.warning("Could not open read-only graph: %s", exc)
            return None, None, None

    def _build_empty_receipt(self, eval_id: str, now_iso: str, pipeline: str) -> Dict[str, Any]:
        metrics = compute_aggregate_metrics([], [], None)
        receipt_obj = AnswerEvalReceipt(
            eval_id=eval_id,
            timestamp=now_iso,
            pipeline=pipeline,
            ledger_range={
                "start_answer_id": None,
                "end_answer_id": None,
                "record_count": 0,
                "chain_valid": True,
            },
            graph_sha256=None,
            generation=None,
            metrics=metrics,
        )
        receipt_dict = receipt_obj.to_dict()
        receipt_dict["receipt_digest"] = compute_eval_receipt_digest(receipt_dict)
        validate_eval_receipt(receipt_dict)
        return receipt_dict

    def _finalize_receipt(self, receipt: Dict[str, Any], output_path: Optional[Path]) -> Dict[str, Any]:
        dest = output_path or (self.reports_dir / f"answer_eval_{receipt['eval_id']}_receipt.json")
        dest = Path(dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        receipt["_output_file"] = str(dest)
        return receipt
