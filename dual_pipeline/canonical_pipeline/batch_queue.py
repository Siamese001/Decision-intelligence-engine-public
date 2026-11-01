"""Submission of queued canonical synthesis work."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.unified_agentic_pipeline import PipelineStage


def _handle_batch_queue(
    out_p: Path,
    meta: Dict[str, Any],
    source_text: str,
    tracker: Any,
    model_name: Optional[str],
    provider_type: str,
) -> Dict[str, Any]:
    """Serializes prompts and submits asynchronous OpenAI Batch API job."""
    from dual_pipeline.batch_core.adapter import (
        BatchSubmissionAdapter,
        create_batch_request,
        record_pending_batch,
        serialize_synthesis_prompts,
    )
    resolved_model = model_name or resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING)
    stage_roles = (
        ("output_1", "You are a forensic transcript normalizer."),
        ("output_2", "You are an executive recap writer."),
        ("output_3", "You are a canonical signal and graph extractor."),
    )
    reqs = [
        create_batch_request(
            custom_id=f"{out_p.name}__{suffix}",
            body={"model": resolved_model, "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": source_text[:30000]}]},
        )
        for suffix, sys_prompt in stage_roles
    ]
    batch_file = serialize_synthesis_prompts(out_p, reqs)
    adapter = BatchSubmissionAdapter(offline=(provider_type == "offline"))
    batch_info = adapter.submit_batch(batch_file, metadata=meta)
    record_pending_batch(out_p, batch_info)
    meta["batch_id"] = batch_info.get("batch_id")
    meta["status"] = "BATCH_QUEUED"
    (out_p / "metadata.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    tracker.complete_stage(
        PipelineStage.L2_SYNTHESIS,
        status="PASS",
        details={"batch_queued": True, "batch_id": batch_info.get("batch_id")},
    )
    return {
        "status": "BATCH_QUEUED",
        "batch_id": batch_info.get("batch_id"),
        "output_dir": str(out_p),
        "batch_file": str(batch_file),
        "simulated": batch_info.get("simulated", False),
    }

