"""
dual_pipeline/document_pipeline_core
Core modules for first-class document and email ingestion.
"""
from .content_loader import extract_document_text_snippet, load_document_content
from .evaluator import evaluate_document_judge_and_release, evaluate_document_qa_gates
from .executor import DocumentGraphExtractorExecutor
from .metadata_helper import scaffold_document_metadata
from .parallel_ingestor import ParallelDocumentIngestor, ingest_documents_parallel
from .pipeline import DocumentPipeline
from .publisher import publish_document_batch_to_graph, publish_document_to_graph

__all__ = [
    "scaffold_document_metadata",
    "evaluate_document_qa_gates",
    "evaluate_document_judge_and_release",
    "DocumentGraphExtractorExecutor",
    "DocumentPipeline",
    "ParallelDocumentIngestor",
    "ingest_documents_parallel",
    "publish_document_to_graph",
    "publish_document_batch_to_graph",
    "load_document_content",
    "extract_document_text_snippet",
]
