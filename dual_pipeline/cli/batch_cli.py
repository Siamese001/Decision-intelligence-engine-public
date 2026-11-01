"""
dual_pipeline/cli/batch_cli.py

CLI handler for 'nee backtest-collect'.
Polls and materializes completed OpenAI Batch API outputs into canonical deliverables.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.batch_core.collector import collect_and_materialize_batch

logger = logging.getLogger(__name__)


def _backtest_collect(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute backtest-collect CLI command."""
    meeting_dir_raw = getattr(args, "meeting_dir", None)
    if not meeting_dir_raw:
        raise ValueError("--meeting-dir is required for backtest-collect")

    meeting_dir = Path(meeting_dir_raw).expanduser().resolve()
    if not meeting_dir.is_dir():
        raise FileNotFoundError(f"Meeting directory does not exist: {meeting_dir}")

    results_file_arg = getattr(args, "results_file", None)
    results_jsonl = None
    if results_file_arg:
        p = Path(results_file_arg).expanduser().resolve()
        if p.is_file():
            results_jsonl = p.read_text(encoding="utf-8")

    res = collect_and_materialize_batch(
        meeting_dir=meeting_dir,
        results_jsonl=results_jsonl,
    )

    print(json.dumps(res, indent=2))
    return res


def _batch_queue(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute batch-queue CLI command to inspect or submit offline batch requests."""
    meeting_dir_raw = getattr(args, "meeting_dir", None)
    if not meeting_dir_raw:
        raise ValueError("--meeting-dir is required for batch-queue")

    meeting_dir = Path(meeting_dir_raw).expanduser().resolve()
    if not meeting_dir.is_dir():
        raise FileNotFoundError(f"Meeting directory does not exist: {meeting_dir}")

    from dual_pipeline.batch_core.adapter import (
        BatchSubmissionAdapter,
        load_pending_batch,
        record_pending_batch,
    )

    pending = load_pending_batch(meeting_dir)
    batch_jsonl = meeting_dir / "batch_requests.jsonl"
    adapter = BatchSubmissionAdapter()

    if batch_jsonl.is_file() and not pending:
        sub_res = adapter.submit_batch(batch_jsonl, metadata={"meeting_dir": str(meeting_dir)})
        record_pending_batch(meeting_dir, sub_res)
        res = {"status": "SUBMITTED", "batch": sub_res}
    elif pending:
        batch_id = pending.get("latest_batch_id")
        status_res = adapter.retrieve_batch_status(batch_id) if batch_id else {}
        res = {"status": "PENDING", "batch": pending, "live_status": status_res}
    else:
        res = {"status": "NO_BATCH", "message": f"No batch_requests.jsonl found in {meeting_dir}"}

    print(json.dumps(res, indent=2))
    return res

