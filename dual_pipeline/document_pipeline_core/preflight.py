"""
dual_pipeline/document_pipeline_core/preflight.py

Intake Preflight Validation for Document Ingestion.
Catches missing files, 0-byte corrupt inputs, missing format dependencies (openpyxl, pypdf),
malformed audience metadata, and extraction contract incompatibilities BEFORE any expensive
extraction-provider or LLM call.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from dual_pipeline.evidence_field_limits import envelope_field_limits
from dual_pipeline.exceptions import SourceIntegrityError

logger = logging.getLogger(__name__)


@dataclass
class PreflightReport:
    valid: bool
    source_paths: List[str]
    total_bytes: int
    detected_formats: List[str]
    dependencies_verified: List[str]
    metadata_summary: Dict[str, Any] = field(default_factory=dict)


def _check_xlsx_dependency_and_archive(path: Path) -> None:
    """Verify openpyxl is installed and that the file is a valid non-empty zip archive."""
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            f"Dependency 'openpyxl' is required to load XLSX file '{path.name}'. "
            "Install openpyxl before ingesting spreadsheet sources."
        ) from exc

    if not zipfile.is_zipfile(path):
        raise ValueError(
            f"Invalid or corrupt spreadsheet file '{path.name}'. File is not a valid zip archive."
        )
    try:
        with zipfile.ZipFile(path) as zf:
            if "xl/workbook.xml" not in zf.namelist():
                raise ValueError(
                    f"Corrupt XLSX file '{path.name}': missing 'xl/workbook.xml'."
                )
    except zipfile.BadZipFile as exc:
        raise ValueError(
            f"Corrupt XLSX file '{path.name}': BadZipFile error."
        ) from exc


def _check_pdf_dependency_and_structure(path: Path) -> None:
    """Verify pypdf is installed and that the PDF header/structure is valid."""
    try:
        import pypdf
    except ImportError as exc:
        raise RuntimeError(
            f"Dependency 'pypdf' is required to load PDF file '{path.name}'. "
            "Install pypdf before ingesting PDF sources."
        ) from exc

    raw_header = path.read_bytes()[:1024]
    if b"%PDF-" not in raw_header:
        raise ValueError(
            f"Invalid PDF file '{path.name}': missing '%PDF-' header."
        )
    try:
        reader = pypdf.PdfReader(str(path))
        if len(reader.pages) == 0:
            raise ValueError(f"PDF document '{path.name}' contains zero pages.")
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF structure for '{path.name}': {exc}") from exc


def _check_docx_structure(path: Path) -> None:
    """Verify DOCX is a valid zip archive containing word/document.xml."""
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Invalid DOCX file '{path.name}': not a valid zip archive.")
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError(
                    f"Corrupt DOCX file '{path.name}': missing 'word/document.xml'."
                )
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Corrupt DOCX file '{path.name}': BadZipFile error.") from exc


def validate_audience_serialization(metadata: Dict[str, Any]) -> None:
    """Validate audience serialization using SSOT canonicalize_audiences."""
    audience_val = metadata.get("audiences")
    if audience_val is None:
        audience_val = metadata.get("audience")

    if audience_val is None:
        return

    # If comma-separated string, convert to list before canonicalization
    if isinstance(audience_val, str) and not audience_val.strip().startswith(("[", "{")):
        audience_val = [a.strip() for a in audience_val.split(",") if a.strip()]

    try:
        from dual_pipeline.label_quality_core.audiences import (  # noqa # noqa: lazy-decouple
            canonical_audiences_json,
            canonicalize_audiences,
        )
        from dual_pipeline.label_quality_core.constants import (  # noqa # noqa: lazy-decouple
            LabelQualityError,
        )
        canonical_tuple = canonicalize_audiences(audience_val)
        metadata["audiences"] = list(canonical_tuple)
        metadata["allowed_audiences_json"] = canonical_audiences_json(canonical_tuple)
    except (LabelQualityError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Audience serialization validation failed: {exc}. "
            "Audiences must conform to alphanumeric/hyphen/period identifiers."
        ) from exc


def validate_metadata_constraints(metadata: Dict[str, Any]) -> None:
    """Validate title, meeting_name, and classification against schema limits."""
    limits = envelope_field_limits()
    title_limit = limits["title"]

    title = metadata.get("title") or metadata.get("meeting_name")
    if title and isinstance(title, str) and len(title) > title_limit:
        raise ValueError(
            f"Evidence field source.title contains {len(title)} characters; maximum is {title_limit}. "
            "Preserve the source and regenerate bounded fields; silent truncation is prohibited."
        )

    classification = metadata.get("classification")
    if classification is not None:
        if not isinstance(classification, str) or not classification.strip():
            raise ValueError("Classification must be a non-empty string identifier.")

    forged_keys = {"reviewer_authority", "elevation", "bypass_rbac", "override_role"}
    for k in forged_keys:
        if k in metadata:
            raise ValueError(
                f"Unauthorized authority field '{k}' detected in document metadata. "
                "Document body headings/signatures cannot elevate reviewer authority."
            )


def _resolve_sources(sources: Union[str, Path, Sequence[Union[str, Path]]]) -> List[Path]:
    source_list: List[Path] = []
    if isinstance(sources, (str, Path)):
        source_list.append(Path(sources).resolve())
    elif isinstance(sources, (list, tuple)):
        for s in sources:
            source_list.append(Path(s).resolve())
    else:
        raise TypeError(f"Unsupported sources argument type: {type(sources)}")

    if not source_list:
        raise ValueError("At least one source file must be specified for intake preflight.")
    return source_list


def _check_format_support(ext: str) -> None:
    from dual_pipeline.source_classification.capability_registry import (
        SupportStatus,
        get_capability_by_ext,
    )
    cap = get_capability_by_ext(ext)
    if cap and cap.status == SupportStatus.BLOCKED:
        raise SourceIntegrityError(f"Format '{ext}' is BLOCKED from ingestion: {cap.notes}")
    if cap and cap.status == SupportStatus.UNAVAILABLE:
        raise SourceIntegrityError(f"Format '{ext}' is UNAVAILABLE for ingestion: {cap.notes}")


def _check_single_source_preflight(src_path: Path) -> Tuple[int, str, Optional[str]]:
    if not src_path.is_file():
        raise FileNotFoundError(f"Input source file does not exist: {src_path}")
    f_size = src_path.stat().st_size
    if f_size == 0:
        raise SourceIntegrityError(f"Input source file is empty (0 bytes): {src_path}")
    ext = src_path.suffix.lower()
    _check_format_support(ext)
    dep = None
    if ext in (".xlsx", ".xlsm"):
        _check_xlsx_dependency_and_archive(src_path)
        dep = "openpyxl"
    elif ext == ".pdf":
        _check_pdf_dependency_and_structure(src_path)
        dep = "pypdf"
    elif ext == ".docx":
        _check_docx_structure(src_path)
        dep = "zipfile/word.xml"
    return f_size, ext.lstrip("."), dep


def validate_intake_preflight(
    sources: Union[str, Path, Sequence[Union[str, Path]]],
    metadata: Optional[Dict[str, Any]] = None,
) -> PreflightReport:
    """
    Execute deterministic intake preflight before paid extraction.
    Fails immediately on:
    - Missing source files
    - 0-byte or corrupted source files
    - Missing required parser dependencies (openpyxl, pypdf)
    - Malformed audience serialization or oversized title metadata
    """
    meta = metadata or {}
    source_list = _resolve_sources(sources)

    total_bytes = 0
    formats: List[str] = []
    deps_verified: List[str] = []

    for src_path in source_list:
        f_size, fmt, dep = _check_single_source_preflight(src_path)
        total_bytes += f_size
        formats.append(fmt)
        if dep:
            deps_verified.append(dep)

    # Validate metadata
    validate_audience_serialization(meta)
    validate_metadata_constraints(meta)

    return PreflightReport(
        valid=True,
        source_paths=[str(p) for p in source_list],
        total_bytes=total_bytes,
        detected_formats=formats,
        dependencies_verified=list(set(deps_verified)),
        metadata_summary={
            "audiences": meta.get("audiences"),
            "classification": meta.get("classification"),
            "title": meta.get("title") or meta.get("meeting_name"),
        },
    )
