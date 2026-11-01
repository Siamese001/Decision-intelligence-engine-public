"""
dual_pipeline/document_pipeline_core/pipeline.py

First-class Document & Email Ingestion Pipeline.
Extracts only Deliverable 03 (Canonical Signal Projection) directly from written text,
bypassing spoken transcript normalizers (01) and executive meeting recaps (02).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.transcript_stages_core.synthesizers import (
    LLMTranscriptSynthesizer,
    OfflineTranscriptSynthesizer,
)

logger = logging.getLogger(__name__)


def _run_5_agent_prepass(input_path: Union[str, Path, None], meta: Dict[str, Any]) -> None:
    if "5_agent_intelligence_report" in meta:
        return
    is_role_library_doc = "agile delivery roles" in str(meta.get("title") or "").lower()
    should_run_5_agent = meta.get("enable_5_agent_intelligence") is True or (
        is_role_library_doc and meta.get("enable_5_agent_intelligence") is not False
    )
    if not (should_run_5_agent and input_path and Path(input_path).exists()):
        return
    try:
        from dual_pipeline.document_pipeline_core.content_loader import load_document_content
        from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
            _is_meeting_summary_doc,
            build_intelligence_prior_context,
            run_5_agent_document_intelligence,
        )
        doc_text, _, _ = load_document_content(Path(input_path), metadata=meta)
        if len(doc_text.strip()) > 200 and not _is_meeting_summary_doc(doc_text, meta):
            prov_type = meta.get("provider_type") or (meta.get("_extraction_execution") or {}).get("provider_type")
            report = run_5_agent_document_intelligence(
                document_text=doc_text,
                document_title=meta.get("title") or Path(input_path).stem,
                provider_type=prov_type,
            )
            meta["5_agent_intelligence_report"] = {
                "coverage_score": report.coverage_score, "primacy_bias_detected": report.primacy_bias_detected,
                "total_entities": report.total_entities_detected, "entities": report.entities,
                "relationships": report.relationships, "arbiter_verdict": report.arbiter_verdict,
            }
            if report.arbiter_verdict == "PASS":
                intel_context = build_intelligence_prior_context(report)
                meta["prior_context"] = f"{meta.get('prior_context', '')}\n\n{intel_context}".strip()
            else:
                meta["enable_5_agent_intelligence"] = False
    except Exception as e:
        meta["enable_5_agent_intelligence"] = False
        logger.warning("5-Agent Document Intelligence pre-pass skipped or failed: %s", e)


class DocumentPipeline(CanonicalAgenticPipeline):
    """End-to-end pipeline for ingesting documents, emails, and written memos into GraphDB."""

    @property
    def pipeline_name(self) -> str:
        return "DocumentPipeline"

    def __init__(
        self,
        provider_type: str = "llm",
        model_name: Optional[str] = None,
        reasoning_effort: Optional[str] = "high",
    ):
        super().__init__(
            provider_type=provider_type,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            modality=CanonicalModality.DOCUMENT,
        )
        self.provider_type = provider_type
        def_model, def_effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.model_name = model_name or def_model
        eff = reasoning_effort or def_effort or "high"
        self.reasoning_effort = eff

        if provider_type == "offline":
            self.provider = OfflineTranscriptSynthesizer()
        else:
            self.provider = LLMTranscriptSynthesizer(
                model=self.model_name,
                reasoning_effort=self.reasoning_effort,
            )

        self.document_extractor = DocumentGraphExtractorExecutor(self.provider)
        self.l2_graph = self.document_extractor  # backward compatibility shim

    def run(
        self,
        input_path: Union[str, Path, None],
        metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "auto",
        mutate_graph: bool = True,
        directive_id: Optional[str] = None,
        reviewer: Optional[str] = None,
        rationale: Optional[str] = None,
        embedding_backend: str = "bge-m3",
        judge_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_judge_retries: int = 2,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        fast_judge: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute document ingestion emitting Output 03, governance receipts, and graph mutations."""
        logger.info("Starting Document Ingestion Pipeline for: %s", input_path)
        meta = dict(metadata or {})
        meta["is_document"] = True
        meta["source_type"] = "document"
        meta["output3_only"] = True
        meta["fast_judge"] = fast_judge
        meta.setdefault("provider_type", self.provider_type)

        from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
        if input_path and (not meta.get("project") or meta.get("project") == "auto"):
            scaffolded = scaffold_document_metadata(
                input_path=input_path,
                title=meta.get("title") or meta.get("document_title"),
                project=meta.get("project"),
                classification=meta.get("classification"),
                audiences=meta.get("audiences"),
                author=meta.get("author") or meta.get("organizer"),
                date_str=meta.get("date"),
                metadata=meta,
            )
            for k, v in scaffolded.items():
                if k not in meta or not meta[k]:
                    meta[k] = v

        _run_5_agent_prepass(input_path, meta)
        return super().run(
            input_path=input_path,
            metadata=meta,
            output_dir=output_dir,
            mutate_graph=mutate_graph,
            directive_id=directive_id,
            reviewer=reviewer,
            rationale=rationale,
            embedding_backend=embedding_backend,
            judge_model=judge_model,
            reasoning_effort=reasoning_effort,
            max_judge_retries=max_judge_retries,
            progress_callback=progress_callback,
            **kwargs,
        )
