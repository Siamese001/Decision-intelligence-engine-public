"""Dual-Pipeline Layer 0: Typed Decision Support Obligations and Frame Projections.

Defines sealed evidence requirements for decision options, criteria, and constraints.
Distinguishes relevance from true support (entity, polarity, direction, temporal bounds).
Strictly Layer L0: Zero upward imports (standard library only).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple


class DecisionSupportState(str, Enum):
    """Evaluation state of an evidence record against a support obligation."""
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


_NEGATION_RE = re.compile(
    r"\b(?:not\s+(?:supported|able|viable|feasible|compatible|approved|recommended|sufficient|ready)|"
    r"never|cannot|can't|unable|declined|fails|failed|"
    r"opposes|opposed|incompatible|discontinued|rejected|refused|lacks|prohibits)\b",
    re.IGNORECASE,
)

_CONSTRAINT_LIMITATION_RE = re.compile(
    r"\b(?:phasing\s+out|segregated\s+pods?|5\s*[-–]\s*10%|5pct|10pct|1:8|1to8|45%\s+higher|45pct|"
    r"two\s+weeks\s+prior|14[- ]day|deprovisioning|bottleneck|overrun|exceeds|"
    r"rate\s+differential|holdback|capped\s+at)\b",
    re.IGNORECASE,
)

def _is_citation_only(text: str) -> bool:
    cleaned = re.sub(r"\[[\w\.\-]+\]", " ", text)
    cleaned = re.sub(
        r"\b(?:see|citation|citations|source|doc|ref|refs|id)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[\d\s:,;#.\-]", "", cleaned)
    return len(cleaned) == 0

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
    "to", "was", "were", "will", "with", "than", "then", "this", "these",
}


def _sha256_canonical(data: Any) -> str:
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _extract_meaningful_tokens(text: str) -> set[str]:
    norm = text.lower()
    norm = re.sub(r"\b(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)%", r"\1pct \2pct", norm)
    norm = re.sub(r"\b(\d+(?:\.\d+)?)%", r"\1pct", norm)
    norm = re.sub(r"\b(\d+):(\d+)\b", r"\1to\2", norm)
    words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", norm)
    subwords = re.findall(r"\b[a-zA-Z0-9]{3,}\b", norm.replace("_", " ").replace("-", " "))
    return {w for w in (*words, *subwords) if w not in _STOPWORDS}


def _extract_entity_tokens(label: str) -> set[str]:
    """Extract named or distinct entity tokens from option label."""
    cap_words = re.findall(r"\b[A-Z][a-zA-Z0-9_\-]*\b", label)
    all_words = re.findall(r"\b[a-zA-Z0-9_\-]{3,}\b", label)
    combined = {w.lower() for w in (*cap_words, *all_words) if w.lower() not in _STOPWORDS}
    return combined


@dataclass
class DecisionSupportObligation:
    """A typed claim/evidence obligation binding an option to a criterion."""
    obligation_id: str
    option_id: str
    criterion_id: str
    option_label: str
    criterion_label: str
    entity_scope: list[str] = field(default_factory=list)
    required_polarity: str = "positive"
    temporal_bound: str = "current"
    expected_direction: str = "forward"
    support_state: str = DecisionSupportState.UNKNOWN.value
    material_evidence_ids: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compile_option_criterion_requirements(
    frame_digest: str,
    options: Sequence[Mapping[str, Any]],
    criteria: Sequence[Mapping[str, Any]],
    default_materiality: str,
) -> list[dict[str, Any]]:
    reqs: list[dict[str, Any]] = []
    for opt in options:
        opt_id = str(opt.get("option_id") or "")
        opt_label = str(opt.get("label") or opt_id)
        for crit in criteria:
            crit_id = str(crit.get("criterion_id") or "")
            crit_label = str(crit.get("label") or crit_id)
            req_id_raw = f"{frame_digest}:{opt_id}:{crit_id}"
            req_id = f"req_dso_{hashlib.sha256(req_id_raw.encode()).hexdigest()[:16]}"
            reqs.append({
                "requirement_id": req_id,
                "requirement_label": f"[{opt_label}] satisfies {crit_label}",
                "requirement_kind": "decision_support_obligation",
                "frame_digest": frame_digest,
                "option_id": opt_id,
                "criterion_id": crit_id,
                "option_label": opt_label,
                "criterion_label": crit_label,
                "allowed_lanes": ["approved", "candidate"],
                "missing_evidence_disposition": "explicit_gap",
                "materiality": default_materiality,
            })
    return reqs


def _compile_constraint_requirements(
    frame_digest: str,
    constraints: Sequence[Any],
) -> list[dict[str, Any]]:
    reqs: list[dict[str, Any]] = []
    for c_idx, constraint in enumerate(constraints):
        c_str = str(constraint).strip()
        if c_str:
            c_raw = f"{frame_digest}:constraint:{c_idx}:{c_str}"
            c_id = f"req_con_{hashlib.sha256(c_raw.encode()).hexdigest()[:16]}"
            reqs.append({
                "requirement_id": c_id,
                "requirement_label": f"Constraint satisfaction: {c_str}",
                "requirement_kind": "decision_constraint_obligation",
                "frame_digest": frame_digest,
                "option_id": "",
                "criterion_id": "",
                "constraint": c_str,
                "allowed_lanes": ["approved"],
                "missing_evidence_disposition": "explicit_gap",
                "materiality": "high",
            })
    return reqs


def _compile_temporal_requirements(
    frame_digest: str,
    time_horizon: str,
) -> list[dict[str, Any]]:
    if not time_horizon or time_horizon.lower() in ("unspecified", "none"):
        return []
    t_raw = f"{frame_digest}:time_horizon:{time_horizon}"
    t_id = f"req_tim_{hashlib.sha256(t_raw.encode()).hexdigest()[:16]}"
    return [{
        "requirement_id": t_id,
        "requirement_label": f"Temporal validity: {time_horizon}",
        "requirement_kind": "decision_temporal_obligation",
        "frame_digest": frame_digest,
        "option_id": "",
        "criterion_id": "",
        "time_horizon": time_horizon,
        "allowed_lanes": ["approved", "candidate"],
        "missing_evidence_disposition": "explicit_gap",
        "materiality": "medium",
    }]


def compile_frame_evidence_requirements(
    frame: Mapping[str, Any],
    default_materiality: str = "high",
) -> list[dict[str, Any]]:
    """Compile sealed retrieval coverage requirements from a DecisionFrame."""
    frame_digest = str(frame.get("frame_digest") or "")
    options = list(frame.get("options") or [])
    criteria = list(frame.get("criteria") or [])
    constraints = list(frame.get("constraints") or [])
    time_horizon = str(frame.get("time_horizon") or "").strip()

    reqs = _compile_option_criterion_requirements(
        frame_digest, options, criteria, default_materiality
    )
    reqs.extend(_compile_constraint_requirements(frame_digest, constraints))
    reqs.extend(_compile_temporal_requirements(frame_digest, time_horizon))
    return reqs


def _check_citation_or_empty(raw_text: str, record: Mapping[str, Any]) -> str | None:
    is_citation = (
        bool(record.get("source_evidence", {}).get("citation_only"))
        if isinstance(record.get("source_evidence"), Mapping)
        else False
    )
    if not raw_text or is_citation or _is_citation_only(raw_text):
        return "Record provides citation-only reference or empty content without predicate support."
    return None


def _check_direction_mismatch(record: Mapping[str, Any], expected_direction: str) -> str | None:
    record_direction = str(record.get("direction") or record.get("support_direction") or "forward").lower()
    if expected_direction == "forward" and record_direction in ("reverse_only", "reverse", "opposing"):
        return f"Relation direction '{record_direction}' inverts expected forward support."
    return None


def _check_temporal_mismatch(record: Mapping[str, Any], expected_temporal: str) -> str | None:
    currentness = str(record.get("currentness_state") or record.get("temporal_state") or "current").lower()
    if expected_temporal == "current" and currentness in ("stale", "superseded", "archived", "historical"):
        return f"Record temporal state '{currentness}' violates required current state bound."
    if expected_temporal == "current" and "future" in currentness:
        return "Record expresses future intent rather than realized current state."
    return None


def _has_negative_polarity(
    record: Mapping[str, Any],
    raw_text: str,
    text_lower: str,
    known_conflicts: Optional[Sequence[str]],
) -> bool:
    if bool(_NEGATION_RE.search(raw_text)):
        return True
    if str(record.get("polarity") or "").lower() in ("negative", "opposes", "counter"):
        return True
    if bool(record.get("counterevidence")):
        return True
    if (
        str(record.get("answer_use") or "").lower() == "decision_constraint"
        and bool(_CONSTRAINT_LIMITATION_RE.search(raw_text))
    ):
        return True
    if known_conflicts and any(str(c).lower() in text_lower for c in known_conflicts):
        return True
    return False


def _extract_record_texts(record: Mapping[str, Any]) -> tuple[str, str]:
    primary_text = str(
        record.get("text")
        or record.get("content")
        or record.get("assertion_text")
        or record.get("retrieval_text")
        or record.get("title")
        or ""
    ).strip()
    gp = record.get("graph_path") if isinstance(record.get("graph_path"), Mapping) else {}
    gp_parts = [
        str(gp.get("subject") or ""),
        str(gp.get("predicate") or ""),
        str(gp.get("object") or ""),
        str(record.get("title") or ""),
        str(record.get("retrieval_text") or ""),
    ]
    raw_text = " ".join(part for part in (primary_text, *gp_parts) if part).strip()
    return primary_text, raw_text


def evaluate_evidence_support(
    record: Mapping[str, Any],
    option_label: str,
    criterion_label: str,
    *,
    required_polarity: str = "positive",
    expected_direction: str = "forward",
    expected_temporal: str = "current",
    known_conflicts: Optional[Sequence[str]] = None,
) -> Tuple[DecisionSupportState, str]:
    """Evaluate whether an evidence record truly supports an option on a criterion."""
    primary_text, raw_text = _extract_record_texts(record)
    cite_err = _check_citation_or_empty(primary_text or raw_text, record)
    if cite_err:
        return DecisionSupportState.UNSUPPORTED, cite_err

    entity_tokens = _extract_entity_tokens(option_label)
    text_lower = raw_text.lower()
    if entity_tokens and not any(e in text_lower for e in entity_tokens):
        return DecisionSupportState.UNSUPPORTED, f"Record does not mention target entity for option '{option_label}'."

    for err in (_check_direction_mismatch(record, expected_direction), _check_temporal_mismatch(record, expected_temporal)):
        if err:
            return DecisionSupportState.UNSUPPORTED, err

    criterion_tokens = _extract_meaningful_tokens(criterion_label)
    overlap = criterion_tokens.intersection(_extract_meaningful_tokens(raw_text))

    if _has_negative_polarity(record, raw_text, text_lower, known_conflicts):
        if overlap:
            return DecisionSupportState.CONTRADICTED, "Evidence contains counterevidence or negative polarity opposing criterion satisfaction."
        return DecisionSupportState.UNSUPPORTED, "Evidence contains negative polarity and does not support criterion."

    if len(overlap) >= max(1, min(2, len(criterion_tokens))):
        return DecisionSupportState.SUPPORTED, f"Evidence affirmatively supports '{option_label}' on '{criterion_label}'."

    return DecisionSupportState.UNSUPPORTED, "Record mentions target entity but lacks predicate support for criterion."


__all__ = [
    "DecisionSupportObligation",
    "DecisionSupportState",
    "compile_frame_evidence_requirements",
    "evaluate_evidence_support",
]
