"""
dual_pipeline/batch_core

Asynchronous OpenAI Batch API integration for cost-optimized backtesting.
Provides 50% discount on LLM tokens with 24-hour non-interactive SLA.
"""
from __future__ import annotations

from .adapter import (
    BatchSubmissionAdapter,
    create_batch_request,
    format_batch_jsonl,
    load_pending_batch,
    parse_batch_jsonl,
    record_pending_batch,
    serialize_synthesis_prompts,
)
from .collector import (
    BatchResultCollector,
    collect_and_materialize_batch,
)

__all__ = [
    "BatchSubmissionAdapter",
    "create_batch_request",
    "format_batch_jsonl",
    "parse_batch_jsonl",
    "record_pending_batch",
    "load_pending_batch",
    "serialize_synthesis_prompts",
    "BatchResultCollector",
    "collect_and_materialize_batch",
]
