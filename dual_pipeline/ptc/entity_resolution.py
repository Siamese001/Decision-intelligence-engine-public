"""
dual_pipeline/ptc/entity_resolution.py

Programmatic Entity Resolution & Grounding Harness (PERH) for Pipeline A Stage 03.
Enforces C-03/C-04 closed entity prefixes, W2 Semantic Spine canonical predicates,
and W3 temporal hygiene stamps (MM/DD/YYYY) without multi-turn LLM context bloat.
Layer: L3 (Domain Extraction & Normalization).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

ALLOWED_ENTITY_PREFIXES: frozenset[str] = frozenset({
    "person:",
    "org:",
    "system:",
    "workstream:",
    "project:",
    "metric:",
    "role:",
    "policy:",
})

ALLOWED_STATEMENT_KINDS: frozenset[str] = frozenset({
    "summary",
    "decision",
    "risk",
    "dependency",
    "technical",
    "strategic",
})

CANONICAL_PREDICATES: frozenset[str] = frozenset({
    "depends_on",
    "creates_risk",
    "mitigated_by",
    "owns",
    "participates_in",
    "governs",
    "implements",
    "affects",
    "blocks",
    "replaces",
    "derived_from",
    "evidenced_by",
})

PREDICATE_NORMALIZATION_MAP: Dict[str, str] = {
    "depends": "depends_on",
    "dependency": "depends_on",
    "dependency_on": "depends_on",
    "relies_on": "depends_on",
    "risk": "creates_risk",
    "risks": "creates_risk",
    "mitigates": "mitigated_by",
    "mitigation": "mitigated_by",
    "owner": "owns",
    "owned_by": "owns",
    "participates": "participates_in",
    "participant": "participates_in",
    "governed_by": "governs",
    "implements": "implements",
    "implemented_by": "implements",
    "impacts": "affects",
    "affects": "affects",
    "blocked_by": "blocks",
    "blocks": "blocks",
}

DATE_FORMAT_REGEX = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def normalize_prefix(entity_id: str, default_prefix: str = "system:") -> str:
    """Ensures an entity ID starts with a valid C-03 allowed prefix."""
    entity_str = entity_id.strip()
    for prefix in ALLOWED_ENTITY_PREFIXES:
        if entity_str.startswith(prefix):
            return entity_str
    # Prepend default prefix if none matched
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", entity_str).strip("_").lower()
    return f"{default_prefix}{clean_name}"


def normalize_predicate(raw_predicate: str) -> str:
    """Normalizes relation verbs to canonical W2 Semantic Spine vocabulary."""
    pred_clean = raw_predicate.strip().lower().replace(" ", "_").replace("-", "_")
    if pred_clean in CANONICAL_PREDICATES:
        return pred_clean
    return PREDICATE_NORMALIZATION_MAP.get(pred_clean, "affects")


def format_temporal_date(date_str: Optional[str] = None) -> str:
    """Enforces strict MM/DD/YYYY temporal date format (C-07 / Domain SSOT)."""
    if date_str and DATE_FORMAT_REGEX.match(date_str.strip()):
        return date_str.strip()
    now = datetime.now()
    return now.strftime("%m/%d/%Y")


@dataclass
class PTCTriple:
    """Structured graph signal triple with temporal and provenance metadata."""
    subject: str
    predicate: str
    object: str
    kind: str = "technical"
    statement_text: str = ""
    observed_at: str = ""
    stale_after: str = ""
    confidence: float = 0.90
    citation: Optional[str] = None


@dataclass(frozen=True)
class PTCEntityResolutionPlan:
    """Input plan declaring candidate triples and contextual alias catalog."""
    triples: Sequence[Dict[str, Any] | PTCTriple]
    meeting_date: Optional[str] = None
    validity_months: int = 6
    catalog_alias_map: Mapping[str, str] = field(default_factory=dict)


@dataclass
class PTCResolvedProjection:
    """Canonical, schema-compliant graph projection and ambiguity register."""
    promoted_triples: List[Dict[str, Any]]
    ambiguity_locks: List[Dict[str, Any]]
    rejected_triples: List[Dict[str, Any]]
    total_triples: int
    promoted_count: int
    ambiguity_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PTCEntityResolver:
    """In-process entity resolver enforcing ontology constraints and temporal hygiene."""

    def resolve(self, plan: PTCEntityResolutionPlan) -> PTCResolvedProjection:
        promoted: List[Dict[str, Any]] = []
        ambiguities: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        observed_at = format_temporal_date(plan.meeting_date)
        # Compute default stale_after (e.g. 6 months forward)
        try:
            obs_dt = datetime.strptime(observed_at, "%m/%d/%Y")
            stale_month = obs_dt.month + plan.validity_months
            stale_year = obs_dt.year + (stale_month - 1) // 12
            stale_month = (stale_month - 1) % 12 + 1
            stale_after = f"{stale_month:02d}/{obs_dt.day:02d}/{stale_year:04d}"
        except Exception:
            stale_after = observed_at

        alias_map = {k.lower(): v for k, v in plan.catalog_alias_map.items()}

        for item in plan.triples:
            triple = item if isinstance(item, PTCTriple) else PTCTriple(**item)

            # 1. Closed Enum Kind Enforcement (C-02)
            kind = triple.kind.lower()
            if kind not in ALLOWED_STATEMENT_KINDS:
                rejected.append({
                    "triple": asdict(triple),
                    "reason": f"INVALID_KIND: '{triple.kind}' not in {sorted(ALLOWED_STATEMENT_KINDS)}",
                })
                continue

            # 2. Entity Prefix & Alias Resolution (C-03, C-04)
            subj_clean = triple.subject.strip()
            obj_clean = triple.object.strip()

            # Alias matching against catalog
            resolved_subj = alias_map.get(subj_clean.lower(), subj_clean)
            resolved_obj = alias_map.get(obj_clean.lower(), obj_clean)

            norm_subj = normalize_prefix(resolved_subj, default_prefix="system:")
            norm_obj = normalize_prefix(resolved_obj, default_prefix="system:")

            # 3. Canonical Predicate Normalization (C-05)
            norm_pred = normalize_predicate(triple.predicate)

            # 4. Ambiguity Gap Gating (0.65 <= conf < 0.85)
            if 0.65 <= triple.confidence < 0.85:
                ambiguities.append({
                    "subject": norm_subj,
                    "predicate": norm_pred,
                    "object": norm_obj,
                    "confidence": triple.confidence,
                    "reason": "AMBIGUOUS_ENTITY_GAP: Confidence between 0.65 and 0.85 requires HITL lock.",
                    "statement_text": triple.statement_text,
                })

            # 5. Promoted Triple Envelope (C-07 Temporal Hygiene)
            promoted.append({
                "subject": norm_subj,
                "predicate": norm_pred,
                "object": norm_obj,
                "kind": kind,
                "statement_text": triple.statement_text or f"{norm_subj} {norm_pred} {norm_obj}",
                "observed_at": triple.observed_at or observed_at,
                "stale_after": triple.stale_after or stale_after,
                "confidence": triple.confidence,
                "citation": triple.citation,
            })

        return PTCResolvedProjection(
            promoted_triples=promoted,
            ambiguity_locks=ambiguities,
            rejected_triples=rejected,
            total_triples=len(plan.triples),
            promoted_count=len(promoted),
            ambiguity_count=len(ambiguities),
        )
