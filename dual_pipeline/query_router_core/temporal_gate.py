"""Fail-closed temporal source existence gate, unpromoted DLQ/worktree diagnostics, and deliverable alias resolution."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core import ROOT

_DELIVERABLE_MAP: dict[str, str] = {
    "1": "01_hitl_resolved_transcript.md",
    "01": "01_hitl_resolved_transcript.md",
    "2": "02_executive_intelligence_report.md",
    "02": "02_executive_intelligence_report.md",
    "3": "03_canonical_signal_projection.json",
    "03": "03_canonical_signal_projection.json",
}

_STOPWORDS = frozenset(
    (
        "summarize", "summary", "talking", "recap", "points", "for", "the", "meeting",
        "today", "todays", "yesterday", "yesterdays", "completed", "audience", "team",
        "define", "action", "items", "and", "complete", "this", "scope", "outputs",
        "output", "deliverable", "deliverables", "artifact", "artifacts", "review",
        "what", "who", "when", "where", "why", "how", "with", "from", "about",
    )
) | frozenset(
    (
        "words", "query", "phase", "am", "pm", "et", "est", "edt",
        "jan", "january", "feb", "february", "mar", "march", "apr", "april",
        "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept",
        "september", "oct", "october", "nov", "november", "dec", "december",
    )
)

_DOMAIN_ACRONYMS = frozenset({
    "ai", "bu", "vp", "qa", "ed", "sow", "nee", "nea", "edo", "it", "hr", "ui", "ux", "db", "pr", "ci", "cd",
})

_MONTH_NAME_MAP: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def resolve_deliverable_aliases(question: str) -> list[str]:
    """Bind 'output(s) 1/2/3' or 'deliverable(s) 1/2/3' strictly to canonical synthesis artifacts."""
    if not question or not isinstance(question, str):
        return []
    found: list[str] = []
    pattern = r"\b(?:outputs?|deliverables?|artifacts?)\s*(?:0?[123])(?:\s*(?:,|and|&|or|-|to)\s*(?:outputs?\s*|deliverables?\s*)?0?[123])*\b"
    for match in re.finditer(pattern, question, re.IGNORECASE):
        for num in re.findall(r"0?[123]", match.group(0)):
            mapped = _DELIVERABLE_MAP.get(num)
            if mapped and mapped not in found:
                found.append(mapped)
    return found


def _parse_ref_date(reference_date: str | None) -> datetime:
    if reference_date:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(reference_date.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _normalize_year(yr_str: str | None, reference_date: str | None) -> int:
    if not yr_str:
        return _parse_ref_date(reference_date).year
    raw_yr = int(yr_str)
    return (2000 + raw_yr) if raw_yr < 100 else raw_yr


def _extract_month_name_date(question: str, reference_date: str | None) -> str | None:
    m_day_month = re.search(
        r"\b(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\s+(?:of\s+)?"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?:\.?\s*,?\s*(20\d{2}|\d{2}))?\b",
        question,
        re.IGNORECASE,
    )
    if m_day_month:
        dd = int(m_day_month.group(1))
        mm = _MONTH_NAME_MAP[m_day_month.group(2).lower()]
        yyyy = _normalize_year(m_day_month.group(3), reference_date)
        return f"{yyyy:04d}-{mm:02d}-{dd:02d}"
    m_month = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\.?\s+(0?[1-9]|[12]\d|3[01])(?!(?:\d))(?:st|nd|rd|th)?(?:\s*,?\s*(20\d{2}|\d{2}))?\b",
        question,
        re.IGNORECASE,
    )
    if m_month:
        mm = _MONTH_NAME_MAP[m_month.group(1).lower()]
        dd = int(m_month.group(2))
        yyyy = _normalize_year(m_month.group(3), reference_date)
        return f"{yyyy:04d}-{mm:02d}-{dd:02d}"
    return None


def extract_temporal_target_date(question: str, reference_date: str | None = None) -> str | None:
    """Extract normalized YYYY-MM-DD target date when query specifies an explicit date or relative day."""
    if not question or not isinstance(question, str):
        return None
    m_iso = re.search(r"\b(20\d{2})-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])\b", question)
    if m_iso:
        return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
    m_us = re.search(r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-](20\d{2}|\d{2})\b", question)
    if m_us:
        yyyy = _normalize_year(m_us.group(3), reference_date)
        return f"{yyyy:04d}-{int(m_us.group(1)):02d}-{int(m_us.group(2)):02d}"
    if month_date := _extract_month_name_date(question, reference_date):
        return month_date
    m_compact = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", question)
    if m_compact:
        return f"{m_compact.group(1)}-{m_compact.group(2)}-{m_compact.group(3)}"
    ref_dt = _parse_ref_date(reference_date)
    if re.search(r"\btoday(?:\'s|s)?\b", question, re.IGNORECASE):
        return ref_dt.strftime("%Y-%m-%d")
    if re.search(r"\byesterday(?:\'s|s)?\b", question, re.IGNORECASE):
        return (ref_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    return None


def extract_meeting_topic_tokens(question: str) -> list[str]:
    """Extract distinctive topic and stakeholder tokens from a meeting query."""
    if not question or not isinstance(question, str):
        return []
    tokens: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]{2,}", question.lower()):
        if raw.isdigit():
            continue
        if len(raw) == 2 and raw not in _DOMAIN_ACRONYMS:
            continue
        if raw not in _STOPWORDS and raw not in tokens:
            tokens.append(raw)
    return tokens


def _matches_topic_tokens(text_blob: str, topic_tokens: list[str]) -> bool:
    if not topic_tokens:
        return True
    lower_blob = text_blob.lower()
    blob_words = set(re.findall(r"[a-z0-9]+", lower_blob))
    matched_count = sum(
        1 for tok in topic_tokens
        if (tok in blob_words if len(tok) <= 3 else tok in lower_blob)
    )
    required = 1 if len(topic_tokens) == 1 else min(2, len(topic_tokens))
    return matched_count >= required


def _parse_dlq_blocker_if_matching(path: Path, date_tokens: tuple[str, ...], topic_tokens: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    blob = json.dumps(payload.get("metadata") or {}) + " " + str(payload.get("input_path") or "")
    if not any(dt in blob for dt in date_tokens) or not _matches_topic_tokens(blob, topic_tokens):
        return None
    return {
        "dlq_id": str(payload.get("dlq_id") or path.stem),
        "stage": str(payload.get("stage") or "UNKNOWN"),
        "error_type": str(payload.get("error_type") or "UNKNOWN"),
        "error_message": str(payload.get("error_message") or ""),
        "stable_op_id": str(payload.get("stable_op_id") or ""),
    }


def _scan_dlq_entries(dlq_dir: Path, date_tokens: tuple[str, ...], topic_tokens: list[str]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not dlq_dir.is_dir():
        return blockers
    for path in sorted(dlq_dir.glob("dlq_*.json"), reverse=True):
        if item := _parse_dlq_blocker_if_matching(path, date_tokens, topic_tokens):
            blockers.append(item)
            if len(blockers) >= 25:
                break
    return blockers


def _scan_worktrees(worktrees_dir: Path, date_tokens: tuple[str, ...], topic_tokens: list[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if not worktrees_dir.is_dir():
        return matches
    for entry in sorted(worktrees_dir.iterdir()):
        if not entry.is_dir():
            continue
        name_lower = entry.name.lower()
        if not any(dt.lower() in name_lower for dt in date_tokens):
            continue
        if not _matches_topic_tokens(name_lower, topic_tokens):
            continue
        matches.append({"worktree_slug": entry.name, "worktree_path": str(entry)})
    return matches


def scan_unpromoted_dlq_and_worktrees(
    target_date: str,
    topic_tokens: list[str],
    repo_root: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Inspect .local/dual_pipeline/dlq and .worktrees without scanning output/ directly."""
    root = repo_root or ROOT
    rt_root = runtime_root or (root / ".local" / "dual_pipeline")
    parts = target_date.split("-")
    date_tokens: tuple[str, ...] = (target_date,)
    if len(parts) == 3:
        yyyy, mm, dd = parts[0], parts[1], parts[2]
        date_tokens = (target_date, f"{mm}/{dd}/{yyyy}", f"{mm}-{dd}-{yyyy}", f"{mm}_{dd}_{yyyy}", f"{yyyy}{mm}{dd}")
    return {
        "dlq_blockers": _scan_dlq_entries(rt_root / "dlq", date_tokens, topic_tokens),
        "unpromoted_worktrees": _scan_worktrees(root / ".worktrees", date_tokens, topic_tokens),
    }


def _load_source_2hop_context(cursor: Any, src_id: str) -> str:
    extra_parts: list[str] = []
    try:
        cursor.execute(
            """
            SELECT a.claim_text, a.subject_node_id, a.object_node_id,
                   ns.canonical_name, no.canonical_name
            FROM assertion a
            LEFT JOIN node ns ON ns.node_id = a.subject_node_id
            LEFT JOIN node no ON no.node_id = a.object_node_id
            WHERE a.primary_source_id = ?
            LIMIT 50
            """,
            (src_id,),
        )
        for c_row in cursor.fetchall():
            extra_parts.extend(str(x) for x in c_row if x)
    except Exception:
        try:
            cursor.execute(
                "SELECT claim_text, subject_node_id, object_node_id FROM assertion WHERE primary_source_id = ? LIMIT 50",
                (src_id,),
            )
            for c_row in cursor.fetchall():
                extra_parts.extend(str(x) for x in c_row if x)
        except Exception:
            pass
    return " ".join(extra_parts)


def _query_matching_sources(conn: Any, target_date: str, topic_tokens: list[str]) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_id, source_name, source_date, cold_archive_ref FROM source WHERE source_date = ?",
        (target_date,),
    )
    matched: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        src_id, src_name, src_date, archive_ref = str(row[0]), str(row[1] or ""), str(row[2] or ""), str(row[3] or "")
        base_blob = f"{src_name} {archive_ref}"
        if not _matches_topic_tokens(base_blob, topic_tokens):
            hop_blob = f"{base_blob} {_load_source_2hop_context(cursor, src_id)}"
            if not _matches_topic_tokens(hop_blob, topic_tokens):
                continue
        matched.append({
            "source_id": src_id,
            "source_name": src_name,
            "source_date": src_date,
            "cold_archive_ref": archive_ref,
        })
    return matched


def verify_temporal_source_existence(
    question: str,
    conn: Any = None,
    reference_date: str | None = None,
    repo_root: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Verify requested meeting date exists in graph.sqlite; fail closed with NOT_IN_KNOWLEDGE_GRAPH if absent."""
    aliases = resolve_deliverable_aliases(question)
    target_date = extract_temporal_target_date(question, reference_date=reference_date)
    if target_date is None:
        return {
            "status": "NO_TEMPORAL_CONSTRAINT",
            "requested_date": None,
            "matching_sources": [],
            "unpromoted_worktrees": [],
            "dlq_blockers": [],
            "deliverable_aliases_requested": aliases,
            "message": None,
        }
    topic_tokens = extract_meeting_topic_tokens(question)
    matched = _query_matching_sources(conn, target_date, topic_tokens) if conn is not None else []
    if matched:
        return {
            "status": "FOUND_IN_KNOWLEDGE_GRAPH",
            "requested_date": target_date,
            "matching_sources": matched,
            "unpromoted_worktrees": [],
            "dlq_blockers": [],
            "deliverable_aliases_requested": aliases,
            "message": f"Verified {len(matched)} source(s) in Knowledge Graph for {target_date}.",
        }
    diag = scan_unpromoted_dlq_and_worktrees(target_date, topic_tokens, repo_root=repo_root, runtime_root=runtime_root)
    return {
        "status": "NOT_IN_KNOWLEDGE_GRAPH",
        "requested_date": target_date,
        "matching_sources": [],
        "unpromoted_worktrees": diag["unpromoted_worktrees"],
        "dlq_blockers": diag["dlq_blockers"],
        "deliverable_aliases_requested": aliases,
        "message": (
            f"Requested meeting on {target_date} is not in the Knowledge Graph (graph.sqlite). "
            "Silent fallback to historical meetings is prohibited."
        ),
    }
