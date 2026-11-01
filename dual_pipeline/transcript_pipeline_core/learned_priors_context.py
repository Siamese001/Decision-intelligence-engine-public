"""
dual_pipeline/transcript_pipeline_core/learned_priors_context.py

Modular helper for loading learned operator priors into pipeline execution metadata.
Keeps context_builder.py strictly under the 400-line architectural budget.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from dual_pipeline.prompts.learned_priors import compile_learned_priors_block

logger = logging.getLogger(__name__)


def append_learned_priors_context(metadata: Dict[str, Any], prior_context_blocks: List[str]) -> None:
    """Appends learned operator priors XML to prior context blocks and metadata."""
    try:
        priors_xml = compile_learned_priors_block()
        if priors_xml:
            metadata["learned_operator_priors"] = priors_xml
            prior_context_blocks.append(f"--- LEARNED OPERATOR PRIORS ---\n{priors_xml}")
            logger.info("Injected learned operator priors into prompt context.")
    except Exception as e:
        logger.debug("Learned operator priors injection skipped/failed: %s", e)
