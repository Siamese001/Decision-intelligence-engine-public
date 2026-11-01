"""
dual_pipeline/transcript_stages_core/l2_executors.py

L2 Stage Executors Facade & Legacy Monolithic Synthesis Coordinator.
Re-exports modularized Stage 01, 02, and 03 synthesis agents:
- L2ForensicNormalizerExecutor: Stage 01 Forensic Normalizer.
- L2ExecutiveRecapExecutor: Stage 02 Executive Recap Agent.
- L2GraphExtractorExecutor: Stage 03 Canonical Graph Extractor Agent.
- L2SynthesisExecutor: Legacy monolithic coordinator.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .forensic_normalizer import ForensicNormalizer
from .graph_executor import (
    L2GraphExtractorExecutor,
)
from .graph_executor import (
    build_stage03_user_content as _build_stage03_user_content,
)
from .graph_executor import (
    post_process_stage03_projection as _post_process_stage03_projection,
)
from .graph_executor import (
    resolve_stage03_args as _resolve_stage03_args,
)
from .models import L2SynthesisProvider
from .recap_executor import L2ExecutiveRecapExecutor

logger = logging.getLogger(__name__)


class L2ForensicNormalizerExecutor:
    """
    L2 Stage 01: Forensic Normalizer Agent.
    Produces Artifact 01 (hitl_resolved_transcript.md).
    Delegates to decoupled ForensicNormalizer agent.
    """

    def __init__(self, provider: L2SynthesisProvider):
        self.provider = provider
        self._normalizer = ForensicNormalizer()

    def execute(
        self,
        u0_context: Dict[str, Any],
        plan: Any,
        remediation_directive: Optional[str] = None,
    ) -> str:
        """Run Stage 01 and return the forensic transcript string."""
        logger.info("L2-Stage01: Forensic Normalizer Agent")
        return self._normalizer.execute(
            u0_context=u0_context,
            plan=plan,
            provider=self.provider,
            remediation_directive=remediation_directive,
        )


class L2SynthesisExecutor:
    """
    Legacy monolithic executor — retained for backward compatibility.
    The new pipeline uses the three split executors above.
    synthesize_recap/synthesize_graph are called with the legacy 2-arg signature.
    """

    def __init__(self, provider: L2SynthesisProvider):
        self.provider = provider

    def execute(self, pa_context: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, str]:
        logger.info("Executing L2: Advisory Synthesis (legacy monolithic path)")
        if isinstance(pa_context, dict):
            prompt = pa_context.get("transcript", str(pa_context))
        else:
            prompt = str(pa_context)

        # Call with legacy 2-arg signature — OfflineTranscriptSynthesizer accepts both
        recap = self.provider.synthesize_recap(prompt, metadata)
        graph = self.provider.synthesize_graph(prompt, metadata)
        return {"recap": recap, "graph": graph}


__all__ = [
    "L2ForensicNormalizerExecutor",
    "L2ExecutiveRecapExecutor",
    "L2GraphExtractorExecutor",
    "L2SynthesisExecutor",
    "_resolve_stage03_args",
    "_build_stage03_user_content",
    "_post_process_stage03_projection",
]
