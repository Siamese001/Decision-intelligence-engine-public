"""PA — Prompt Assembler (per-stage)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from .constants import _load_stage_prompt

logger = logging.getLogger(__name__)


class PAPacketAssembler:
    """
    Assembles minimized, source-bound context packets for each L2 stage.
    Loads the correct stage prompt template per artifact.

    Wave 5 (Decision 3): Supplementary sources passed via --supplementary-sources
    are stored in metadata["supplementary_sources"] by the CLI and loaded here as
    named references for Stage 01 speaker attribution.
    """

    def execute(self, c0_context: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Executing PA: Packet Assembly (loading stage prompt templates)")

        recap_prompt = _load_stage_prompt("recap")
        graph_prompt = _load_stage_prompt("graph")

        # ── Supplementary sources (Decision 3) ──────────────────────────────
        # Sources already loaded + validated by the CLI (transcript_cli.py).
        # Each entry: {"name": str, "path": str, "content": str}
        # PA binds them as named references so Stage 01 can sharpen attribution.
        supplementary_sources: list = metadata.get("supplementary_sources", [])
        if supplementary_sources:
            logger.info(
                "PA: %d supplementary source(s) bound: %s",
                len(supplementary_sources),
                [s.get("name") for s in supplementary_sources],
            )

        return {
            "transcript": c0_context.get("transcript", ""),
            "statements": c0_context.get("statements", []),
            "hash": c0_context.get("hash", ""),
            "metadata": metadata,
            "conformance_checked": c0_context.get("conformance_checked", False),
            "diarization_status": c0_context.get("diarization_status", "UNDIARIZED"),
            "source_type": c0_context.get("source_type", "UNDIARIZED_TEXT"),
            "meeting_type_hint": c0_context.get("meeting_type_hint"),
            # Stage prompt templates — loaded here so each executor has them
            "_stage_prompts": {
                "recap": recap_prompt,
                "graph": graph_prompt,
            },
            # Supplementary sources for Stage 01 speaker attribution (Decision 3)
            "_supplementary_sources": supplementary_sources,
        }
