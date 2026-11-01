"""
dual_pipeline/document_pipeline_core/source_manifest.py

Source Manifest and Complete Provenance Accounting for Document Ingestion.
Ensures every page, worksheet, and row range is explicitly accounted for with
inclusion/exclusion dispositions, converted digests, and summary-to-source provenance.
Strictly bars claiming full workbook ingestion from attachment counts alone.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass
class PageCoverage:
    page_num: int
    char_count: int
    disposition: str = "INCLUDED"  # INCLUDED | EXCLUDED
    exclusion_reason: Optional[str] = None
    is_raster: bool = False
    is_blank: bool = False
    visual_disposition: str = "TEXT_ONLY"  # TEXT_ONLY | ESCALATE_VISUAL | HITL_REQUIRED | EXCLUDED
    quality_score: float = 1.0
    has_images: bool = False
    image_count: int = 0
    flagged_regions: List[str] = field(default_factory=list)


@dataclass
class SheetCoverage:
    sheet_name: str
    total_rows: int
    nonempty_rows: int
    header: List[str] = field(default_factory=list)
    disposition: str = "INCLUDED"  # INCLUDED | EXCLUDED
    exclusion_reason: Optional[str] = None
    uncalculated_formulas: int = 0
    is_sparse: bool = False
    sparse_row_indices: List[int] = field(default_factory=list)


@dataclass
class SlideCoverage:
    slide_num: int
    title: str = ""
    char_count: int = 0
    shape_count: int = 0
    disposition: str = "INCLUDED"  # INCLUDED | EXCLUDED
    visual_disposition: str = "TEXT_ONLY"  # TEXT_ONLY | ESCALATE_VISUAL | HITL_REQUIRED
    exclusion_reason: Optional[str] = None
    flagged_shapes: List[str] = field(default_factory=list)


@dataclass
class UnitCoverage:
    unit_type: str  # page | sheet_cell_row | delimited_row | dom_block | rfc822_part | line_block | paragraph_table | slide
    total_units: int
    included_units: int
    excluded_units: int = 0
    disposition: str = "INCLUDED"  # INCLUDED | PARTIAL | EXCLUDED
    locator_pattern: str = ""
    visual_policy: str = "NEVER"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceDocumentEntry:
    path: str
    filename: str
    sha256: str
    bytes: int
    format: str
    is_summary: bool
    converted_text_sha256: str
    converted_text_chars: int
    pages: List[PageCoverage] = field(default_factory=list)
    sheets: List[SheetCoverage] = field(default_factory=list)
    slides: List[SlideCoverage] = field(default_factory=list)
    unit_coverage: Optional[UnitCoverage] = None
    summary_provenance: Optional[Dict[str, Any]] = None


@dataclass
class SourceManifest:
    schema_version: str = "urn:nee:dual-pipeline:source-manifest:1.0.0"
    timestamp: str = ""
    sources: List[SourceDocumentEntry] = field(default_factory=list)
    total_sources: int = 0
    total_raw_bytes: int = 0
    total_converted_chars: int = 0
    full_ingestion_claim: bool = False
    ingested_representation: str = "raw_sources"  # raw_sources | composed_summary | hybrid
    claim_rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _inspect_pdf_pages(raw_bytes: bytes) -> Tuple[List[PageCoverage], int]:
    """Inspect all pages in a PDF and build coverage accounting with quality scores."""
    from dual_pipeline.pdf_source import inspect_pdf_pages
    try:
        report = inspect_pdf_pages(raw_bytes)
        coverages: List[PageCoverage] = []
        total_chars = 0
        for p in report.pages:
            total_chars += p.char_count
            cov_disp = "EXCLUDED" if p.disposition == "EXCLUDED" else "INCLUDED"
            has_img = p.image_count > 0
            coverages.append(
                PageCoverage(
                    page_num=p.page_num,
                    char_count=p.char_count,
                    disposition=cov_disp,
                    exclusion_reason="EMPTY_PAGE" if cov_disp == "EXCLUDED" else None,
                    is_raster=has_img,
                    is_blank=bool(cov_disp == "EXCLUDED" and not has_img),
                    visual_disposition=p.disposition,
                    quality_score=p.quality_score,
                    has_images=has_img,
                    image_count=p.image_count,
                    flagged_regions=list(p.flagged_regions),
                )
            )
        return coverages, total_chars
    except Exception:
        return [], 0


def _inspect_pptx_slides(raw_bytes: bytes) -> Tuple[List[SlideCoverage], int]:
    """Inspect all slides in a PPTX and build coverage accounting with visual dispositions."""
    from dual_pipeline.pptx_source import extract_pptx_presentation
    try:
        pres = extract_pptx_presentation(raw_bytes)
        coverages: List[SlideCoverage] = []
        total_chars = 0
        for s in pres.slides:
            chars = sum(len(p) for p in s.paragraphs) + len(s.title)
            total_chars += chars
            coverages.append(
                SlideCoverage(
                    slide_num=s.slide_number,
                    title=s.title,
                    char_count=chars,
                    shape_count=len(s.shape_identifiers),
                    disposition="INCLUDED",
                    visual_disposition=s.disposition,
                    flagged_shapes=list(s.flagged_shape_locators),
                )
            )
        return coverages, total_chars
    except Exception:
        return [], 0


def _check_sparse_rows(raw_header: List[str], rows: List[Any]) -> List[int]:
    sparse_indices: List[int] = []
    if len(raw_header) < 5:
        return sparse_indices
    for r_idx, r in enumerate(rows[1:], start=2):
        populated = [c for c in r if c is not None and str(c).strip() != ""]
        if 0 < len(populated) <= 1:
            sparse_indices.append(r_idx)
    return sparse_indices


def _count_uncalculated_formulas(ws_f: Any, ws_d: Any) -> int:
    if ws_f is None or ws_d is None:
        return 0
    uncalculated = 0
    for row_f, row_d in zip(ws_f.iter_rows(), ws_d.iter_rows()):
        for cell_f, cell_d in zip(row_f, row_d):
            val_str = str(cell_f.value) if cell_f.value is not None else ""
            if (cell_f.data_type == "f" or val_str.startswith("=")) and cell_d.value is None:
                uncalculated += 1
    return uncalculated


def _inspect_xlsx_sheets(raw_bytes: bytes) -> Tuple[List[SheetCoverage], int]:
    """Inspect all sheets in an XLSX workbook and build coverage accounting."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
    try:
        wb_formulas = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)
    except Exception:
        wb_formulas = None

    coverages: List[SheetCoverage] = []
    total_nonempty = 0

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        ws_f = wb_formulas[sheet_name] if wb_formulas and sheet_name in wb_formulas.sheetnames else None
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            coverages.append(
                SheetCoverage(
                    sheet_name=sheet_name,
                    total_rows=0,
                    nonempty_rows=0,
                    disposition="EXCLUDED",
                    exclusion_reason="EMPTY_SHEET",
                )
            )
            continue

        raw_header = [str(x) if x is not None else "" for x in rows[0]]
        nonempty_rows = [r for r in rows if any(x is not None and str(x).strip() != "" for x in r)]
        nonempty_count = len(nonempty_rows)
        total_nonempty += nonempty_count

        sparse_indices = _check_sparse_rows(raw_header, rows)
        uncalc = _count_uncalculated_formulas(ws_f, ws)
        disp = "EXCLUDED" if nonempty_count == 0 else "INCLUDED"
        excl = "EMPTY_SHEET" if nonempty_count == 0 else None

        coverages.append(
            SheetCoverage(
                sheet_name=sheet_name,
                total_rows=len(rows),
                nonempty_rows=nonempty_count,
                header=raw_header,
                disposition=disp,
                exclusion_reason=excl,
                uncalculated_formulas=uncalc,
                is_sparse=len(sparse_indices) > 0,
                sparse_row_indices=sparse_indices,
            )
        )
    return coverages, total_nonempty



def _is_summary_document(path: Path, text: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Identify whether a document is a human-composed recap/email rather than raw source."""
    if metadata and (metadata.get("is_summary") or metadata.get("is_notes")):
        return True
    name = path.name.lower()
    if any(k in name for k in ("recap", "summary", "notes", "email_composed")):
        return True
    # If text is an email with attachment headers or short overview
    if "from:" in text.lower() and "subject:" in text.lower() and "attachment" in text.lower():
        return True
    return False


def _build_pdf_unit_coverage(pages: List[PageCoverage]) -> UnitCoverage:
    inc = sum(1 for p in pages if p.disposition == "INCLUDED")
    escalated = sum(1 for p in pages if p.visual_disposition in ("ESCALATE_VISUAL", "HITL_REQUIRED"))
    return UnitCoverage(
        unit_type="page",
        total_units=len(pages),
        included_units=inc,
        excluded_units=len(pages) - inc,
        disposition="INCLUDED" if inc == len(pages) else ("PARTIAL" if inc > 0 else "EXCLUDED"),
        locator_pattern="p{page_num}",
        visual_policy="ON_LOW_TEXT_OR_SCANNED",
        details={"escalated_pages": escalated},
    )


def _build_xlsx_unit_coverage(sheets: List[SheetCoverage]) -> UnitCoverage:
    tot_ne = sum(s.nonempty_rows for s in sheets)
    inc_ne = sum(s.nonempty_rows for s in sheets if s.disposition == "INCLUDED")
    return UnitCoverage(
        unit_type="sheet_cell_row",
        total_units=tot_ne,
        included_units=inc_ne,
        excluded_units=tot_ne - inc_ne,
        disposition="INCLUDED" if inc_ne == tot_ne else ("PARTIAL" if inc_ne > 0 else "EXCLUDED"),
        locator_pattern="{sheet}!R{row}C{col}",
        visual_policy="NEVER",
        details={"sheet_count": len(sheets)},
    )


def _build_pptx_unit_coverage(slides: Optional[List[SlideCoverage]]) -> UnitCoverage:
    slide_list = slides or []
    tot = len(slide_list) if slide_list else 1
    escalated = sum(1 for s in slide_list if s.visual_disposition != "TEXT_ONLY")
    return UnitCoverage(
        unit_type="slide",
        total_units=tot,
        included_units=tot,
        excluded_units=0,
        disposition="INCLUDED",
        locator_pattern="slide{num}#shape{id}",
        visual_policy="ON_NON_TEXT_SHAPES",
        details={"slide_count": tot, "escalated_slides": escalated},
    )


def _build_generic_unit_coverage(ext: str, meta: Dict[str, Any], conv_text: str) -> UnitCoverage:
    cov = meta.get("_source_coverage") or meta.get("source_coverage") or {}
    pattern_map = {
        "csv": ("delimited_row", "R{row_index}"),
        "tsv": ("delimited_row", "R{row_index}"),
        "html": ("dom_block", "block{block_index}"),
        "htm": ("dom_block", "block{block_index}"),
        "eml": ("rfc822_part", "header:{field}, body, attachment:{name}"),
        "docx": ("paragraph_table", "p{idx}, tbl{idx}!R{r}C{c}"),
    }
    unit_type, locator_pattern = pattern_map.get(ext, ("line_block", "L{line_number}"))
    tot = cov.get("total_records") or cov.get("total_units") or cov.get("total_blocks")
    if tot is None:
        tot = len([l for l in conv_text.splitlines() if l.strip()])
    return UnitCoverage(
        unit_type=unit_type,
        total_units=max(0, tot),
        included_units=max(0, tot),
        excluded_units=0,
        disposition="INCLUDED",
        locator_pattern=locator_pattern,
        visual_policy="NEVER",
        details=dict(cov),
    )


def _build_unit_coverage(
    ext: str,
    pages: List[PageCoverage],
    sheets: List[SheetCoverage],
    meta: Dict[str, Any],
    conv_text: str,
    slides: Optional[List[SlideCoverage]] = None,
) -> UnitCoverage:
    """Build per-format unit and coverage accounting."""
    if ext == "pdf":
        return _build_pdf_unit_coverage(pages)
    if ext in ("xlsx", "xlsm"):
        return _build_xlsx_unit_coverage(sheets)
    if ext == "pptx":
        return _build_pptx_unit_coverage(slides)
    return _build_generic_unit_coverage(ext, meta, conv_text)


def build_source_entry(
    file_path: Union[str, Path],
    metadata: Optional[Dict[str, Any]] = None,
    converted_text: Optional[str] = None,
) -> SourceDocumentEntry:
    """Construct a complete SourceDocumentEntry with page/sheet provenance."""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Source file does not exist: {p}")
    raw_bytes = p.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    ext = p.suffix.lower().lstrip(".")

    pages: List[PageCoverage] = []
    sheets: List[SheetCoverage] = []
    slides: List[SlideCoverage] = []
    meta = metadata or {}

    # Get converted text
    if converted_text is None:
        from dual_pipeline.document_pipeline_core.content_loader import load_document_content
        try:
            conv_text, _, _ = load_document_content(p, metadata=meta)
        except ValueError as exc:
            if "contains no extractable text" in str(exc) or "scanned or blank" in str(exc):
                conv_text = ""
            else:
                raise
    else:
        conv_text = converted_text

    conv_hash = hashlib.sha256(conv_text.encode("utf-8")).hexdigest()
    is_summary = _is_summary_document(p, conv_text, meta)

    if ext == "pdf":
        pages, _ = _inspect_pdf_pages(raw_bytes)
    elif ext in ("xlsx", "xlsm"):
        sheets, _ = _inspect_xlsx_sheets(raw_bytes)
    elif ext == "pptx":
        slides, _ = _inspect_pptx_slides(raw_bytes)

    unit_cov = _build_unit_coverage(ext, pages, sheets, meta, conv_text, slides=slides)

    return SourceDocumentEntry(
        path=str(p),
        filename=p.name,
        sha256=file_hash,
        bytes=len(raw_bytes),
        format=ext,
        is_summary=is_summary,
        converted_text_sha256=conv_hash,
        converted_text_chars=len(conv_text),
        pages=pages,
        sheets=sheets,
        slides=slides,
        unit_coverage=unit_cov,
    )


def _check_workbook_sheet_coverage(entries: List[SourceDocumentEntry]) -> Tuple[bool, bool]:
    has_workbooks = any(s.format in ("xlsx", "xlsm") for s in entries)
    all_sheets_included = True
    for s in entries:
        if s.format in ("xlsx", "xlsm"):
            if not s.sheets:
                all_sheets_included = False
            for sheet in s.sheets:
                if sheet.nonempty_rows > 0 and sheet.disposition != "INCLUDED":
                    all_sheets_included = False
    return has_workbooks, all_sheets_included


def _find_coverage_blocker(entries: List[SourceDocumentEntry]) -> Optional[str]:
    for s in entries:
        if s.format in ("ppt", "msg"):
            return f"Format '{s.format}' is unsupported; cannot claim full ingestion."
        if any(getattr(p, "is_raster", False) and p.disposition != "INCLUDED" for p in s.pages):
            return "Raster image pages present without OCR/text extraction; partial page coverage."
        if any(getattr(sheet, "uncalculated_formulas", 0) > 0 for sheet in s.sheets):
            return "Uncalculated formula cells detected in workbook; extraction is incomplete."
    return None


def _compute_ingestion_certification(
    metadata: Optional[Dict[str, Any]],
    entries: List[SourceDocumentEntry],
    summaries: List[SourceDocumentEntry],
    raw_sources: List[SourceDocumentEntry],
) -> Tuple[bool, str, str]:
    meta = metadata or {}
    status = meta.get("support_status")
    if status in ("PARTIAL", "UNAVAILABLE", "BLOCKED"):
        return False, "partial_raw", f"Unsupported/partial conversion status '{status}'; cannot claim full ingestion."

    blocker = _find_coverage_blocker(entries)
    if blocker:
        return False, "partial_raw", blocker

    has_wb, all_sheets = _check_workbook_sheet_coverage(entries)
    if bool(summaries and raw_sources) and meta.get("ingest_mode") in ("summary_only",):
        return False, "composed_summary", "Only composed summary ingested; attached raw workbooks were not mapped."
    if has_wb and not all_sheets:
        return False, "partial_raw", "Not all workbook worksheets were included in extraction; partial sheet coverage."
    if raw_sources:
        return True, "raw_sources", f"All {len(raw_sources)} raw sources and their sheets/pages fully accounted for."
    return bool(entries), "composed_summary" if summaries else "unknown", "Summary-only ingestion."


def _bind_summary_provenance(
    summaries: List[SourceDocumentEntry], raw_sources: List[SourceDocumentEntry]
) -> None:
    if summaries and raw_sources:
        raw_refs = [{"filename": r.filename, "sha256": r.sha256} for r in raw_sources]
        for s in summaries:
            s.summary_provenance = {
                "summarizes_sources": raw_refs,
                "role": "composed_synthesis_recap",
            }


def build_source_manifest(
    sources: List[Union[str, Path]],
    metadata: Optional[Dict[str, Any]] = None,
    converted_texts: Optional[Dict[str, str]] = None,
) -> SourceManifest:
    """
    Build complete source manifest across all inputs with strict full-ingestion invariants.
    Bars claiming full workbook ingestion from attachment counts alone.
    """
    entries: List[SourceDocumentEntry] = []
    total_bytes = 0
    total_chars = 0
    c_texts = converted_texts or {}

    for src in sources:
        entry = build_source_entry(src, metadata, converted_text=c_texts.get(str(src)))
        entries.append(entry)
        total_bytes += entry.bytes
        total_chars += entry.converted_text_chars

    raw_sources = [s for s in entries if not s.is_summary]
    summaries = [s for s in entries if s.is_summary]

    full_claim, rep, rationale = _compute_ingestion_certification(
        metadata, entries, summaries, raw_sources
    )
    _bind_summary_provenance(summaries, raw_sources)

    now_iso = datetime.now(timezone.utc).isoformat()
    return SourceManifest(
        schema_version="urn:nee:dual-pipeline:source-manifest:1.0.0",
        timestamp=now_iso,
        sources=entries,
        total_sources=len(entries),
        total_raw_bytes=total_bytes,
        total_converted_chars=total_chars,
        full_ingestion_claim=full_claim,
        ingested_representation=rep,
        claim_rationale=rationale,
    )


def save_source_manifest(manifest: SourceManifest, output_dir: Union[str, Path]) -> Path:
    """Persist source_manifest.json to target output directory."""
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    manifest_file = out_p / "source_manifest.json"
    manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    logger.info("Persisted source manifest to %s", manifest_file)
    return manifest_file
