"""
dual_pipeline/document_pipeline_core/metadata_helper.py

Deterministic metadata scaffolding for written documents, emails, memos, and charters.
Extracts header metadata (sender/author, subject/title, date, recipients) and resolves project directory.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from dual_pipeline.document_pipeline_core.content_loader import extract_document_text_snippet
from dual_pipeline.meeting_paths import (
    normalize_meeting_date,
    standardize_meeting_slug,
)
from dual_pipeline.query_router_core.project_router import determine_project_directory


def _clean_header_value(val: str) -> str:
    """Strips markdown mailto and angle brackets from header strings."""
    c = re.sub(r"\[([^\]]+)\]\(mailto:[^\)]+\)", r"\1", val)
    c = re.sub(r"\[[^\]]*\]\([^\)]*\)", "", c)
    c = re.sub(r"<[^>]*>", "", c)
    c = re.sub(r"[<>\(\)]+", "", c)
    return c.strip()


def _extract_email_headers(text: str) -> Dict[str, Any]:
    """Extract standard email header fields if present in top 4000 chars."""
    headers: Dict[str, Any] = {}
    top_text = text[:4000]

    from_match = re.search(r"^From:\s*([^\n<]+)(?:<([^>]+)>)?", top_text, re.MULTILINE | re.IGNORECASE)
    if from_match:
        name = _clean_header_value(from_match.group(1))
        raw_email = from_match.group(2).strip() if from_match.group(2) else ""
        clean_email = re.sub(r"^\[|\]|\(mailto:[^\)]+\)", "", raw_email).strip()
        headers["author"] = name or "Author"
        if clean_email:
            headers["author_email"] = clean_email

    to_match = re.search(r"^To:\s*([^\n<]+)(?:<([^>]+)>)?", top_text, re.MULTILINE | re.IGNORECASE)
    if to_match:
        headers["recipient"] = _clean_header_value(to_match.group(1))
        if to_match.group(2):
            headers["recipient_email"] = re.sub(r"^\[|\]|\(mailto:[^\)]+\)", "", to_match.group(2)).strip()

    subject_match = re.search(r"^Subject:\s*([^\n]+)", top_text, re.MULTILINE | re.IGNORECASE)
    if subject_match:
        headers["title"] = subject_match.group(1).strip()

    date_match = re.search(
        r"^(?:Date|Sent):\s*(?:[A-Za-z]+,\s*)?([A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        top_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if date_match:
        raw_dt = date_match.group(1).strip()
        try:
            parsed = datetime.strptime(raw_dt, "%B %d, %Y")
            headers["date"] = parsed.strftime("%m/%d/%Y")
        except ValueError:
            try:
                parsed = datetime.strptime(raw_dt, "%b %d, %Y")
                headers["date"] = parsed.strftime("%m/%d/%Y")
            except ValueError:
                headers["date"] = raw_dt

    time_match = re.search(
        r"^(?:Date|Sent):[^\n]*?\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)(?:[^\S\r\n]+[A-Za-z]{2,4})?)",
        top_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if time_match:
        headers["time"] = time_match.group(1).strip()

    # Extract all attendees from From, To, and Cc header blocks
    attendees: list[str] = []
    if headers.get("author"):
        attendees.append(headers["author"])

    for field in ("To", "Cc"):
        block_m = re.search(
            rf"^{field}:\s*(.*?)(?=^(?:From|To|Cc|Subject|Sent|Date):|\Z)",
            top_text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if block_m:
            raw_block = block_m.group(1)
            entries = re.split(r"[;\n]+", raw_block)
            for e in entries:
                c = _clean_header_value(e)
                if c and not c.startswith(("To:", "Cc:", "From:", "Subject:")):
                    if c not in attendees:
                        attendees.append(c)

    if attendees:
        headers["attendees"] = attendees

    return headers


def _resolve_document_project(
    project: Optional[str],
    meta: Dict[str, Any],
    headers: Dict[str, Any],
    eff_title: str,
    eff_author: str,
    raw_text: str,
) -> str:
    """Resolve project directory for document metadata scaffolding."""
    eff_project = project or meta.get("project")
    if eff_project and eff_project.lower() != "auto":
        from dual_pipeline.parameters import resolve_project_name
        return resolve_project_name(eff_project)

    router_meta = {
        "meeting_name": eff_title,
        "title": eff_title,
        "author": eff_author,
        "attendees": meta.get("attendees") or headers.get("attendees"),
        "dry_run": meta.get("dry_run", False),
    }
    is_dry = bool(meta.get("dry_run") or os.environ.get("NEE_DRY_RUN") in ("1", "true", "True"))
    try:
        eff_project = determine_project_directory(
            router_meta, raw_text[:2000], allow_llm=not is_dry, dry_run=is_dry
        )
    except Exception:
        eff_project = None

    if not eff_project or eff_project.lower() == "auto":
        from dual_pipeline.query_router_core.project_router import infer_project_from_keyword_signals
        eff_project = infer_project_from_keyword_signals(
            f"{raw_text} {eff_title} {eff_author}",
            check_partner_alliances=False,
        )

    if not eff_project or eff_project.lower() == "auto":
        from dual_pipeline.exceptions import ProjectResolutionRequiredError
        raise ProjectResolutionRequiredError(
            f"Target project could not be resolved for document '{eff_title}'. "
            "Specify --project explicitly (e.g. --project project_nexus, --project project_lens)."
        )
    return eff_project


def _detect_document_classification(raw_text: str, eff_title: str) -> str:
    """Detects if document content contains confidential markings or commercial terms."""
    combined = f"{eff_title}\n{raw_text[:15000]}"

    # Check for letter-spaced or regular markings:
    # "INTERNAL USE ONLY", "PROPRIETARY AND CONFIDENTIAL", "STRICTLY CONFIDENTIAL"
    explicit_patterns = (
        r"\bI\s*N\s*T\s*E\s*R\s*N\s*A\s*L\s+U\s*S\s*E\s+O\s*N\s*L\s*Y\b",
        r"\bP\s*R\s*O\s*P\s*R\s*I\s*E\s*T\s*A\s*R\s*Y\s+A\s*N\s*D\s+C\s*O\s*N\s*F\s*I\s*D\s*E\s*N\s*T\s*I\s*A\s*L\b",
        r"\bS\s*T\s*R\s*I\s*C\s*T\s*L\s*Y\s+C\s*O\s*N\s*F\s*I\s*D\s*E\s*N\s*T\s*I\s*A\s*L\b",
        r"\b(?:partner_advisory confidential|confidential - internal|confidential client|customer confidential)\b",
    )
    for pat in explicit_patterns:
        if re.search(pat, combined, flags=re.IGNORECASE):
            return "confidential"

    # Commercial / Contractual / Pricing terms from pre-intake policy
    commercial_patterns = (
        r"\b(?:pricing structure|pricing model|how they price|rate card|statement of work|\bSOW\b)\b",
        r"\b(?:procurement terms?|commercial terms?|contract terms?)\b",
    )
    for pat in commercial_patterns:
        if re.search(pat, combined, flags=re.IGNORECASE):
            return "confidential"

    return "internal"


def _resolve_document_dates(
    date_str: Optional[str],
    meta: Dict[str, Any],
    headers: Dict[str, Any],
) -> tuple[str, str, str, Optional[str], str]:
    """Disambiguate file upload/snapshot date from actual content/authoring date."""
    explicit_actual = meta.get("actual_date") or meta.get("content_date") or headers.get("date")
    raw_date = explicit_actual or date_str or meta.get("date") or meta.get("upload_date")
    date_dir, date_disp = normalize_meeting_date(raw_date)
    upload_raw = meta.get("upload_date") or date_str or meta.get("date")
    _, upload_disp = normalize_meeting_date(upload_raw) if upload_raw else (date_dir, date_disp)
    if explicit_actual:
        _, actual_disp = normalize_meeting_date(explicit_actual)
        status = str(meta.get("date_status") or "exact")
        return date_dir, date_disp, upload_disp, actual_disp, status
    status = str(meta.get("date_status") or "estimated_from_source_snapshot")
    return date_dir, date_disp, upload_disp, None, status


def _resolve_document_audiences(
    audiences: Optional[Union[str, list[str]]],
    meta: Dict[str, Any],
    eff_project: str,
    eff_class: str,
) -> list[str]:
    """Resolve and firewall audiences for document metadata scaffolding."""
    if audiences is not None:
        if isinstance(audiences, str):
            return [a.strip() for a in audiences.split(",") if a.strip()]
        return list(audiences)

    if meta.get("audiences"):
        raw = list(meta["audiences"])
        if eff_project == "internal_consulting" or eff_class in ("confidential", "restricted"):
            raw = [a for a in raw if a not in ("nee-engineering", "client_approved_external")]
        return raw or ["internal_consulting"]

    if eff_project == "internal_consulting" or eff_class in ("confidential", "restricted"):
        return ["internal_consulting"]

    return ["internal_consulting", "nee-engineering"]


def scaffold_document_metadata(
    input_path: Union[str, Path],
    metadata_path: Optional[Union[str, Path]] = None,
    project: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
    date_str: Optional[str] = None,
    classification: Optional[str] = None,
    audiences: Optional[Union[str, list[str]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Build unified metadata for document/email ingestion."""
    meta: Dict[str, Any] = {}
    if metadata:
        meta.update(metadata)

    if metadata_path and os.path.exists(str(metadata_path)):
        with open(str(metadata_path), "r", encoding="utf-8") as f:
            loaded_meta = json.load(f)
        if meta.get("force_rerun"):
            for stale_k in (
                "remediation_directive",
                "remediation_directive_graph",
                "remediation_directive_recap",
                "remediation_directive_forensic",
                "document_patch_request",
                "prior_graph_candidate",
                "prior_recap_candidate",
                "status",
                "partial_deliverables_error",
                "partial_deliverables_saved",
                "attempt",
                "retry_count",
            ):
                loaded_meta.pop(stale_k, None)
        for k, v in loaded_meta.items():
            if k not in meta or meta[k] is None:
                meta[k] = v

    input_p: Optional[Path] = None
    if isinstance(input_path, Path) or (
        isinstance(input_path, str) and os.path.exists(str(input_path)) and os.path.isfile(str(input_path))
    ):
        input_p = Path(input_path).resolve()

    snippet_meta = dict(meta)
    if input_p and input_p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}:
        snippet_meta["allow_visual_image"] = True
        snippet_meta["enable_visual_ocr"] = True
        snippet_meta["is_visual_document"] = True
        snippet_meta["is_document"] = True

    src = source_text or (meta.get("source_text") if isinstance(meta.get("source_text"), str) else None)
    raw_text = src[:15000] if src else extract_document_text_snippet(input_path, max_chars=15000, metadata=snippet_meta)
    headers = _extract_email_headers(raw_text) if raw_text else {}

    # Check for prominent markdown heading (# Title)
    markdown_title: Optional[str] = None
    if raw_text:
        for line in raw_text.splitlines():
            clean_line = line.strip()
            if clean_line.startswith("# "):
                markdown_title = clean_line[2:].strip()
                break

    # Title resolution
    eff_title = (
        title
        or meta.get("title")
        or meta.get("document_title")
        or meta.get("meeting_name")
        or headers.get("title")
        or markdown_title
    )
    if not eff_title and input_p:
        eff_title = input_p.stem.replace("_", " ").replace("-", " ").title()
    eff_title = eff_title or "Document Ingestion"

    # Author resolution (unknown authors remain unknown)
    eff_author = author or meta.get("author") or meta.get("organizer") or headers.get("author") or None

    # Date resolution (disambiguates file upload date vs actual content date)
    date_dir, date_disp, upload_disp, actual_disp, date_status = _resolve_document_dates(date_str, meta, headers)

    eff_project = _resolve_document_project(project, meta, headers, eff_title, eff_author, raw_text)

    # Classification resolution
    detected_class = _detect_document_classification(raw_text, eff_title)
    if classification:
        eff_class = classification
    elif meta.get("classification"):
        eff_class = meta["classification"]
    else:
        eff_class = detected_class

    # Auto-escalate if explicit confidential markings were detected
    if eff_class == "internal" and detected_class == "confidential":
        eff_class = "confidential"

    # Audiences resolution
    eff_audiences = _resolve_document_audiences(audiences, meta, eff_project, eff_class)

    raw_attendees = meta.get("attendees") or headers.get("attendees")
    if raw_attendees:
        eff_attendees = list(raw_attendees)
        if eff_author and eff_author not in eff_attendees:
            eff_attendees.insert(0, eff_author)
    else:
        eff_attendees = ([eff_author] if eff_author else []) + ([headers["recipient"]] if "recipient" in headers else [])

    slug = standardize_meeting_slug(eff_title, attendees=eff_attendees, max_len=40)
    doc_time = headers.get("time") or datetime.now().strftime("%I%M%p_ET")
    time_clean = re.sub(r"[^\w]+", "", doc_time)

    meta.update({
        "title": eff_title, "meeting_name": eff_title, "author": eff_author, "organizer": eff_author,
        "attendees": eff_attendees, "date": date_disp, "upload_date": upload_disp, "actual_date": actual_disp,
        "date_status": date_status, "date_filename": date_dir, "time": doc_time, "time_filename": time_clean,
        "project": eff_project, "classification": eff_class, "audiences": eff_audiences,
        "source_type": "document", "is_document": True, "output3_only": True, "filename_slug": slug,
    })

    for hk in ("author_email", "recipient", "recipient_email"):
        if hk in headers:
            meta[hk] = headers[hk]

    return meta
