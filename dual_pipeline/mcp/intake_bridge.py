"""Governed document intake bridge for Model Context Protocol and CLI callers.

Delegates file loading to core unified_pipeline loaders, enforces pre-egress
sanitization, and routes document sources to Output 03 signal projections.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Union

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer

logger = logging.getLogger(__name__)

SAFE_PROJECT_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class EgressSanitizationQuarantineError(Exception):
    """Raised when egress content contains forbidden patterns and quarantine is enforced."""


@dataclass
class GovernedDocumentIntakeResult:
    success: bool
    filename: str
    file_path: str
    format: str
    sha256: str
    project: str
    row_count: int
    col_count: int
    preview_markdown: str
    sanitized: bool
    quarantined: bool
    quarantine_reason: Optional[str] = None
    pipeline_dispatched: bool = False
    output_03_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitize_preview_text(text: str) -> tuple[str, bool, Optional[str]]:
    """Sanitize text before egress, quarantining or redacting detected secrets."""
    if not text:
        return "", False, None
    try:
        clean = LocalPreEgressSanitizer.sanitize_text(text)
        return clean, False, None
    except EgressSanitizationError as exc:
        logger.warning("Audit Event: Pre-egress sanitization quarantine triggered on text preview")
        # Redact sensitive lines for preview while flagging quarantine
        redacted = "[QUARANTINED: Content contains sensitive patterns matching egress firewall rules]"
        return redacted, True, str(exc)


def _compute_metrics(text: str, fmt: str) -> tuple[int, int]:
    """Compute approximate row and column counts based on format."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return 0, 0
    if fmt in {"xlsx", "csv"}:
        table_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
        if table_lines:
            header_cells = [c.strip() for c in table_lines[0].split("|")[1:-1]]
            return max(0, len(table_lines) - 2), len(header_cells)
    return len(lines), 1


def ingest_document_safely(
    input_source: Union[str, Path],
    *,
    project: str = "project_nexus",
    max_preview_chars: int = 2000,
    dispatch_pipeline: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> GovernedDocumentIntakeResult:
    """Ingest a document source through governed content loading and pre-egress sanitization."""
    file_p = Path(input_source).resolve()

    if not file_p.exists():
        return GovernedDocumentIntakeResult(
            success=False,
            filename=file_p.name,
            file_path=str(file_p),
            format="",
            sha256="",
            project=project,
            row_count=0,
            col_count=0,
            preview_markdown="",
            sanitized=False,
            quarantined=False,
            error=f"File not found: {file_p}",
        )

    if file_p.is_dir():
        return GovernedDocumentIntakeResult(
            success=False,
            filename=file_p.name,
            file_path=str(file_p),
            format="directory",
            sha256="",
            project=project,
            row_count=0,
            col_count=0,
            preview_markdown="",
            sanitized=False,
            quarantined=False,
            error=f"Source path is a directory, not a document file: {file_p}",
        )

    if not SAFE_PROJECT_RE.match(project):
        return GovernedDocumentIntakeResult(
            success=False,
            filename=file_p.name,
            file_path=str(file_p),
            format=file_p.suffix.lstrip("."),
            sha256="",
            project=project,
            row_count=0,
            col_count=0,
            preview_markdown="",
            sanitized=False,
            quarantined=False,
            error=f"Invalid project identifier: '{project}'. Must match [a-zA-Z0-9_-]{{1,64}}.",
        )

    try:
        text, sha256_hash, fmt = load_document_content(file_p, metadata=metadata)
        row_count, col_count = _compute_metrics(text, fmt)

        preview_raw = text[:max_preview_chars]
        preview_clean, quarantined, q_reason = sanitize_preview_text(preview_raw)

        pipeline_dispatched = False
        out3_path = None

        if dispatch_pipeline and not quarantined:
            from dual_pipeline.cli.handlers import _ingest_document
            ns = argparse.Namespace(
                command="ingest-document",
                input=str(file_p),
                project=project,
                auto_reroute=True,
                document=True,
            )
            dispatch_res = _ingest_document(ns)
            pipeline_dispatched = True
            if isinstance(dispatch_res, dict):
                out3_path = dispatch_res.get("output_03_path")

        return GovernedDocumentIntakeResult(
            success=True,
            filename=file_p.name,
            file_path=str(file_p),
            format=fmt,
            sha256=sha256_hash,
            project=project,
            row_count=row_count,
            col_count=col_count,
            preview_markdown=preview_clean,
            sanitized=True,
            quarantined=quarantined,
            quarantine_reason=q_reason,
            pipeline_dispatched=pipeline_dispatched,
            output_03_path=out3_path,
        )
    except Exception as exc:
        logger.exception("Governed document intake failed on %s: %s", file_p, exc)
        return GovernedDocumentIntakeResult(
            success=False,
            filename=file_p.name,
            file_path=str(file_p),
            format=file_p.suffix.lstrip("."),
            sha256="",
            project=project,
            row_count=0,
            col_count=0,
            preview_markdown="",
            sanitized=False,
            quarantined=False,
            error=str(exc),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Governed MCP Document Intake Bridge")
    parser.add_argument("file_path", help="Path to document to ingest")
    parser.add_argument("--project", default="project_nexus", help="Project identifier")
    parser.add_argument("--preview-only", action="store_true", help="Extract preview without dispatching pipeline")
    parser.add_argument("--dispatch", action="store_true", help="Dispatch to NEE Output 3 pipeline")
    args = parser.parse_args()

    result = ingest_document_safely(
        args.file_path,
        project=args.project,
        dispatch_pipeline=args.dispatch and not args.preview_only,
    )
    print(json.dumps(result.to_dict()))
    if not result.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
