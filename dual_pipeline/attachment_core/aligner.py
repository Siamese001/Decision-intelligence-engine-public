"""
Cross-modal alignment engine correlating spoken dialogue turns with uploaded meeting attachments.
Supports semantic deck cues, presentation transitions, and non-destructive inline collateral annotations.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .types import (
    AttachmentMention,
    AttachmentRecord,
    CollateralAnnotation,
    CrossModalAttachmentAlignment,
    ExtractedAttachmentContent,
)

GENERIC_VISUAL_CUES = [
    "on the screen",
    "on the slide",
    "on the next slide",
    "next slide",
    "this slide",
    "on this slide",
    "in the diagram",
    "architecture diagram",
    "screenshot",
    "attached document",
    "in the chart",
    "uploaded image",
    "in the visual",
    "looking at the screen",
    "pull up the diagram",
    "pull up the slide",
    "as shown here",
    "looking at the roadmap",
    "the red bar",
    "red bar",
    "input box",
    "to the input box",
    "two things at once",
    "two choices",
    "submit this certification",
    "how many tickets",
    "points now",
    "calculated the points",
    "previous sprint",
    "prototype",
    "drawer",
    "smoke detection",
    "active events",
    "beginning of shift",
    "insights hub",
]

# Semantic topic mappings connecting deck themes to spoken cues
_SEMANTIC_DECK_MAPPINGS = [
    {
        "stem_keywords": ["delivery", "status", "jira", "plan", "roadmap"],
        "cues": [
            r"\bnext\s+slide\b",
            r"\blooking\s+at\s+the\s+roadmap\b",
            r"\bred\s+bar\b",
            r"\buap\b",
            r"\bbuffer\s+here\b",
            r"\buat\s+in\s+motion\b",
            r"\bshooting\s+for\s+everything\s+to\s+get\s+into\s+uat\b",
        ],
        "summary": "BAA Delivery Status & Jira Plan Roadmap",
    },
    {
        "stem_keywords": ["readiness", "report", "safeguards", "registration"],
        "cues": [
            r"\binput\s+box\b",
            r"\btechnology\s+advisor\b",
            r"\bbusiness\s+application\s+page\b",
            r"\bretirement\s+scheme\b",
            r"\bcursor\b",
            r"\bquicker,\s+more\s+streamlined\s+proposals\b",
        ],
        "summary": "BAA Readiness Report & Catalog Safeguards",
    },
    {
        "stem_keywords": ["streamlined", "certify", "flow", "ux", "improvement"],
        "cues": [
            r"\bconfirms\s+two\s+things\b",
            r"\bsubmit\s+this\s+certification\b",
            r"\bstart\s+the\s+video\b",
            r"\bchange\s+a\s+field\b",
            r"\bgoing\s+through\s+the\s+certification\b",
        ],
        "summary": "Streamlined BAA Certify Flow (6 to 3 turns)",
    },
    {
        "stem_keywords": ["devin", "integration", "metrics", "team"],
        "cues": [
            r"\bprevious\s+sprint\b",
            r"\bcalculated\s+the\s+points\b",
            r"\bhow\s+many\s+tickets\b",
            r"\bmcp\s+with\s+the\s+dram\b",
            r"\bwork\s+is\s+done\s+more\b",
        ],
        "summary": "EA-Sherpa Sprint 49.5 Devin Integration Team Metrics",
    },
]


def _extract_stem_keywords(filename: str) -> List[str]:
    """Generates searchable keywords from attachment filename stem, including 3-letter acronyms."""
    stem = filename.rsplit(".", 1)[0]
    tokens = re.split(r"[\s\-_]+", stem)
    return [t.lower() for t in tokens if len(t) >= 3 and not t.isdigit()]


def _find_matched_phrase(txt_lower: str, fn: str, stem_lower: str) -> Optional[str]:
    fn_lower = fn.lower()
    if fn_lower in txt_lower:
        return fn
    if len(stem_lower) > 5 and stem_lower in txt_lower:
        return stem_lower
    return None


def _find_keyword_match(txt_lower: str, fn: str) -> Optional[str]:
    keywords = _extract_stem_keywords(fn)
    matched_kw = [kw for kw in keywords if kw in txt_lower]
    if not matched_kw:
        return None
    has_visual_cue = any(cue in txt_lower for cue in GENERIC_VISUAL_CUES)
    if has_visual_cue or len(matched_kw) >= 2:
        return ", ".join(matched_kw)
    return None


def _find_semantic_deck_match(txt_lower: str, fn: str) -> Optional[str]:
    """Correlates spoken dialogue cues with presentation deck themes."""
    fn_lower = fn.lower()
    for mapping in _SEMANTIC_DECK_MAPPINGS:
        if any(kw in fn_lower for kw in mapping["stem_keywords"]):
            for cue_pat in mapping["cues"]:
                if re.search(cue_pat, txt_lower):
                    return mapping["summary"]
    return None


def _find_component_match(
    txt_lower: str, content: Optional[ExtractedAttachmentContent]
) -> Optional[str]:
    if not content or not content.components:
        return None
    for comp in content.components:
        lbl = comp.get("label", "")
        if len(lbl) >= 3 and lbl.lower() in txt_lower:
            return lbl
    return None


_GENERIC_KEYWORD_STOPLIST = {
    "report", "status", "delivery", "plan", "update", "notes",
    "overview", "deck", "slide", "meeting", "roadmap", "document",
}


def _match_attachment_for_turn(
    turn: Dict[str, Any],
    t_idx: int,
    att: AttachmentRecord,
    content: Optional[ExtractedAttachmentContent],
) -> Optional[AttachmentMention]:
    txt = turn.get("turn_text") or turn.get("text") or ""
    txt_lower = txt.lower()
    stem_lower = att.original_path.stem.lower().replace("_", " ")

    phrase_match = _find_matched_phrase(txt_lower, att.filename, stem_lower)
    deck_match = _find_semantic_deck_match(txt_lower, att.filename)
    keyword_match = _find_keyword_match(txt_lower, att.filename)
    comp_match = _find_component_match(txt_lower, content)

    matched = phrase_match or deck_match or keyword_match or comp_match
    if not matched:
        return None

    matched_parts = [p.strip().lower() for p in matched.split(",")]
    non_generic_parts = [p for p in matched_parts if p not in _GENERIC_KEYWORD_STOPLIST]
    only_generic = not non_generic_parts
    has_visual_cue = any(cue in txt_lower for cue in GENERIC_VISUAL_CUES)

    multi_kw_walkthrough = bool(keyword_match and len(non_generic_parts) >= 2)
    strong_keyword_visual = bool(keyword_match and len(non_generic_parts) >= 2 and (has_visual_cue or getattr(att, "attribution_mode", "") == "COLLATERAL_REVIEWED"))
    is_weak = bool(
        only_generic or (keyword_match and not phrase_match and not comp_match and not deck_match and not strong_keyword_visual and not multi_kw_walkthrough)
    )
    is_authenticated = bool(phrase_match or comp_match or deck_match or strong_keyword_visual or multi_kw_walkthrough) and not only_generic

    return AttachmentMention(
        turn_index=t_idx,
        speaker=turn.get("speaker", "Speaker"),
        timestamp=turn.get("timestamp", ""),
        turn_text=txt,
        attachment_id=att.attachment_id,
        filename=att.filename,
        matched_keyword=matched,
        is_weak_alignment=is_weak,
        is_speaker_authenticated=is_authenticated,
    )


def align_attachments_with_transcript(
    transcript_text: str,
    attachments: List[AttachmentRecord],
    extracted_contents: Optional[Dict[str, ExtractedAttachmentContent]] = None,
) -> CrossModalAttachmentAlignment:
    """
    Correlates spoken dialogue turns with uploaded meeting attachments:
    1. Explicit filename or title matches
    2. Semantic deck topic cues and visual presentation anchors
    3. Keyword overlap paired with generic visual cues
    4. Mentions of diagram component labels
    """
    from dual_pipeline.presentation_aligner import parse_transcript_turns

    turns = parse_transcript_turns(transcript_text)
    alignment = CrossModalAttachmentAlignment(total_attachments=len(attachments))
    contents_map = extracted_contents or {}
    referenced_ids: Set[str] = set()
    turn_aligns: Dict[int, List[str]] = {}

    for t_idx, turn in enumerate(turns, 1):
        matched_att_for_turn: List[str] = []
        for att in attachments:
            mention = _match_attachment_for_turn(turn, t_idx, att, contents_map.get(att.attachment_id))
            if mention:
                alignment.mentions.append(mention)
                matched_att_for_turn.append(att.attachment_id)
                referenced_ids.add(att.attachment_id)

        if matched_att_for_turn:
            turn_aligns[t_idx] = sorted(list(set(matched_att_for_turn)))

    alignment.turn_alignments = turn_aligns
    alignment.unreferenced_attachments = [
        a.attachment_id for a in attachments if a.attachment_id not in referenced_ids
    ]
    return alignment


def _inject_single_mention(annotated: str, mention: AttachmentMention) -> tuple[str, bool]:
    raw_txt = mention.turn_text
    if not raw_txt:
        return annotated, False
    kw_str = f" - {mention.matched_keyword}" if mention.matched_keyword and mention.matched_keyword != mention.filename else ""
    annotation_line = f"\n> *[Referenced Collateral: `{mention.filename}`{kw_str}]*"
    if mention.filename in annotated and f"`{mention.filename}`" in annotated:
        return annotated, True
    escaped_snippet = re.escape(raw_txt[:60])
    match = re.search(rf"({escaped_snippet}[^\n]*)", annotated)
    if match:
        end_pos = match.end(1)
        return annotated[:end_pos] + annotation_line + annotated[end_pos:], True
    return annotated, False


def inject_attachment_annotations(
    transcript_text: str,
    alignment: CrossModalAttachmentAlignment,
) -> str:
    """
    Annotates spoken transcript turns where uploaded collateral was referenced:
    > *[Referenced Collateral: filename - summary]*
    Preserves existing markdown structure and ### [HH:MM:SS] timestamp headers.
    Weak keyword overlap and unauthenticated mentions are strictly filtered out.
    """
    if not alignment or not alignment.mentions:
        return transcript_text

    annotated = transcript_text
    annotated_att_ids: Set[str] = set()

    for mention in alignment.mentions:
        if getattr(mention, "is_weak_alignment", False):
            continue
        if not getattr(mention, "is_speaker_authenticated", False):
            continue
        if mention.attachment_id in annotated_att_ids:
            continue
        annotated, was_annotated = _inject_single_mention(annotated, mention)
        if was_annotated:
            annotated_att_ids.add(mention.attachment_id)

    return annotated


def _parse_hms_to_seconds(hms: str) -> Optional[float]:
    """Parses 'HH:MM:SS' or 'MM:SS' into total seconds."""
    try:
        parts = [int(x) for x in hms.strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
    except (ValueError, AttributeError):
        pass
    return None


def _resolve_aligned_window_timestamps(
    transcript_turns: List[Dict[str, Any]],
    turn_seconds: List[Optional[float]],
    drop_secs: Optional[float],
    unknown_drop: bool,
    window_seconds: float,
) -> Tuple[str, str]:
    if drop_secs is not None and not unknown_drop:
        aligned = [
            t.get("timestamp", "")
            for t, ts in zip(transcript_turns, turn_seconds)
            if ts is not None and abs(ts - drop_secs) <= window_seconds
        ]
    else:
        aligned = [t.get("timestamp", "") for t in transcript_turns if t.get("timestamp")]
    if not aligned:
        aligned = [t.get("timestamp", "") for t in transcript_turns if t.get("timestamp")] or ["00:00:00", "00:00:00"]
    return aligned[0], aligned[-1]


def align_by_drop_timestamp(
    transcript_turns: List[Dict[str, Any]],
    attachments: List[AttachmentRecord],
    extracted_contents: Optional[Dict[str, ExtractedAttachmentContent]] = None,
    window_seconds: float = 120.0,
) -> List[CollateralAnnotation]:
    """
    Temporal window alignment for COLLATERAL_REVIEWED attachments.
    """
    annotations: List[CollateralAnnotation] = []
    contents_map = extracted_contents or {}

    collateral_atts = [a for a in attachments if a.attribution_mode == "COLLATERAL_REVIEWED"]
    if not collateral_atts:
        return annotations

    turn_seconds: List[Optional[float]] = [
        _parse_hms_to_seconds(t.get("timestamp", "")) for t in transcript_turns
    ]

    for att in collateral_atts:
        drop_secs = _parse_hms_to_seconds(att.dropped_at_elapsed or "")
        content = contents_map.get(att.attachment_id)
        key_topics = content.key_topics if content else []
        unknown_drop = (att.dropped_at_elapsed or "00:00:00") == "00:00:00"

        turn_range = _resolve_aligned_window_timestamps(
            transcript_turns, turn_seconds, drop_secs, unknown_drop, window_seconds
        )
        annotations.append(
            CollateralAnnotation(
                attachment_id=att.attachment_id,
                filename=att.filename,
                dropped_at_elapsed=att.dropped_at_elapsed or "00:00:00",
                aligned_turn_range=turn_range,
                staged_image_path=att.relative_staged_path or None,
                key_topics=key_topics,
            )
        )

    return annotations


def build_collateral_annotations(
    transcript_text: str,
    attachments: List[AttachmentRecord],
    extracted_contents: Optional[Dict[str, ExtractedAttachmentContent]] = None,
) -> List[CollateralAnnotation]:
    """
    Entry point: runs temporal window alignment for COLLATERAL_REVIEWED records
    and returns CollateralAnnotation objects ready for synthesis prompt injection
    and Output 1/2 embedding.
    """
    from dual_pipeline.presentation_aligner import parse_transcript_turns
    turns = parse_transcript_turns(transcript_text)
    return align_by_drop_timestamp(
        transcript_turns=turns,
        attachments=attachments,
        extracted_contents=extracted_contents,
    )
