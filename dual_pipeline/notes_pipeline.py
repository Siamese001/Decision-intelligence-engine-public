"""
dual_pipeline/notes_pipeline.py

Public entrypoint and pipeline runner for processed meeting notes (Copilot, Zoom AI, speaker summaries).
Bypasses Stage 01 (Forensic Transcript) and produces Deliverables 02 (Executive Recap) and 03 (Canonical Signal Projection).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from .transcript_pipeline_core.pipeline import RawTranscriptPipeline


class NotesPipeline(RawTranscriptPipeline):
    """Pipeline specialized for pre-processed meeting notes (e.g. Copilot, Zoom summaries)."""

    def run(
        self,
        input_path: Union[str, Path],
        metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "auto",
        ingestion_context: Optional[Dict[str, Any]] = None,
        presentation_path: Optional[Union[str, Path]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_judge_retries: int = 2,
        judge_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mutate_graph: bool = True,
        directive_id: Optional[str] = None,
        reviewer: Optional[str] = None,
        rationale: Optional[str] = None,
        embedding_backend: str = "bge-m3",
        signal_envelope: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        meta = dict(metadata or {})
        meta["notes_mode"] = True
        meta["is_notes"] = True
        meta.setdefault("source_type", "copilot_notes")
        meta["skip_output_1"] = True
        meta["outputs"] = ["02", "03"]
        return super().run(
            input_path=str(input_path),
            metadata=meta,
            output_dir=output_dir,
            ingestion_context=ingestion_context,
            presentation_path=presentation_path,
            progress_callback=progress_callback,
            max_judge_retries=max_judge_retries,
            judge_model=judge_model,
            reasoning_effort=reasoning_effort,
            mutate_graph=mutate_graph,
            directive_id=directive_id,
            reviewer=reviewer,
            rationale=rationale,
            embedding_backend=embedding_backend,
            signal_envelope=signal_envelope,
            **kwargs,
        )


__all__ = ["NotesPipeline"]
