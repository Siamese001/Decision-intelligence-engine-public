"""CLI subpackage for dual_pipeline, exposing command parsing, execution, and handler dispatch."""
from __future__ import annotations

from typing import Any

from dual_pipeline.cli.engine import execute, main
from dual_pipeline.cli.parser import build_parser

_ENGINE_EXPORTS = {
    "_assert_backend_matches_pair",
    "_audiences",
    "_audit_run",
    "_authorization",
    "_backend",
    "_bind_model_authorization",
    "_bind_receipt_model_authorization",
    "_completion_guard",
    "_default_publish_directive_id",
    "_graph_for_intake",
    "_ingestion_reasoning_service",
    "_intake_engine",
}

_HANDLERS_EXPORTS = {
    "_candidate_backlog_cleanup",
    "_combined_remediation",
    "_combined_remediation_finalize",
    "_ingest_meeting",
    "_intake_publish",
    "_release_batch",
    "_source_retirement",
    "_validation_closeout",
}

_PARSER_EXPORTS = {
    "_BGE_M3_BACKENDS",
    "_add_authorization",
    "_add_backend",
    "_add_ingestion_reasoning_controls",
    "_add_l1_controller_controls",
    "_add_l2_controls",
    "_add_query_routing_controls",
    "_add_runtime",
    "_add_semantic_cache_controls",
    "_l1_control_policy_from_args",
}

__all__ = sorted(["main", "execute", "build_parser"] + list(_ENGINE_EXPORTS | _HANDLERS_EXPORTS | _PARSER_EXPORTS))


def __getattr__(name: str) -> Any:
    if name in _PARSER_EXPORTS:
        from dual_pipeline.cli import parser

        return getattr(parser, name)
    if name in _HANDLERS_EXPORTS:
        from dual_pipeline.cli import handlers

        return getattr(handlers, name)
    if name in _ENGINE_EXPORTS:
        from dual_pipeline.cli import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())
