from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Locator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "section"
    value: str = "Document Body"
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    exact_text: Optional[str] = None
    artifact_sha256: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_locator(cls, values: Any) -> Any:
        if isinstance(values, str):
            return {"kind": "section", "value": values}
        if isinstance(values, dict):
            kind = values.get("kind") or values.get("type") or values.get("source_type") or "section"
            value = (
                values.get("value")
                or values.get("location")
                or values.get("locator")
                or values.get("text")
                or "Document Body"
            )
            out: Dict[str, Any] = {"kind": str(kind), "value": str(value)}
            for opt_k in ("start_offset", "end_offset", "exact_text", "artifact_sha256"):
                if opt_k in values:
                    out[opt_k] = values[opt_k]
            return out
        return {"kind": "section", "value": "Document Body"}


ClosedEvidenceKind = Literal["summary", "decision", "risk", "dependency", "technical", "strategic"]


def _resolve_evidence_speaker(values: Dict[str, Any]) -> Optional[str]:
    spk = (
        values.get("speaker")
        or values.get("speaker_name")
        or values.get("owner")
        or values.get("author")
    )
    return str(spk) if spk is not None else None


def _resolve_evidence_classification(values: Dict[str, Any]) -> Optional[str]:
    sc = (
        values.get("source_classification")
        or values.get("classification")
        or values.get("type")
    )
    return str(sc) if sc is not None else None


def _resolve_evidence_kind(values: Dict[str, Any]) -> str:
    valid_kinds = ("summary", "decision", "risk", "dependency", "technical", "strategic")
    raw_kind = values.get("kind")
    if raw_kind is not None:
        rk = str(raw_kind).lower().strip()
        if rk in valid_kinds:
            return rk
        if rk in ("quote", "direct_quote", "factual", "operational", "fact"):
            return "technical"
    sc = str(values.get("source_classification", "")).lower()
    return sc if sc in valid_kinds else "summary"


def _resolve_evidence_text(values: Dict[str, Any]) -> str:
    txt = values.get("text") or values.get("exact_text") or values.get("quote") or values.get("claim")
    if txt is None or not str(txt).strip():
        raise ValueError("EvidenceStatement requires non-empty text/quote/claim.")
    return str(txt)


def _resolve_evidence_locator(values: Dict[str, Any]) -> Any:
    loc = values.get("locator") or values.get("source_locator") or values.get("location")
    return loc if loc is not None else {"kind": "section", "value": "Document Body"}


class EvidenceStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    statement_id: Optional[str] = None
    evidence_id: Optional[str] = None
    speaker: Optional[str] = Field(default=None)
    speaker_role: Optional[str] = None
    speaker_org: Optional[str] = None
    source_classification: Optional[str] = Field(default=None)
    kind: ClosedEvidenceKind = Field(default="summary", description="Closed 6-kind taxonomy")
    text: str = Field(..., description="Verbatim or grounded evidence statement")
    exact_text: Optional[str] = None
    quote: Optional[str] = None
    direct_quote: Optional[str] = None
    claim: Optional[str] = None
    normalized_description: Optional[str] = None
    quote_pointer: Optional[List[int]] = Field(default=None, description="[start_idx, end_idx] character span in source transcript")
    char_span: Optional[List[int]] = None
    position_pointer: Optional[List[int]] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    locator: Locator = Field(default_factory=lambda: Locator(kind="section", value="Document Body"))
    source_locator: Optional[Any] = None
    source_locators: Optional[List[str]] = None
    evidence_locators: Optional[List[str]] = None
    grounding_status: Optional[str] = Field(default=None, description="Verbatim source grounding verification status")
    epistemic_status: Optional[str] = None
    confidence: Optional[Any] = None
    timestamp: Optional[str] = None
    source: Optional[str] = None
    source_type: Optional[str] = None
    attribution_mode: Optional[str] = None
    resolution_id: Optional[str] = None
    ledger_entry_id: Optional[str] = None
    turn_offset: Optional[int] = None
    artifact_sha256: Optional[str] = None
    content_sha256: Optional[str] = None
    hitl_resolved: Optional[bool] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @model_validator(mode="before")
    @classmethod
    def populate_fields(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        out = dict(values)
        for alias_key in ("speaker_name", "owner", "author", "classification", "type", "location", "offset_basis", "unit_locator", "page_num", "slide_number", "page", "slide", "section"):
            out.pop(alias_key, None)
        out["speaker"] = _resolve_evidence_speaker(values)
        out["source_classification"] = _resolve_evidence_classification(values)
        out["kind"] = _resolve_evidence_kind(values)
        out["text"] = _resolve_evidence_text(values)
        out["locator"] = _resolve_evidence_locator(values)
        return out


class EntityUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(..., description="Entity ID with domain prefix (e.g., org:, person:)")
    type: str
    name: str
    description: Optional[str] = Field(default=None, description="Substantive role or project context")
    mutation: str = Field(default="CREATE")
    evidence_ids: List[str] = Field(default_factory=list)
    assertion_type: Optional[str] = None
    extraction_confidence: Optional[float] = None
    evidence: Optional[Any] = None
    role: Optional[str] = None
    professional_title: Optional[str] = None
    org: Optional[str] = None
    organization: Optional[str] = None
    aliases: Optional[List[str]] = None
    attributes: Optional[Dict[str, Any]] = None
    confidence: Optional[Any] = None
    lineage: Optional[Any] = None
    is_human_confirmed: Optional[bool] = None
    provenance: Optional[str] = None
    resolution_id: Optional[str] = None
    status: Optional[str] = None
    epistemic_status: Optional[str] = None
    assertion_scope: Optional[str] = None
    attribution_mode: Optional[str] = None
    is_authenticated: Optional[bool] = None
    is_eligible_graph_fact: Optional[bool] = None
    prior_entity_id: Optional[str] = None
    canonical_name: Optional[str] = None
    node_id: Optional[str] = None
    entity_id: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    artifact_sha256: Optional[str] = None
    scope: Optional[str] = None
    rationale: Optional[str] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @field_validator("id")
    @classmethod
    def validate_entity_prefix(cls, v: str) -> str:
        allowed_prefixes = (
            "org:", "person:", "role:", "system:", "workstream:", "project:",
            "metric:", "policy:", "initiative:", "capability:", "pattern:",
            "decision:", "proposal:", "stakeholder:", "document:",
            "deliverable:", "milestone:", "forum:", "model:",
        )
        if not v.startswith(allowed_prefixes):
            raise ValueError(f"Entity ID must start with one of the allowed prefixes: {allowed_prefixes}")
        return v

    @model_validator(mode="after")
    def validate_person_description(self) -> EntityUpsert:
        if self.id.startswith("person:"):
            desc = (self.description or "").strip()
            generic_pats = [re.compile(r"^(?:review participant|meeting attendee|participant|attendee|contributor|team member)[\s.]*$", re.I)]
            if desc and any(pat.match(desc) for pat in generic_pats):
                raise ValueError(f"Generic placeholder description '{desc}' on person '{self.id}' violates Gate 3.11.")
        return self


class EdgeConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = Field(..., ge=0.0, le=1.0)
    tier: Optional[str] = None
    rationale: Optional[str] = None


class EdgeEvidenceStickyNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement_id: Optional[str] = None
    direct_quote: str
    locator: Dict[str, Any]
    turn_offset: Optional[int] = None
    all_evidence_ids: Optional[List[str]] = None
    evidence_ids: Optional[List[str]] = None


class EdgeLineage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: Optional[str] = None
    speaker: Optional[str] = None
    speaker_role: Optional[str] = None
    speaker_org: Optional[str] = None
    recorded_at: Optional[str] = None
    source_hash: Optional[str] = None


def _normalize_rel_predicate(values: dict[str, Any]) -> None:
    pred = values.get("predicate") or values.get("relationship") or "RELATED_TO"
    values["predicate"] = pred
    values["relationship"] = pred


def _normalize_rel_evidence_ids(values: dict[str, Any]) -> None:
    if values.get("evidence_ids"):
        return
    ev = values.get("evidence")
    if isinstance(ev, dict):
        stmt_id = ev.get("statement_id")
        values["evidence_ids"] = [stmt_id] if stmt_id else []
    elif isinstance(ev, str) and ev:
        values["evidence_ids"] = [ev]
    else:
        values["evidence_ids"] = []


def _normalize_rel_signal_id(values: dict[str, Any]) -> None:
    if not values.get("signal_id"):
        from_id = str(values.get("from_id", "src")).replace(":", "-")
        to_id = str(values.get("to_id", "dst")).replace(":", "-")
        pred = str(values.get("predicate") or values.get("relationship") or "RELATED_TO").upper()
        values["signal_id"] = f"rel-{from_id}--{to_id}--{pred}"


class RelationshipSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    signal_id: str
    id: Optional[str] = None
    from_id: str
    to_id: str
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    target_node_id: Optional[str] = None
    relationship: str = "RELATED_TO"
    predicate: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    assertion_type: Optional[str] = None
    extraction_confidence: Optional[float] = None
    confidence: Optional[Union[EdgeConfidence, float, Dict[str, Any]]] = None
    evidence: Optional[Union[EdgeEvidenceStickyNote, Dict[str, Any], str]] = None
    lineage: Optional[Union[EdgeLineage, Dict[str, Any], str]] = None
    source: Optional[str] = None
    is_human_confirmed: Optional[bool] = None
    provenance: Optional[str] = None
    status: Optional[str] = None
    epistemic_status: Optional[str] = None
    mutation: Optional[str] = None
    description: Optional[str] = None
    rationale: Optional[str] = None
    qualifiers: Optional[Dict[str, Any]] = None
    is_quarantined: Optional[bool] = None
    quarantine_reason: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    artifact_sha256: Optional[str] = None
    scope: Optional[str] = None
    assertion_scope: Optional[str] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @model_validator(mode="before")
    @classmethod
    def populate_relationship_and_evidence(cls, values: Any) -> Any:
        if isinstance(values, dict):
            out = dict(values)
            out.pop("auto_healed", None)
            _normalize_rel_predicate(out)
            _normalize_rel_evidence_ids(out)
            _normalize_rel_signal_id(out)
            return out
        return values


class DecisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    decision_id: str
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    status: str = "DECIDED"
    mutation: Optional[str] = None
    epistemic_status: Optional[str] = None
    assertion_type: Optional[str] = None
    decision: Optional[str] = None
    decision_statement: Optional[str] = None
    statement: Optional[str] = None
    claim: Optional[str] = None
    topic: Optional[str] = None
    choice: Optional[str] = None
    title: Optional[str] = None
    rationale: Optional[str] = None
    description: Optional[str] = None
    decision_question: Optional[str] = None
    decision_owner: Optional[str] = None
    decision_scope: Optional[str] = None
    scope: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    evidence: Optional[Any] = None
    owner_id: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    threatens_entity_id: Optional[str] = None
    extraction_confidence: Optional[float] = None
    artifact_sha256: Optional[str] = None
    lineage: Optional[Any] = None
    confidence: Optional[Any] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @model_validator(mode="before")
    @classmethod
    def populate_rationale(cls, values: Any) -> Any:
        if isinstance(values, dict):
            out = dict(values)
            if not out.get("decision_id") and out.get("id"):
                out["decision_id"] = out["id"]
            audit_rat = out.get("_audit_rationale")
            if not out.get("status"):
                out["status"] = out.get("mutation") or "DECIDED"
            if not out.get("rationale"):
                out["rationale"] = (
                    out.get("decision_question")
                    or out.get("description")
                    or out.get("decision")
                    or audit_rat
                )
            return out
        return values


class ProposalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    proposal_id: str
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    status: str = "PROPOSED"
    mutation: Optional[str] = None
    epistemic_status: Optional[str] = None
    assertion_type: Optional[str] = None
    proposal: Optional[str] = None
    proposal_text: Optional[str] = None
    statement: Optional[str] = None
    value_case: Optional[str] = None
    choice: Optional[str] = None
    title: Optional[str] = None
    rationale: Optional[str] = None
    description: Optional[str] = None
    proposal_owner: Optional[str] = None
    proposal_scope: Optional[str] = None
    scope: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    evidence: Optional[Any] = None
    owner_id: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    threatens_entity_id: Optional[str] = None
    extraction_confidence: Optional[float] = None
    artifact_sha256: Optional[str] = None
    lineage: Optional[Any] = None
    confidence: Optional[Any] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @model_validator(mode="before")
    @classmethod
    def populate_rationale(cls, values: Any) -> Any:
        if isinstance(values, dict):
            out = dict(values)
            if not out.get("proposal_id") and out.get("id"):
                out["proposal_id"] = out["id"]
            audit_rat = out.get("_audit_rationale")
            if not out.get("status"):
                out["status"] = out.get("mutation") or "PROPOSED"
            if not out.get("rationale"):
                out["rationale"] = out.get("description") or out.get("proposal") or audit_rat
            return out
        return values


class ActionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action_id: str
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    owner_id: Optional[str] = None
    owner: Optional[str] = None
    status: str = "OPEN"
    mutation: Optional[str] = None
    epistemic_status: Optional[str] = None
    assertion_type: Optional[str] = None
    action: Optional[str] = None
    title: Optional[str] = None
    due_date: Optional[str] = None
    target_timing: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    action_scope: Optional[str] = None
    scope: Optional[str] = None
    description: Optional[str] = None
    rationale: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    evidence: Optional[Any] = None
    extraction_confidence: Optional[float] = None
    artifact_sha256: Optional[str] = None
    lineage: Optional[Any] = None
    confidence: Optional[Any] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @field_validator("owner_id", mode="before")
    @classmethod
    def validate_owner_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        s = str(v).strip()
        if s.lower() in {"unassigned", "unassigned (pending owner assignment)", "tbd", "tbc", "unknown", "team", "all"}:
            raise ValueError(f"ActionUpdate owner_id '{s}' is not a resolved person or role.")
        return s

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_due_date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        if v in {"DATE NOT ESTABLISHED", "PENDING FORMAL AGREEMENT", "MILESTONE NOT RATIFIED"}:
            return v
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
        if m:
            v = f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", v):
            raise ValueError("Due date must be in MM/DD/YYYY format or an allowed unresolved sentinel if provided.")
        return v


class RiskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    risk_id: str
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    status: str = "OPEN"
    mutation: Optional[str] = None
    epistemic_status: Optional[str] = None
    assertion_type: Optional[str] = None
    risk_description: Optional[str] = None
    claim: Optional[str] = None
    topic: Optional[str] = None
    title: Optional[str] = None
    rationale: Optional[str] = None
    description: Optional[str] = None
    risk_scope: Optional[str] = None
    scope: Optional[str] = None
    evidence_ids: List[str] = Field(..., description="Explicit evidence IDs grounding this risk update")
    evidence: Optional[Any] = None
    owner_id: Optional[str] = None
    severity: Optional[str] = None
    impact: Optional[str] = None
    likelihood: Optional[str] = None
    probability: Optional[str] = None
    category: Optional[str] = None
    mitigation: Optional[str] = None
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    threatens_entity_id: Optional[str] = None
    extraction_confidence: Optional[float] = None
    artifact_sha256: Optional[str] = None
    lineage: Optional[Any] = None
    confidence: Optional[Any] = None
    audit_rationale: Optional[str] = Field(default=None, alias="_audit_rationale")

    @model_validator(mode="before")
    @classmethod
    def populate_rationale(cls, values: Any) -> Any:
        if isinstance(values, dict):
            out = dict(values)
            if not out.get("risk_id") and out.get("id"):
                out["risk_id"] = out["id"]
            audit_rat = out.get("_audit_rationale")
            if not out.get("status"):
                out["status"] = out.get("mutation") or "OPEN"
            if not out.get("rationale"):
                out["rationale"] = out.get("description") or out.get("risk_description") or audit_rat
            return out
        return values


class GraphMutations(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    entity_upserts: List[EntityUpsert] = Field(default_factory=list)
    relationship_signals: List[RelationshipSignal] = Field(default_factory=list)
    decision_updates: List[DecisionUpdate] = Field(default_factory=list)
    proposal_updates: List[ProposalUpdate] = Field(default_factory=list)
    action_updates: List[ActionUpdate] = Field(default_factory=list)
    risk_updates: List[RiskUpdate] = Field(default_factory=list)
    strategic_intent_signals: List[Dict[str, Any]] = Field(default_factory=list)
    proposals: Optional[List[Any]] = None
    risks: Optional[List[Any]] = None
    decisions: Optional[List[Any]] = None
    actions: Optional[List[Any]] = None
    entities: Optional[List[Any]] = None
    relationships: Optional[List[Any]] = None
    meeting_archetype: Optional[str] = None
    budget_triage_audit: Optional[Dict[str, Any]] = Field(default=None, alias="_budget_triage_audit")


class CanonicalSignalProjectionV57(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["5.7"] = Field("5.7")
    meeting_id: str
    meeting_date: Optional[str] = None
    date: Optional[str] = None
    project: Optional[str] = None
    source_file: Optional[str] = None
    classification: str = Field(..., description="Meeting classification (e.g., internal, public, client-facing)")
    audiences: List[str]
    evidence_index: Dict[str, EvidenceStatement]
    graph_mutations: GraphMutations
    evidence_only_statement_ids: List[str] = Field(default_factory=list)
    expected_base_graph_version: Optional[str] = None
    is_document: Optional[bool] = None
    output3_only: Optional[bool] = None
    canonical_modality: Optional[str] = None
    raw_transcript: Optional[str] = None
    meeting_archetype: Optional[str] = None
    meeting_metadata: Optional[Dict[str, Any]] = None
    entities: Optional[List[Dict[str, Any]]] = None
    relationships: Optional[List[Dict[str, Any]]] = None
    decisions: Optional[List[Dict[str, Any]]] = None
    risks: Optional[List[Dict[str, Any]]] = None
    proposals: Optional[List[Dict[str, Any]]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    decision_updates: Optional[List[Dict[str, Any]]] = None
    proposal_updates: Optional[List[Dict[str, Any]]] = None
    risk_updates: Optional[List[Dict[str, Any]]] = None
    action_updates: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    context_metadata: Optional[Dict[str, Any]] = None
    ambiguity_resolutions: Optional[List[Dict[str, Any]]] = None
    speaker_role_resolutions: Optional[Dict[str, Any]] = None
    incremental_reconciliation: Optional[Dict[str, Any]] = None
    strategic_analysis: Optional[Union[str, Dict[str, Any], List[Any]]] = Field(default=None, alias="_strategic_analysis")
    coverage_audit: Optional[Union[str, Dict[str, Any], List[Any]]] = Field(default=None, alias="_coverage_audit")
    threat_warnings: Optional[List[Any]] = Field(default=None, alias="_threat_warnings")


class ActionItemRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    action_deliverable: str
    owner: str
    target_timing: str
    source_locator: str

    @field_validator("action_deliverable")
    @classmethod
    def validate_active_verb(cls, v: str) -> str:
        _passive = re.compile(
            r"^\s*(Acknowledge|Note|Consider|Review|Monitor|Be aware|Discuss|"
            r"Explore|Observe|Reflect|Recognize|Understand|Appreciate|Keep in mind|TBC)",
            re.IGNORECASE,
        )
        if _passive.match(v):
            raise ValueError(
                f"Action deliverable must be active and concrete, not passive observation. "
                f"Got: '{v}'"
            )
        return v

    @field_validator("owner", "target_timing")
    @classmethod
    def validate_no_tbc(cls, v: str) -> str:
        if v == "TBC" or v.strip() == "":
            raise ValueError("Owner and target timing cannot be TBC or empty.")
        return v


class HeaderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    time: str
    attendees: Dict[str, List[str]]


class ExecutiveRecapModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    header: HeaderMetadata
    objective: str
    key_findings: List[str]
    action_items: List[ActionItemRow] = Field(default_factory=list)
    open_decisions: Optional[List[str]] = Field(default_factory=list)
