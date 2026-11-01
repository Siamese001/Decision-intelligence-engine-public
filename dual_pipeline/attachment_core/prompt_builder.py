"""
L2 synthesis prompt builder injecting uploaded meeting collateral and visual evidence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .types import (
    AttachmentRecord,
    CollateralAnnotation,
    CrossModalAttachmentAlignment,
    ExtractedAttachmentContent,
)


def _format_components_and_connections(content: ExtractedAttachmentContent) -> List[str]:
    lines = []
    if content.components:
        comp_strs = [f"`{c.get('component_ref', c.get('label'))}` ({c.get('label')})" for c in content.components]
        lines.append(f"- **Identified Architecture Components**: {', '.join(comp_strs)}")
    if content.connections:
        conn_strs = [
            f"`{conn.get('from_ref')}` --[{conn.get('predicate', conn.get('label', 'CONNECTS_TO'))}]--> `{conn.get('to_ref')}`"
            for conn in content.connections
        ]
        lines.append(f"- **Visual Data Flows / Edges**: {'; '.join(conn_strs)}")
    return lines


_DECISIVE_KEYWORDS = {
    "critical", "failed", "blocked", "high", "risk", "error", "offline",
    "delay", "p0", "alert", "remediation", "unresolved", "variance", "decision", "action",
}


def _score_table_row(row: Any) -> float:
    text = " ".join(str(c).lower() for c in row if c is not None)
    score = sum(1.0 for kw in _DECISIVE_KEYWORDS if kw in text)
    if any(char.isdigit() for char in text):
        score += 0.5
    return score


def _select_decisive_table_rows(rows: List[Any], limit: int = 10) -> Tuple[List[Any], int]:
    if len(rows) <= limit:
        return rows, 0
    indexed_scores = [(idx, _score_table_row(r), r) for idx, r in enumerate(rows)]
    sorted_by_score = sorted(indexed_scores, key=lambda x: x[1], reverse=True)
    selected_tuples = sorted(sorted_by_score[:limit], key=lambda x: x[0])
    selected_rows = [t[2] for t in selected_tuples]
    return selected_rows, len(rows) - len(selected_rows)


def _select_decisive_claims(claims: List[Any], limit: int = 10) -> Tuple[List[str], int]:
    formatted = [c if isinstance(c, str) else c.get("claim", str(c)) for c in claims]
    if len(formatted) <= limit:
        return formatted, 0
    indexed_scores = [
        (idx, sum(1.0 for kw in _DECISIVE_KEYWORDS if kw in text.lower()), text)
        for idx, text in enumerate(formatted)
    ]
    sorted_by_score = sorted(indexed_scores, key=lambda x: x[1], reverse=True)
    selected_tuples = sorted(sorted_by_score[:limit], key=lambda x: x[0])
    selected_texts = [t[2] for t in selected_tuples]
    return selected_texts, len(formatted) - len(selected_texts)


def _format_milestones(milestones: Optional[List[Any]]) -> Optional[str]:
    if not milestones:
        return None
    m_lines = [
        f"  * **{m.get('name', 'Milestone')}**: Date: `{m.get('date', 'TBD')}`, Status: `{m.get('status', 'PENDING')}`"
        for m in milestones
    ]
    return "- **Extracted Milestones & Schedule**:\n" + "\n".join(m_lines)


def _format_single_table(table: Dict[str, Any]) -> List[str]:
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers:
        return []
    lines = [
        "  | " + " | ".join(str(h) for h in headers) + " |",
        "  | " + " | ".join("---" for _ in headers) + " |",
    ]
    selected_rows, omitted = _select_decisive_table_rows(rows, limit=10)
    for r in selected_rows:
        lines.append("  | " + " | ".join(str(c) for c in r) + " |")
    if omitted > 0:
        lines.append(
            f"  > *[Omission accounting: {omitted} of {len(rows)} total rows omitted "
            f"from prompt context (selected {len(selected_rows)} decisive rows)]*"
        )
    return lines


def _format_milestones_and_tables(content: ExtractedAttachmentContent) -> List[str]:
    lines = []
    m_block = _format_milestones(getattr(content, "milestones", None))
    if m_block:
        lines.append(m_block)
    tables = getattr(content, "tables", None) or []
    t_lines = []
    for t in tables:
        t_lines.extend(_format_single_table(t))
    if t_lines:
        lines.append("- **Extracted Tables & Statuses**:\n" + "\n".join(t_lines))
    return lines


def _format_claims_and_text(content: ExtractedAttachmentContent) -> List[str]:
    lines = []
    if getattr(content, "claims", None):
        selected_claims, omitted_claims = _select_decisive_claims(content.claims, limit=10)
        c_lines = [f"  * {c}" for c in selected_claims]
        if omitted_claims > 0:
            c_lines.append(
                f"  > *[Omission accounting: {omitted_claims} of {len(content.claims)} total claims omitted from prompt context]*"
            )
        lines.append("- **Visual Architecture Claims**:\n" + "\n".join(c_lines))
    if content.extracted_text:
        snippet = content.extracted_text.strip()
        max_chars = 60000
        if len(snippet) > max_chars:
            snippet = snippet[:max_chars] + "... [truncated]"
        lines.append(f"- **Extracted Content / OCR Text**:\n```text\n{snippet}\n```")
    return lines


def _format_content_details(content: ExtractedAttachmentContent) -> List[str]:
    lines = []
    if getattr(content, "is_demo_or_sample", False):
        lines.append(
            "> **[DEMO / SAMPLE EXHIBIT]**: This collateral depicts sample, demo, or illustrative mockup figures. "
            "Do NOT record these numbers as authentic project budgets or commitments in Output 03."
        )
    if content.summary:
        lines.append(f"- **Summary**: {content.summary}")
    lines.extend(_format_components_and_connections(content))
    lines.extend(_format_milestones_and_tables(content))
    lines.extend(_format_claims_and_text(content))
    return lines


def _format_spoken_mentions(att_id: str, alignment: Optional[CrossModalAttachmentAlignment]) -> List[str]:
    if not alignment or not alignment.mentions:
        return []
    att_mentions = [m for m in alignment.mentions if m.attachment_id == att_id]
    if not att_mentions:
        return []
    m_snippets = []
    for m in att_mentions[:4]:
        weak_tag = " (Weak Cue - Unauthenticated Attribution)" if getattr(m, "is_weak_alignment", False) else ""
        m_snippets.append(
            f"Turn {m.turn_index} [{m.timestamp}] {m.speaker}: \"{m.turn_text[:100]}...\" (keyword: '{m.matched_keyword}'){weak_tag}"
        )
    return ["- **Correlated Spoken Dialogue Turns**:\n  * " + "\n  * ".join(m_snippets)]


def _format_collateral_reviewed_block(
    att: AttachmentRecord,
    annotation: Optional[CollateralAnnotation],
    thumbnail_path: Optional[str],
) -> List[str]:
    """
    Emits a COLLATERAL_REVIEWED exhibit block for Output 1/2 prompt injection.
    Includes drop timestamp, aligned turn range, and PNG thumbnail embed if available.
    """
    lines = [
        "**[COLLATERAL_REVIEWED]** This item was actively reviewed during the meeting.",
    ]
    if att.dropped_at_elapsed and att.dropped_at_elapsed != "00:00:00":
        lines.append(f"- **Dropped at elapsed**: `{att.dropped_at_elapsed}` (session clock when file appeared)")
    if annotation:
        ts_start, ts_end = annotation.aligned_turn_range
        lines.append(f"- **Active discussion window**: `{ts_start}` → `{ts_end}` (±2 min temporal window)")
        if annotation.key_topics:
            lines.append(f"- **Key topics discussed**: {', '.join(annotation.key_topics[:6])}")
    if thumbnail_path:
        lines.append(f"\n![{att.filename}]({thumbnail_path})\n")
        lines.append(
            "> *Embed the above exhibit in Output 01 at the turn nearest to the drop timestamp "
            "and in Output 02 under the Technical Landscape exhibit section.*"
        )
    else:
        lines.append(
            "> *Reference this collateral by filename in Output 01 turn annotation and "
            "Output 02 Technical Landscape section with grounded content citations.*"
        )
    return lines


def _format_value_tier_guidance(
    filename: str,
    val_assessment: Any,
    content: Optional[ExtractedAttachmentContent],
) -> str:
    from .value_classifier import CollateralValueTier
    if val_assessment.tier == CollateralValueTier.FAILED_EXTRACTION or (content and getattr(content, "is_failed", False)):
        err_msg = val_assessment.error_reason or (content.error_reason if content else "unknown extraction error")
        return (
            f"> **[FAILED EXTRACTION / QUARANTINED]**: Visual content extraction failed for `{filename}`: {err_msg}. "
            "Do NOT hallucinate, infer, or fabricate entities, claims, or metrics for this unextracted artifact."
        )
    if val_assessment.tier == CollateralValueTier.HIGH_VALUE:
        return (
            "> **[MANDATORY VALUE REFLECTION]**: This is a HIGH_VALUE architectural or substantive diagram. "
            "You MUST explicitly reflect its components, system names, and data flows in Output 02 "
            "and project its entities and relationships into Output 03."
        )
    if val_assessment.tier == CollateralValueTier.MEDIUM_VALUE:
        return (
            "> **[STRATEGIC CONTEXT]**: This item contains MEDIUM_VALUE organizational or timeline context. "
            "Reflect key milestones or ownership in Output 02."
        )
    return (
        "> **[BACKGROUND REFERENCE]**: This is a LOW_VALUE or decorative collateral item. "
        "Use as background reference only; do NOT create standalone entity mutations in Output 03 unless explicitly cited."
    )


def _format_single_attachment_block(
    att: AttachmentRecord,
    idx: int,
    content: Optional[ExtractedAttachmentContent],
    alignment: Optional[CrossModalAttachmentAlignment],
    annotation: Optional[CollateralAnnotation] = None,
    thumbnail_path: Optional[str] = None,
) -> List[str]:
    lines = [
        f"### Attachment {idx}: `{att.filename}`",
        f"- **Type**: {att.attachment_type.value.upper()}",
        f"- **Stored Path**: `{att.relative_staged_path}`",
        f"- **SHA-256**: `{att.sha256}`",
        f"- **Attribution Mode**: `{att.attribution_mode}`",
    ]
    from .value_classifier import classify_collateral_value

    val_assessment = classify_collateral_value(att, content)
    lines.append(f"- **Value Tier**: `{val_assessment.tier.value}` (Score: {val_assessment.score:.2f})")
    lines.append(_format_value_tier_guidance(att.filename, val_assessment, content))

    # Emit COLLATERAL_REVIEWED exhibit block first so the model sees it prominently
    if att.attribution_mode == "COLLATERAL_REVIEWED":
        lines.extend(_format_collateral_reviewed_block(att, annotation, thumbnail_path))
    elif getattr(val_assessment.tier, "value", str(val_assessment.tier)) == "HIGH_VALUE":
        lines.append(
            "> **[BACKGROUND ARCHITECTURAL REFERENCE]**: While this item lacks a direct live drop timestamp, "
            "it contains substantive architectural or operational evidence. Ground system names, metrics, "
            "and topologies in Output 02 and project relevant entities into Output 03 without fabricating live review."
        )
    else:
        lines.append(
            "> **[BACKGROUND REFERENCE ONLY]**: This item was staged as background reference and was NOT "
            "reviewed live during the meeting. Do NOT assert in-session review, and do NOT fabricate "
            "Output 03 collateral entity mutations."
        )
    if content:
        lines.extend(_format_content_details(content))
    lines.extend(_format_spoken_mentions(att.attachment_id, alignment))
    lines.append("")
    return lines


def build_attachment_prompt_section(
    attachments: List[AttachmentRecord],
    extracted_contents: Dict[str, ExtractedAttachmentContent],
    alignment: Optional[CrossModalAttachmentAlignment] = None,
    collateral_annotations: Optional[List[CollateralAnnotation]] = None,
    thumbnail_paths: Optional[Dict[str, str]] = None,
) -> str:
    """
    Constructs a structured Markdown injection block detailing all uploaded meeting
    collateral, visual diagrams, and extracted topologies for the Stage L2 synthesis prompt.

    COLLATERAL_REVIEWED attachments get a dedicated exhibit block with drop timestamp,
    aligned turn range, and PNG thumbnail embed (if rendered). This is the primary
    mechanism that allows the LLM judge to recognize live-reviewed collateral as
    SOURCE_EXACT / COLLATERAL_REVIEWED rather than UNSUPPORTED.
    """
    if not attachments:
        return ""

    annotations_by_id: Dict[str, CollateralAnnotation] = {}
    if collateral_annotations:
        annotations_by_id = {a.attachment_id: a for a in collateral_annotations}
    thumbnails = thumbnail_paths or {}

    lines = [
        "## Uploaded Meeting Collateral & Visual Evidence",
        "The following visual images, architecture diagrams, and documents were uploaded or referenced during the meeting session.",
        "You MUST incorporate their substantive technical architecture, system components, and decisions across all three deliverables:",
        "- **Deliverable 01 (Forensic Transcript)**: Where dialogue references these visual items, annotate the spoken turn (e.g. `> *[Referenced Collateral: filename]*`). For COLLATERAL_REVIEWED items, embed the PNG thumbnail `![filename](path)` at the nearest turn.",
        "- **Deliverable 02 (Executive Recap)**: Feature key visual findings, architecture topologies, and data flows under Key Discussion Points & Strategic Synthesis. Embed COLLATERAL_REVIEWED thumbnails in the Technical Landscape section.",
        "- **Deliverable 03 (Canonical Signal Projection)**: Model extracted components, systems, and relationships in `entity_upserts` and `relationship_signals` using locator `attachment:<filename>` with `attribution_mode: COLLATERAL_REVIEWED`.",
        "",
        "> **GROUNDING RULE**: Claims citing a COLLATERAL_REVIEWED attachment must ground in concrete extracted visual text, "
        "tables, or architecture components via locator `attachment:<filename>`. Upload approval alone is not blanket confirmation "
        "of unextracted or fabricated facts; claims without supporting content evidence remain subject to grounding verification.",
        "",
    ]

    for idx, att in enumerate(attachments, 1):
        content = extracted_contents.get(att.attachment_id)
        ann = annotations_by_id.get(att.attachment_id)
        thumb = thumbnails.get(att.attachment_id)
        lines.extend(_format_single_attachment_block(att, idx, content, alignment, ann, thumb))

    return "\n".join(lines)


def serialize_attachment_records_with_content(
    records: List[Any],
    extracted_contents_map: Dict[str, Any],
) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for rec in records:
        d = rec.to_dict()
        cnt = extracted_contents_map.get(rec.attachment_id)
        if cnt:
            for field in ("summary", "extracted_text", "tables", "components", "claims"):
                val = getattr(cnt, field, None)
                if val:
                    d[field] = val
        serialized.append(d)
    return serialized


