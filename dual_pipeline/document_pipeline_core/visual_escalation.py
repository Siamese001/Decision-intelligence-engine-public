"""
dual_pipeline/document_pipeline_core/visual_escalation.py

Deterministic visual escalation and OCR routing invariants for PDF and PowerPoint documents.
Enforces selective visual analysis: avoids raster/vision for text-native pages/slides,
routes visual work only where evidence cannot be text-extracted, and flags inaccessible
charts/diagrams as HITL_REQUIRED with exact source locators (p{n}, p{n}#img{id}, slide{n}#shape{id}).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
from xml.etree import ElementTree as ET


class VisualDisposition(str, Enum):
    """Governed visual routing disposition."""
    TEXT_ONLY = "TEXT_ONLY"
    HYBRID_VISUAL_REQUIRED = "HYBRID_VISUAL_REQUIRED"
    ESCALATE_VISUAL = "ESCALATE_VISUAL"
    HITL_REQUIRED = "HITL_REQUIRED"
    QUARANTINE = "QUARANTINE"
    EXCLUDED = "EXCLUDED"


@dataclass(frozen=True)
class TextQualityScore:
    """Evaluated text density and quality metrics for a page or shape."""
    char_count: int
    word_count: int
    alphanumeric_ratio: float
    printable_ratio: float
    has_images: bool
    image_count: int
    is_scanned: bool
    is_low_text: bool
    quality_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PageVisualAssessment:
    """Visual evaluation and disposition for a single PDF page."""
    page_num: int
    locator: str
    char_count: int
    disposition: VisualDisposition
    quality: TextQualityScore
    reason: str
    flagged_locators: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_num": self.page_num,
            "locator": self.locator,
            "char_count": self.char_count,
            "disposition": self.disposition.value,
            "quality": self.quality.to_dict(),
            "reason": self.reason,
            "flagged_locators": list(self.flagged_locators),
        }


@dataclass(frozen=True)
class SlideVisualAssessment:
    """Visual evaluation and disposition for a single PowerPoint slide."""
    slide_num: int
    locator: str
    disposition: VisualDisposition
    text_char_count: int
    shape_count: int
    text_shape_count: int
    visual_shape_count: int
    has_inaccessible_chart: bool
    has_diagram: bool
    reason: str
    flagged_shape_locators: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slide_num": self.slide_num,
            "locator": self.locator,
            "disposition": self.disposition.value,
            "text_char_count": self.text_char_count,
            "shape_count": self.shape_count,
            "text_shape_count": self.text_shape_count,
            "visual_shape_count": self.visual_shape_count,
            "has_inaccessible_chart": self.has_inaccessible_chart,
            "has_diagram": self.has_diagram,
            "reason": self.reason,
            "flagged_shape_locators": list(self.flagged_shape_locators),
        }


def compute_text_quality(text: str, image_count: int = 0) -> TextQualityScore:
    """Compute lexical density, character variety, and image-to-text ratio."""
    clean = (text or "").strip()
    char_len = len(clean)
    if char_len == 0:
        return TextQualityScore(
            char_count=0,
            word_count=0,
            alphanumeric_ratio=0.0,
            printable_ratio=0.0,
            has_images=image_count > 0,
            image_count=image_count,
            is_scanned=image_count > 0,
            is_low_text=True,
            quality_score=0.0,
        )

    words = [w for w in re.split(r"\s+", clean) if w]
    alnum = sum(1 for c in clean if c.isalnum())
    printable = sum(1 for c in clean if c.isprintable())
    alnum_ratio = round(alnum / char_len, 4) if char_len else 0.0
    print_ratio = round(printable / char_len, 4) if char_len else 0.0

    is_scanned = (char_len < 30 and image_count > 0)
    is_low_text = (char_len < 60)

    base = min(1.0, char_len / 250.0) * 0.4 + alnum_ratio * 0.3 + print_ratio * 0.3
    if is_scanned:
        base = min(base, 0.15)

    return TextQualityScore(
        char_count=char_len,
        word_count=len(words),
        alphanumeric_ratio=alnum_ratio,
        printable_ratio=print_ratio,
        has_images=image_count > 0,
        image_count=image_count,
        is_scanned=is_scanned,
        is_low_text=is_low_text,
        quality_score=round(base, 4),
    )


def assess_pdf_page(
    page_num: int,
    text: str,
    image_count: int = 0,
    *,
    is_encrypted: bool = False,
    ocr_failed: bool = False,
    escalate_hybrid: bool = False,
) -> PageVisualAssessment:
    """Determine visual routing disposition for a PDF page."""
    loc = f"p{page_num}"
    if is_encrypted:
        return PageVisualAssessment(
            page_num=page_num,
            locator=loc,
            char_count=0,
            disposition=VisualDisposition.HITL_REQUIRED,
            quality=compute_text_quality("", 0),
            reason="ENCRYPTED_PDF_CREDENTIALS_REQUIRED",
            flagged_locators=[loc],
        )

    if ocr_failed:
        return PageVisualAssessment(
            page_num=page_num,
            locator=loc,
            char_count=len(text),
            disposition=VisualDisposition.HITL_REQUIRED,
            quality=compute_text_quality(text, image_count),
            reason="OCR_PROCESSING_FAILED",
            flagged_locators=[loc],
        )

    q = compute_text_quality(text, image_count)
    if q.char_count == 0 and q.image_count == 0:
        return PageVisualAssessment(
            page_num=page_num,
            locator=loc,
            char_count=0,
            disposition=VisualDisposition.EXCLUDED,
            quality=q,
            reason="EMPTY_BLANK_PAGE",
            flagged_locators=[],
        )

    if q.is_scanned or (q.is_low_text and q.has_images):
        flags = [f"{loc}#img{i}" for i in range(1, image_count + 1)] if image_count else [loc]
        return PageVisualAssessment(
            page_num=page_num,
            locator=loc,
            char_count=q.char_count,
            disposition=VisualDisposition.ESCALATE_VISUAL,
            quality=q,
            reason="SCANNED_OR_LOW_TEXT_PAGE",
            flagged_locators=flags,
        )

    if q.has_images:
        flags = [f"{loc}#img{i}" for i in range(1, image_count + 1)]
        disp = (
            VisualDisposition.HYBRID_VISUAL_REQUIRED
            if escalate_hybrid
            else VisualDisposition.TEXT_ONLY
        )
        return PageVisualAssessment(
            page_num=page_num,
            locator=loc,
            char_count=q.char_count,
            disposition=disp,
            quality=q,
            reason="HYBRID_TEXT_WITH_EMBEDDED_IMAGES",
            flagged_locators=flags,
        )

    return PageVisualAssessment(
        page_num=page_num,
        locator=loc,
        char_count=q.char_count,
        disposition=VisualDisposition.TEXT_ONLY,
        quality=q,
        reason="TEXT_NATIVE_CONFIRMED",
        flagged_locators=[],
    )


def escalate_pdf_pages(
    page_tuples: Sequence[tuple[int, str, int]],
    *,
    escalate_hybrid: bool = True,
) -> List[PageVisualAssessment]:
    """Assess a sequence of (page_num, text, image_count) tuples with hybrid visual region detection."""
    return [
        assess_pdf_page(page_num, text, img_count, escalate_hybrid=escalate_hybrid)
        for page_num, text, img_count in page_tuples
    ]


def assess_pptx_slide(
    slide_num: int,
    text_char_count: int,
    total_shapes: int,
    text_shapes_count: int,
    visual_shapes: List[Dict[str, Any]],
    *,
    has_tables: bool = False,
    has_notes: bool = False,
) -> SlideVisualAssessment:
    """Determine visual routing disposition for a PowerPoint slide."""
    loc = f"slide{slide_num}"
    inaccessible = [s for s in visual_shapes if s.get("inaccessible")]
    if inaccessible:
        flags = [f"{loc}#shape{s['id']}" for s in inaccessible]
        return SlideVisualAssessment(
            slide_num=slide_num,
            locator=loc,
            disposition=VisualDisposition.HITL_REQUIRED,
            text_char_count=text_char_count,
            shape_count=total_shapes,
            text_shape_count=text_shapes_count,
            visual_shape_count=len(visual_shapes),
            has_inaccessible_chart=any(s.get("kind") == "chart" for s in inaccessible),
            has_diagram=any(s.get("kind") == "diagram" for s in inaccessible),
            reason="INACCESSIBLE_CHART_OR_UNSUPPORTED_DATA",
            flagged_shape_locators=flags,
        )

    diagrams = [s for s in visual_shapes if s.get("kind") == "diagram"]
    if diagrams:
        flags = [f"{loc}#shape{s['id']}" for s in diagrams]
        if text_char_count == 0 and not has_tables:
            return SlideVisualAssessment(
                slide_num=slide_num,
                locator=loc,
                disposition=VisualDisposition.HITL_REQUIRED,
                text_char_count=0,
                shape_count=total_shapes,
                text_shape_count=0,
                visual_shape_count=len(visual_shapes),
                has_inaccessible_chart=False,
                has_diagram=True,
                reason="DIAGRAM_ONLY_SLIDE_NO_EXTRACTABLE_TEXT",
                flagged_shape_locators=flags,
            )
        return SlideVisualAssessment(
            slide_num=slide_num,
            locator=loc,
            disposition=VisualDisposition.ESCALATE_VISUAL,
            text_char_count=text_char_count,
            shape_count=total_shapes,
            text_shape_count=text_shapes_count,
            visual_shape_count=len(visual_shapes),
            has_inaccessible_chart=False,
            has_diagram=True,
            reason="SLIDE_CONTAINS_DIAGRAM_AND_TEXT",
            flagged_shape_locators=flags,
        )

    pics = [s for s in visual_shapes if s.get("kind") == "picture"]
    if pics and text_char_count < 30 and not has_tables and not has_notes:
        flags = [f"{loc}#shape{s['id']}" for s in pics]
        return SlideVisualAssessment(
            slide_num=slide_num,
            locator=loc,
            disposition=VisualDisposition.ESCALATE_VISUAL,
            text_char_count=text_char_count,
            shape_count=total_shapes,
            text_shape_count=text_shapes_count,
            visual_shape_count=len(visual_shapes),
            has_inaccessible_chart=False,
            has_diagram=False,
            reason="PICTURE_HEAVY_LOW_TEXT_SLIDE",
            flagged_shape_locators=flags,
        )

    return SlideVisualAssessment(
        slide_num=slide_num,
        locator=loc,
        disposition=VisualDisposition.TEXT_ONLY,
        text_char_count=text_char_count,
        shape_count=total_shapes,
        text_shape_count=text_shapes_count,
        visual_shape_count=len(visual_shapes),
        has_inaccessible_chart=False,
        has_diagram=False,
        reason="TEXT_NATIVE_CONFIRMED",
        flagged_shape_locators=[],
    )


def _check_target_conflict(assessments: Sequence[Any], targets: set[str], kind: str) -> None:
    for a in assessments:
        disp = a.disposition.value if hasattr(a.disposition, "value") else str(a.disposition)
        if disp == "TEXT_ONLY":
            flags = getattr(a, "flagged_locators", getattr(a, "flagged_shape_locators", getattr(a, "flagged_regions", [])))
            if a.locator in targets or (flags and any(f in targets for f in flags)):
                raise ValueError(
                    f"Invariant violation: pure-text {kind} '{a.locator}' was routed to visual/raster processing."
                )


def _check_all_rendered(assessments: Sequence[Any], targets: set[str], kind: str) -> None:
    if len(assessments) > 1 and len(targets) == len(assessments):
        for a in assessments:
            disp = a.disposition.value if hasattr(a.disposition, "value") else str(a.disposition)
            if disp == "TEXT_ONLY":
                raise ValueError(f"Invariant violation: all {kind}s were routed to visual/raster by default.")


def _check_missing_locators(assessments: Sequence[Any], kind: str) -> None:
    for a in assessments:
        disp = a.disposition.value if hasattr(a.disposition, "value") else str(a.disposition)
        if disp in ("ESCALATE_VISUAL", "HITL_REQUIRED"):
            flags = getattr(a, "flagged_locators", getattr(a, "flagged_shape_locators", getattr(a, "flagged_regions", [])))
            if not a.locator and not flags:
                raise ValueError(f"Invariant violation: visual claim for {kind} lacks a source locator.")


def validate_visual_routing_invariants(
    *,
    pages: Optional[Sequence[Any]] = None,
    slides: Optional[Sequence[Any]] = None,
    attempted_raster_targets: Optional[List[str]] = None,
) -> None:
    """Enforce non-negotiable visual routing boundaries."""
    targets = set(attempted_raster_targets or [])
    if pages:
        _check_all_rendered(pages, targets, "PDF page")
        _check_target_conflict(pages, targets, "PDF page")
        _check_missing_locators(pages, "PDF page")
    if slides:
        _check_all_rendered(slides, targets, "PPTX slide")
        _check_target_conflict(slides, targets, "PPTX slide")
        _check_missing_locators(slides, "PPTX slide")


def _extract_chart_title(tree: ET.Element, ns: str) -> str:
    title_elem = tree.find(f".//{{{ns}}}title")
    if title_elem is None:
        return ""
    texts = [n.text for n in title_elem.iter() if n.text]
    return " ".join(t.strip() for t in texts if t.strip())


def _extract_series_pts(ser: ET.Element, ns: str) -> List[str]:
    pts = []
    for pt in ser.iter(f"{{{ns}}}pt"):
        v = pt.find(f"{{{ns}}}v")
        if v is not None and v.text:
            pts.append(v.text.strip())
    return pts


def _extract_chart_series(tree: ET.Element, ns: str) -> List[Dict[str, Any]]:
    series_data = []
    for ser in tree.iter(f"{{{ns}}}ser"):
        tx_elem = ser.find(f"{{{ns}}}tx")
        ser_name = ""
        if tx_elem is not None:
            names = [n.text for n in tx_elem.iter() if n.text]
            ser_name = " ".join(n.strip() for n in names if n.strip())
        pts = _extract_series_pts(ser, ns)
        if pts:
            series_data.append({"name": ser_name or f"Series {len(series_data)+1}", "values": pts})
    return series_data


def parse_chart_xml(chart_xml_bytes: bytes) -> Dict[str, Any]:
    """Extract chart title, categories, and numeric series from DrawingML chart XML."""
    try:
        tree = ET.fromstring(chart_xml_bytes)
    except Exception:
        return {"has_data": False, "title": "", "series": [], "inaccessible": True, "is_accessible": False}

    ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    title = _extract_chart_title(tree, ns)
    series = _extract_chart_series(tree, ns)
    has_data = len(series) > 0
    return {
        "has_data": has_data,
        "is_accessible": has_data,
        "title": title or "Chart",
        "series": series,
        "inaccessible": not has_data,
    }
