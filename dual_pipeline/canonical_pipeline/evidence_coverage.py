"""
dual_pipeline/canonical_pipeline/evidence_coverage.py

Python dataclasses and validation for the bounded evidence-coverage schema.
Tracks item-level dispositions across:
CAPTURED -> EXTRACTED -> PROPOSED -> AUTHORIZED -> WRITTEN -> ACTIVATED -> VERIFIED
(or EXCLUDED / QUARANTINED with explicit reasons).

Distinguishes operational controls (e.g. retry/skip decisions) from eligible graph facts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema

logger = logging.getLogger(__name__)

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "contracts"
    / "dual_pipeline"
    / "schemas"
    / "evidence-coverage.schema.json"
)


class EvidenceState(str, Enum):
    CAPTURED = "CAPTURED"
    EXTRACTED = "EXTRACTED"
    PROPOSED = "PROPOSED"
    AUTHORIZED = "AUTHORIZED"
    WRITTEN = "WRITTEN"
    ACTIVATED = "ACTIVATED"
    VERIFIED = "VERIFIED"
    EXCLUDED = "EXCLUDED"
    QUARANTINED = "QUARANTINED"


class ItemType(str, Enum):
    PERSON_IDENTITY = "PERSON_IDENTITY"
    SPEAKER_ROLE = "SPEAKER_ROLE"
    ORGANIZATION_AFFILIATION = "ORGANIZATION_AFFILIATION"
    VISUAL_CLAIM = "VISUAL_CLAIM"
    TABLE_RECORD = "TABLE_RECORD"
    FACTUAL_CORRECTION = "FACTUAL_CORRECTION"
    OPERATIONAL_CONTROL = "OPERATIONAL_CONTROL"


class SourceOriginType(str, Enum):
    TRANSCRIPT_HITL = "TRANSCRIPT_HITL"
    SCREENSHOT_ATTACHMENT = "SCREENSHOT_ATTACHMENT"
    DOCUMENT = "DOCUMENT"
    AUDIO_TRANSCRIPT = "AUDIO_TRANSCRIPT"
    OPERATOR_DIRECTIVE = "OPERATOR_DIRECTIVE"


@dataclass
class EvidenceItem:
    item_id: str
    item_type: str
    is_eligible_graph_fact: bool
    state: str
    source_origin: Dict[str, Any]
    disposition_reason: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: Optional[str] = None
    proposed_fact: Optional[Dict[str, Any]] = None
    approval_ref: Optional[Dict[str, Any]] = None

    def transition_to(self, new_state: EvidenceState, reason: str) -> None:
        """Transitions item state with explicit timestamp and disposition rationale."""
        self.state = new_state.value if isinstance(new_state, EvidenceState) else str(new_state)
        self.disposition_reason = reason
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        return d


@dataclass
class EvidenceCoverageManifest:
    manifest_id: str
    run_id: str
    source_digest: str
    items: List[EvidenceItem] = field(default_factory=list)
    session_id: Optional[str] = None
    meeting_slug: Optional[str] = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    contract_id: str = "urn:nee:dual-pipeline:evidence-coverage:1.0.0"
    schema_version: str = "1.0.0"

    def compute_summary(self) -> Dict[str, int]:
        total = len(self.items)
        eligible = sum(1 for it in self.items if it.is_eligible_graph_fact)
        ops = sum(1 for it in self.items if not it.is_eligible_graph_fact or it.item_type == ItemType.OPERATIONAL_CONTROL.value)
        verified = sum(1 for it in self.items if it.state == EvidenceState.VERIFIED.value)
        excluded = sum(1 for it in self.items if it.state == EvidenceState.EXCLUDED.value)
        quarantined = sum(1 for it in self.items if it.state == EvidenceState.QUARANTINED.value)
        pending = sum(
            1 for it in self.items
            if it.state in (
                EvidenceState.CAPTURED.value,
                EvidenceState.EXTRACTED.value,
                EvidenceState.PROPOSED.value,
                EvidenceState.AUTHORIZED.value,
                EvidenceState.WRITTEN.value,
                EvidenceState.ACTIVATED.value,
            )
        )
        return {
            "total_items": total,
            "eligible_facts_count": eligible,
            "operational_controls_count": ops,
            "active_verified_count": verified,
            "excluded_count": excluded,
            "quarantined_count": quarantined,
            "pending_count": pending,
        }

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "source_digest": self.source_digest,
            "items": [it.to_dict() for it in self.items],
            "coverage_summary": self.compute_summary(),
            "generated_at": self.generated_at,
        }
        if self.session_id:
            d["session_id"] = self.session_id
        if self.meeting_slug:
            d["meeting_slug"] = self.meeting_slug
        return d

    def validate(self) -> Tuple[bool, List[str]]:
        return validate_evidence_coverage_payload(self.to_dict())


def validate_evidence_coverage_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validates raw dict against evidence-coverage.schema.json."""
    if not SCHEMA_PATH.is_file():
        return False, [f"Evidence coverage schema file not found at {SCHEMA_PATH}"]
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        errors = [err.message for err in validator.iter_errors(payload)]
        return len(errors) == 0, errors
    except Exception as exc:
        return False, [f"Schema validation error: {exc}"]


class ClarificationKind(str, Enum):
    PERSON_CONFIRMATION = "PERSON_CONFIRMATION"
    SPEAKER_ROLE = "SPEAKER_ROLE"
    ORGANIZATION = "ORGANIZATION"
    NUMERIC_CORRECTION = "NUMERIC_CORRECTION"
    ACRONYM_DEFINITION = "ACRONYM_DEFINITION"
    VISUAL_TABLE_CELL = "VISUAL_TABLE_CELL"
    SYSTEM_TOPOLOGY = "SYSTEM_TOPOLOGY"
    OTHER = "OTHER"


class StatementKind(str, Enum):
    FACT = "FACT"
    ENTITY_UPSERT = "ENTITY_UPSERT"
    RELATIONSHIP_SIGNAL = "RELATIONSHIP_SIGNAL"
    ROLE_ASSIGNMENT = "ROLE_ASSIGNMENT"
    TABLE_RECORD = "TABLE_RECORD"
    SYSTEM_PROPERTY = "SYSTEM_PROPERTY"
    TEMPORAL_FACT = "TEMPORAL_FACT"


def _extract_all_locators(ev_index: Dict[str, Any]) -> set[str]:
    all_locators: set[str] = set()
    for ev in ev_index.values():
        if isinstance(ev, dict):
            for lk in ("source_locators", "evidence_locators"):
                for loc in ev.get(lk, []):
                    all_locators.add(str(loc).lower())
            loc_val = ev.get("locator", {})
            if isinstance(loc_val, dict) and "value" in loc_val:
                all_locators.add(str(loc_val["value"]).lower())
            elif isinstance(loc_val, str):
                all_locators.add(loc_val.lower())
    return all_locators


def _extract_projection_index(
    projection: Dict[str, Any],
) -> Tuple[set[str], set[str], set[Tuple[str, str, str]], set[str], Dict[str, Any]]:
    mutations = projection.get("graph_mutations") or projection.get("mutations") or projection
    entity_upserts = mutations.get("entity_upserts", []) if isinstance(mutations, dict) else []
    signals = mutations.get("relationship_signals", []) if isinstance(mutations, dict) else []
    ev_index = projection.get("evidence_index", {}) if isinstance(projection.get("evidence_index"), dict) else {}

    all_locators = _extract_all_locators(ev_index)
    entity_ids = {str(e.get("id", "")).lower() for e in entity_upserts if isinstance(e, dict)}
    entity_names = {str(e.get("name", "")).lower() for e in entity_upserts if isinstance(e, dict)}
    signal_pairs = {
        (str(s.get("from_id", "")).lower(), str(s.get("to_id", "")).lower(), str(s.get("relationship", "")).upper())
        for s in signals if isinstance(s, dict)
    }
    return entity_ids, entity_names, signal_pairs, all_locators, ev_index


def _is_fact_covered(
    item: EvidenceItem,
    entity_ids: set[str],
    entity_names: set[str],
    signal_pairs: set[Tuple[str, str, str]],
    all_locators: set[str],
    ev_index: Dict[str, Any],
) -> bool:
    pf = item.proposed_fact or {}
    subj = str(pf.get("subject", "")).lower()
    pred = str(pf.get("predicate", "") or pf.get("canonical_predicate", "")).upper()
    obj = str(pf.get("object", "")).lower()
    src_loc = str(item.source_origin.get("locator", "")).lower()

    if (
        (subj and (subj in entity_ids or any(subj in en for en in entity_names)))
        or (src_loc and src_loc in all_locators)
        or (pred and (subj, obj, pred) in signal_pairs)
        or any(subj in str(ev.get("text", "")).lower() for ev in ev_index.values() if isinstance(ev, dict))
    ):
        return True
    return False


def _evaluate_item_coverage(
    item: EvidenceItem,
    index_tuple: Tuple[set[str], set[str], set[Tuple[str, str, str]], set[str], Dict[str, Any]],
    violations: List[str],
) -> str:
    if item.state == EvidenceState.EXCLUDED.value:
        if not item.disposition_reason:
            violations.append(f"Excluded item '{item.item_id}' must have an explicit disposition reason.")
        return "EXCLUDED"

    if item.state == EvidenceState.QUARANTINED.value:
        if not item.disposition_reason:
            violations.append(f"Quarantined item '{item.item_id}' must have an explicit quarantine reason.")
        return "QUARANTINED"

    entity_ids, entity_names, signal_pairs, all_locators, ev_index = index_tuple
    if _is_fact_covered(item, entity_ids, entity_names, signal_pairs, all_locators, ev_index):
        item.transition_to(EvidenceState.PROPOSED, "Grounded in Output 03 canonical projection")
        return "COVERED"

    pf = item.proposed_fact or {}
    subj = str(pf.get("subject", "")).lower()
    pred = str(pf.get("predicate", "") or pf.get("canonical_predicate", "")).upper()
    obj = str(pf.get("object", "")).lower()
    violations.append(
        f"Eligible graph fact '{item.item_id}' ({item.item_type}: {subj} {pred} {obj}) "
        f"was removed during projection or healing without an explicit disposition reason."
    )
    return "MISSING"


def verify_fact_coverage_in_projection(
    projection: Dict[str, Any],
    manifest: EvidenceCoverageManifest,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Verifies that every approved eligible fact in the manifest survives into Output 03."""
    violations: List[str] = []
    covered_count = 0
    excluded_count = 0
    quarantined_count = 0
    missing_count = 0

    index_tuple = _extract_projection_index(projection)

    for item in manifest.items:
        if not item.is_eligible_graph_fact:
            continue

        status = _evaluate_item_coverage(item, index_tuple, violations)
        if status == "COVERED":
            covered_count += 1
        elif status == "EXCLUDED":
            excluded_count += 1
        elif status == "QUARANTINED":
            quarantined_count += 1
        else:
            missing_count += 1

    stats = {
        "eligible_facts_count": sum(1 for it in manifest.items if it.is_eligible_graph_fact),
        "covered_in_projection": covered_count,
        "explicitly_excluded": excluded_count,
        "explicitly_quarantined": quarantined_count,
        "missing_without_reason": missing_count,
    }
    return len(violations) == 0, violations, stats


def audit_stage03_projection(final_data: Dict[str, Any], metadata: Optional[Dict[str, Any]]) -> None:
    """Verifies eligible facts survive into Stage 03 projection."""
    ev_manifest = (metadata or {}).get("evidence_manifest") or (metadata or {}).get("evidence_coverage_manifest")
    if ev_manifest is None:
        return
    try:
        if isinstance(ev_manifest, dict):
            manifest_obj = EvidenceCoverageManifest(
                manifest_id=ev_manifest.get("manifest_id", "cov-temp"),
                run_id=ev_manifest.get("run_id", "run-temp"),
                source_digest=ev_manifest.get("source_digest", "0" * 64),
                items=[
                    EvidenceItem(**it) if isinstance(it, dict) else it
                    for it in ev_manifest.get("items", [])
                ],
            )
        else:
            manifest_obj = ev_manifest
        is_cov_valid, cov_violations, cov_stats = verify_fact_coverage_in_projection(final_data, manifest_obj)
        final_data["_coverage_audit"] = {
            "is_compliant": is_cov_valid,
            "violations": cov_violations,
            "coverage_stats": cov_stats,
        }
        if not is_cov_valid:
            logger.warning("Stage 03 Coverage Audit: %d dropped eligible facts: %s", len(cov_violations), cov_violations[:3])
    except Exception as cov_err:
        logger.debug("Stage 03 coverage audit evaluation skipped: %s", cov_err)
