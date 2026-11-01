"""
dual_pipeline/document_pipeline.py

Public entrypoint and facade for first-class document and email ingestion.
"""
from .document_pipeline_core import (
    DocumentGraphExtractorExecutor,
    DocumentPipeline,
    ParallelDocumentIngestor,
    evaluate_document_judge_and_release,
    evaluate_document_qa_gates,
    extract_document_text_snippet,
    ingest_documents_parallel,
    load_document_content,
    publish_document_to_graph,
    scaffold_document_metadata,
)

__all__ = [
    "DocumentPipeline",
    "ParallelDocumentIngestor",
    "ingest_documents_parallel",
    "DocumentGraphExtractorExecutor",
    "publish_document_to_graph",
    "scaffold_document_metadata",
    "evaluate_document_qa_gates",
    "evaluate_document_judge_and_release",
    "load_document_content",
    "extract_document_text_snippet",
]
