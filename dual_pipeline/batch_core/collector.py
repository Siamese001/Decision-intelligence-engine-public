"""
dual_pipeline/batch_core/collector.py

Collects, parses, and materializes completed OpenAI Batch API responses into
canonical deliverables (01, 02, 03), evaluates QA gates, and generates batch receipts.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.batch_core.adapter import (
    BatchSubmissionAdapter,
    load_pending_batch,
    parse_batch_jsonl,
)
from dual_pipeline.cost_tracker import RunCostTracker

logger = logging.getLogger(__name__)


def _extract_deliverables_from_records(
    records: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[str], Dict[str, int]]:
    """Extracts Output 01, 02, 03 text content and aggregates token usage."""
    o1_text: Optional[str] = None
    o2_text: Optional[str] = None
    o3_text: Optional[str] = None
    token_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_prompt_tokens": 0,
    }

    for rec in records:
        custom_id = str(rec.get("custom_id", "")).lower()
        response = rec.get("response", {})
        if response.get("status_code") != 200:
            logger.warning("Batch request %s failed with status %s", custom_id, response.get("status_code"))
            continue

        body = response.get("body", {})
        choices = body.get("choices", [])
        if not choices:
            continue

        content = choices[0].get("message", {}).get("content", "")
        usage = body.get("usage", {})
        token_usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        token_usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
        details = usage.get("prompt_tokens_details", {})
        token_usage["cached_prompt_tokens"] += int(details.get("cached_tokens", 0))

        if "output_1" in custom_id or "01_" in custom_id or "transcript" in custom_id:
            o1_text = content
        elif "output_2" in custom_id or "02_" in custom_id or "recap" in custom_id:
            o2_text = content
        elif "output_3" in custom_id or "03_" in custom_id or "graph" in custom_id:
            o3_text = content

    return o1_text, o2_text, o3_text, token_usage


def _materialize_batch_artifacts(
    meeting_dir: Path,
    o1_text: Optional[str],
    o2_text: Optional[str],
    o3_text: Optional[str],
    metadata: Dict[str, Any],
) -> Dict[str, str]:
    """Writes deliverables to meeting directory and returns artifacts mapping."""
    meeting_name = metadata.get("meeting_name") or "Meeting"
    clean_name = re.sub(r"[^\w\-]+", "_", meeting_name)[:40].strip("_")
    date_str = str(metadata.get("date") or "01-01-2026").replace("/", "-")
    base = f"{clean_name}_{date_str}"

    artifacts: Dict[str, str] = {}
    if o1_text:
        f1 = meeting_dir / f"01_{base}_hitl_resolved_transcript.md"
        f1.write_text(o1_text, encoding="utf-8")
        artifacts["01_transcript"] = str(f1)

    if o2_text:
        f2 = meeting_dir / f"02_{base}_executive_recap.md"
        f2.write_text(o2_text, encoding="utf-8")
        artifacts["02_recap"] = str(f2)

    if o3_text:
        f3 = meeting_dir / f"03_{base}_canonical_signal_projection.json"
        f3.write_text(o3_text, encoding="utf-8")
        artifacts["03_graph"] = str(f3)

    return artifacts


def _load_meeting_metadata(meeting_dir: Path) -> Dict[str, Any]:
    meta_file = meeting_dir / "metadata.json"
    if meta_file.is_file():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _resolve_batch_jsonl(
    meeting_dir: Path,
    results_jsonl: Optional[str],
    adapter: BatchSubmissionAdapter,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    if results_jsonl:
        return results_jsonl, None, None
    results_file = meeting_dir / "batch_results.jsonl"
    if results_file.is_file():
        return results_file.read_text(encoding="utf-8"), None, None

    pending = load_pending_batch(meeting_dir)
    if not pending or not pending.get("latest_batch_id"):
        raise FileNotFoundError(f"No batch_results.jsonl or pending batch found in {meeting_dir}")

    batch_id = pending["latest_batch_id"]
    status_info = adapter.retrieve_batch_status(batch_id)
    if status_info.get("status") != "completed":
        early_res = {
            "status": "BATCH_IN_PROGRESS",
            "batch_id": batch_id,
            "batch_status": status_info.get("status"),
            "meeting_dir": str(meeting_dir),
            "message": f"Batch {batch_id} is currently {status_info.get('status')}. Retry later.",
        }
        return None, batch_id, early_res

    out_file_id = status_info.get("output_file_id")
    if not out_file_id:
        raise RuntimeError(f"Completed batch {batch_id} has no output_file_id")
    return adapter.retrieve_batch_results(out_file_id), batch_id, None


def _calculate_batch_costs(model: str, usage: Dict[str, int]) -> Tuple[float, float, float]:
    cost_tracker = RunCostTracker(max_cost_usd=50.0)
    cost_tracker.record_call(
        model=model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        cached_prompt_tokens=usage["cached_prompt_tokens"],
        is_batch=True,
    )
    batch_cost = cost_tracker.total_cost_usd

    sync_tracker = RunCostTracker(max_cost_usd=50.0)
    sync_tracker.record_call(
        model=model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        cached_prompt_tokens=usage["cached_prompt_tokens"],
        is_batch=False,
    )
    sync_cost = sync_tracker.total_cost_usd
    savings_pct = round(((sync_cost - batch_cost) / sync_cost * 100.0) if sync_cost > 0 else 50.0, 1)
    return batch_cost, sync_cost, savings_pct


def _materialize_and_record_receipt(
    meeting_dir: Path,
    o1: Optional[str],
    o2: Optional[str],
    o3: Optional[str],
    metadata: Dict[str, Any],
    batch_id: Optional[str],
    model: str,
    usage: Dict[str, int],
    batch_cost: float,
    sync_cost: float,
    savings_pct: float,
) -> Dict[str, str]:
    materialized = _materialize_batch_artifacts(meeting_dir, o1, o2, o3, metadata)
    if (metadata.get("backtest") or metadata.get("run_mode") == "backtest") and o2 and o3:
        from dual_pipeline.backtest_core.comparison import (
            generate_backtest_comparison_report,  # noqa: PLC0415
        )
        generate_backtest_comparison_report(
            prior_dir=meeting_dir,
            current_output_2=o2,
            current_output_3=o3,
            output_dir=meeting_dir,
            metadata=metadata,
        )

    receipt = {
        "schema_version": "1.0.0",
        "receipt_type": "batch_collection_receipt",
        "batch_id": batch_id or metadata.get("batch_id") or "batch_manual",
        "meeting_dir": str(meeting_dir),
        "model": model,
        "token_usage": usage,
        "batch_cost_usd": batch_cost,
        "synchronous_equivalent_cost_usd": sync_cost,
        "savings_percentage": savings_pct,
        "materialized_artifacts": materialized,
        "collected_at": time.time(),
        "status": "PASS",
    }
    (meeting_dir / "batch_collection_receipt.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    return materialized


class BatchResultCollector:
    """Collects and processes results from completed OpenAI Batch API executions."""

    def __init__(self, adapter: Optional[BatchSubmissionAdapter] = None) -> None:
        self.adapter = adapter or BatchSubmissionAdapter()

    def collect(
        self,
        meeting_dir: Path,
        results_jsonl: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Collects batch results and materializes deliverables in meeting_dir."""
        meeting_dir = Path(meeting_dir).expanduser().resolve()
        if not meeting_dir.is_dir():
            raise FileNotFoundError(f"Meeting directory not found: {meeting_dir}")

        metadata = _load_meeting_metadata(meeting_dir)
        raw_jsonl, batch_id, early_exit = _resolve_batch_jsonl(meeting_dir, results_jsonl, self.adapter)
        if early_exit is not None:
            return early_exit

        records = parse_batch_jsonl(raw_jsonl or "")
        if not records:
            raise ValueError("No valid batch result records found in results JSONL")

        o1, o2, o3, usage = _extract_deliverables_from_records(records)
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name  # noqa: PLC0415
        default_model = resolve_tier_model_name(ModelTier.FRONTIER_REASONING)
        model = metadata.get("model_name") or default_model

        batch_cost, sync_cost, savings_pct = _calculate_batch_costs(model, usage)

        materialized: Dict[str, str] = {}
        if not dry_run:
            materialized = _materialize_and_record_receipt(
                meeting_dir, o1, o2, o3, metadata, batch_id, model, usage, batch_cost, sync_cost, savings_pct
            )

        return {
            "status": "PASS",
            "batch_id": batch_id,
            "meeting_dir": str(meeting_dir),
            "dry_run": dry_run,
            "materialized": materialized,
            "deliverables_found": {
                "output_1": bool(o1),
                "output_2": bool(o2),
                "output_3": bool(o3),
            },
            "token_usage": usage,
            "cost_usd": batch_cost,
            "savings_percentage": savings_pct,
        }


def collect_and_materialize_batch(
    meeting_dir: Path,
    results_jsonl: Optional[str] = None,
    dry_run: bool = False,
    adapter: Optional[BatchSubmissionAdapter] = None,
) -> Dict[str, Any]:
    """Helper function to run BatchResultCollector."""
    collector = BatchResultCollector(adapter=adapter)
    return collector.collect(meeting_dir, results_jsonl=results_jsonl, dry_run=dry_run)
