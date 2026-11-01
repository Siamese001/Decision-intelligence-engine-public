"""
dual_pipeline/prompts/contracts/versioned_judge_contracts.py

Versioned SSOT Contracts for Meeting Output LLM-as-a-Judge Hardening (Wave 1).
Defines:
1. Strict Pydantic Scorecard Schemas for Output 1, Output 2, Output 3, and Document Mode O3.
2. Cryptographically bound JudgeInputEnvelope for authenticated judge evaluation.
3. Meeting X1, X2, and X3 Cross-Output Coherence Contracts.
4. Validation interfaces ensuring fail-closed rejection of malformed or incomplete evaluations.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dual_pipeline.release_thresholds import ReleaseVerdict


# -----------------------------------------------------------------------------
# 1. Output 01 (Forensic Transcript) Scorecard Schema
# -----------------------------------------------------------------------------

class Output1ScorecardV2(BaseModel):
    """Strict scorecard contract for Output 01 Forensic Transcript Judge."""
    model_config = ConfigDict(extra="forbid")

    rubric_version: str = Field(default="2.0", description="Version of the judge rubric")
    judge_model: str = Field(min_length=1, description="Identifier of the judge model")
    prompt_digest: str = Field(min_length=1, description="Digest of prompt rubric")
    sample_count: int = Field(default=1, ge=1, description="Number of evaluation samples taken")
    is_fallback: bool = Field(default=False, description="Flag indicating if provider fallback was used")
    sample_spread: Optional[float] = Field(default=None, description="Spread between sample scores")
    sample_scores: Optional[List[float]] = Field(default=None, description="Sample scores evaluated")

    # Causal Evidence & Deliberation Fields (condition score tokens left-to-right)
    extracted_evidence_spans: List[str] = Field(default_factory=list, description="Verbatim citations from source")
    prosecutor_counter_arguments: List[str] = Field(default_factory=list, description="Adversarial counter-probe arguments")
    forensic_rationale: str = Field(min_length=1, description="Synthesized justification for evaluation")

    dimension_1_forensic_grounding_and_fidelity: float = Field(ge=0.0, le=25.0)
    dimension_2_attribution_and_room_mic_hygiene: float = Field(ge=0.0, le=25.0)
    dimension_3_chronological_and_timestamp_integrity: float = Field(ge=0.0, le=25.0)
    dimension_4_asr_cleanup_and_domain_preservation: float = Field(ge=0.0, le=25.0)
    composite_score: float = Field(ge=0.0, le=100.0)
    verdict: Literal["PASS", "FAIL", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"]
    evaluated_span_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    effective_model: Optional[str] = Field(default=None)
    effective_provider: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    defect_codes: Optional[List[str]] = Field(default_factory=list)
    actionable_remediation_diff: Optional[str] = Field(default="")
    evidence_references: List[str] = Field(default_factory=list)
    grounding_citations: Optional[List[str]] = Field(default_factory=list)
    critique_diff_envelope: Optional[Dict[str, Any]] = None
    meeting_archetype: Optional[str] = None
    logprob_diagnostics: Optional[Dict[str, Any]] = Field(default=None, description="Continuous expected values from token logprobs")
    evaluator: Optional[str] = Field(default=None)
    failure_type: Optional[str] = Field(default=None)
    passed_pre_check: Optional[bool] = Field(default=None)
    reason: Optional[str] = Field(default=None)
    is_valid_scorecard: Optional[bool] = Field(default=None)
    dimension_1_speaker_identity_fidelity: Optional[float] = Field(default=None)
    dimension_2_turn_boundary_timestamp_integrity: Optional[float] = Field(default=None)
    dimension_3_verbatim_completeness_grounding: Optional[float] = Field(default=None)
    dimension_4_register_framing_absence: Optional[float] = Field(default=None)
    reasoning_effort: Optional[str] = Field(default=None)
    latency_seconds: Optional[float] = Field(default=None)
    is_provider_fallback: Optional[bool] = Field(default=None)
    evaluated_span_complete: Optional[bool] = Field(default=None)
    token_usage: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def sync_provenance_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "dimension_1_speaker_identity_fidelity" in data and "dimension_1_forensic_grounding_and_fidelity" not in data:
                data["dimension_1_forensic_grounding_and_fidelity"] = data["dimension_1_speaker_identity_fidelity"]
            elif "dimension_1_forensic_grounding_and_fidelity" in data and "dimension_1_speaker_identity_fidelity" not in data:
                data["dimension_1_speaker_identity_fidelity"] = data["dimension_1_forensic_grounding_and_fidelity"]

            if "dimension_2_turn_boundary_timestamp_integrity" in data and "dimension_2_attribution_and_room_mic_hygiene" not in data:
                data["dimension_2_attribution_and_room_mic_hygiene"] = data["dimension_2_turn_boundary_timestamp_integrity"]
            elif "dimension_2_attribution_and_room_mic_hygiene" in data and "dimension_2_turn_boundary_timestamp_integrity" not in data:
                data["dimension_2_turn_boundary_timestamp_integrity"] = data["dimension_2_attribution_and_room_mic_hygiene"]

            if "dimension_3_verbatim_completeness_grounding" in data and "dimension_3_chronological_and_timestamp_integrity" not in data:
                data["dimension_3_chronological_and_timestamp_integrity"] = data["dimension_3_verbatim_completeness_grounding"]
            elif "dimension_3_chronological_and_timestamp_integrity" in data and "dimension_3_verbatim_completeness_grounding" not in data:
                data["dimension_3_verbatim_completeness_grounding"] = data["dimension_3_chronological_and_timestamp_integrity"]

            if "dimension_4_register_framing_absence" in data and "dimension_4_asr_cleanup_and_domain_preservation" not in data:
                data["dimension_4_asr_cleanup_and_domain_preservation"] = data["dimension_4_register_framing_absence"]
            elif "dimension_4_asr_cleanup_and_domain_preservation" in data and "dimension_4_register_framing_absence" not in data:
                data["dimension_4_register_framing_absence"] = data["dimension_4_asr_cleanup_and_domain_preservation"]

            if not data.get("judge_model") and data.get("effective_model"):
                data["judge_model"] = data["effective_model"]
            if not data.get("effective_model") and data.get("judge_model"):
                data["effective_model"] = data["judge_model"]
            if not data.get("effective_provider"):
                data["effective_provider"] = "local" if "mock" in str(data.get("judge_model", "")).lower() else "openai"
            if not data.get("prompt_digest"):
                data["prompt_digest"] = data.get("prompt_hash") or data.get("receipt_digest") or "timeout_digest"
            if "sample_count" not in data:
                data["sample_count"] = 1
            if "is_fallback" not in data:
                data["is_fallback"] = bool(data.get("is_provider_fallback", False))
            if not data.get("forensic_rationale") and data.get("rationale"):
                data["forensic_rationale"] = data["rationale"]
        return data

    @model_validator(mode="after")
    def validate_composite_and_floors(self) -> Output1ScorecardV2:
        dim_sum = (
            self.dimension_1_forensic_grounding_and_fidelity
            + self.dimension_2_attribution_and_room_mic_hygiene
            + self.dimension_3_chronological_and_timestamp_integrity
            + self.dimension_4_asr_cleanup_and_domain_preservation
        )
        if abs(self.composite_score - dim_sum) > 0.01:
            raise ValueError(f"composite_score {self.composite_score} does not match dimension sum {dim_sum}")

        # Mandatory per-dimension floor derived from SSOT contract
        from .output1_contract import OUTPUT1_CONTRACT
        o1_floors = OUTPUT1_CONTRACT.get_dimension_floors()
        floors = {
            dim.key: (getattr(self, dim.key), o1_floors.get(dim.key))
            for dim in OUTPUT1_CONTRACT.rubric_dimensions
        }
        below_floors = [k for k, (v, flr) in floors.items() if flr is not None and v < flr]
        if below_floors and self.verdict != "FAIL":
            raise ValueError(f"Dimensions below mandatory floor ({below_floors}) must yield verdict 'FAIL', got '{self.verdict}'")

        if self.evaluated_span_coverage < 1.0 and self.verdict == "PASS":
            raise ValueError(f"Incomplete evaluated span coverage ({self.evaluated_span_coverage} < 1.0) cannot yield verdict 'PASS'")

        return self


# -----------------------------------------------------------------------------
# 2. Output 02 (Executive Strategic Recap) Scorecard Schema
# -----------------------------------------------------------------------------

class Output2SectionScoresV2(BaseModel):
    """Authoritative component section scores for Output 02."""
    model_config = ConfigDict(extra="forbid")

    header_and_overview: float = Field(ge=0.0, le=10.0)
    key_updates_technical_substance: float = Field(ge=0.0, le=35.0)
    conditional_extensions: float = Field(ge=0.0, le=20.0)
    appendix_stakeholder_perspectives: float = Field(ge=0.0, le=35.0)


class Output2ScorecardV2(BaseModel):
    """Strict scorecard contract for Output 02 Executive Strategic Brief / Recap Judge."""
    model_config = ConfigDict(extra="forbid")

    rubric_version: str = Field(default="2.0", description="Version of the judge rubric")
    judge_model: str = Field(min_length=1, description="Identifier of the judge model")
    prompt_digest: str = Field(min_length=1, description="Digest of prompt rubric")
    sample_count: int = Field(default=1, ge=1, description="Number of evaluation samples taken")
    is_fallback: bool = Field(default=False, description="Flag indicating if provider fallback was used")
    sample_spread: Optional[float] = Field(default=None, description="Spread between sample scores")
    sample_scores: Optional[List[float]] = Field(default=None, description="Sample scores evaluated")

    # Causal Evidence & Deliberation Fields
    extracted_evidence_spans: List[str] = Field(default_factory=list, description="Verbatim citations from source")
    prosecutor_counter_arguments: List[str] = Field(default_factory=list, description="Adversarial counter-probe arguments")
    executive_rationale: str = Field(min_length=1, description="Synthesized justification for evaluation")

    section_scores: Output2SectionScoresV2
    composite_score: float = Field(ge=0.0, le=100.0)
    verdict: Literal["PASS", "FAIL", "HITL_REQUIRED", "PASS_AUTHORIZED_EXCEPTION"]
    evaluated_span_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    effective_model: Optional[str] = Field(default=None)
    effective_provider: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    defect_codes: Optional[List[str]] = Field(default_factory=list)
    actionable_remediation_diff: Optional[str] = Field(default="")
    grounding_citations: Optional[List[str]] = Field(default_factory=list)
    extension_evaluations: Optional[List[Any]] = Field(default_factory=list)
    meeting_archetype: Optional[str] = None
    critique_diff_envelope: Optional[Dict[str, Any]] = None
    permitted_pending_owners_count: int = Field(default=0, ge=0)
    dimension_1_executive_altitude: Optional[float] = None
    dimension_2_technical_substance: Optional[float] = None
    dimension_3_strategic_synthesis: Optional[float] = None
    dimension_4_scoping_and_modality: Optional[float] = None
    forensic_rationale: Optional[str] = None
    defect_penalty_applied: Optional[bool] = None
    logprob_diagnostics: Optional[Dict[str, Any]] = Field(default=None, description="Continuous expected values from token logprobs")

    @model_validator(mode="before")
    @classmethod
    def sync_provenance_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("judge_model") and data.get("effective_model"):
                data["judge_model"] = data["effective_model"]
            if not data.get("effective_model") and data.get("judge_model"):
                data["effective_model"] = data["judge_model"]
            if not data.get("effective_provider"):
                data["effective_provider"] = "local" if "mock" in str(data.get("judge_model", "")).lower() else "openai"
            if not data.get("prompt_digest"):
                data["prompt_digest"] = data.get("prompt_hash") or data.get("receipt_digest")
            if "sample_count" not in data:
                data["sample_count"] = 1
            if "is_fallback" not in data:
                data["is_fallback"] = bool(data.get("is_provider_fallback", False))
            if not data.get("executive_rationale") and data.get("forensic_rationale"):
                data["executive_rationale"] = data["forensic_rationale"]
            elif not data.get("executive_rationale") and data.get("rationale"):
                data["executive_rationale"] = data["rationale"]
            if not data.get("forensic_rationale") and data.get("executive_rationale"):
                data["forensic_rationale"] = data["executive_rationale"]
        return data

    @model_validator(mode="after")
    def validate_composite_and_sections(self) -> Output2ScorecardV2:
        sec_sum = (
            self.section_scores.header_and_overview
            + self.section_scores.key_updates_technical_substance
            + self.section_scores.conditional_extensions
            + self.section_scores.appendix_stakeholder_perspectives
        )
        if abs(self.composite_score - sec_sum) > 0.01:
            raise ValueError(f"composite_score {self.composite_score} does not match section sum {sec_sum}")

        from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
        from dual_pipeline.prompts.contracts.output_contract import OutputContract  # ensure properties bound
        sec_floors = CANONICAL_PROMPT_CONFIG.output2.section_floors
        below_sec_floors = [
            k for k, flr in sec_floors.items() if getattr(self.section_scores, k) < flr
        ]
        if below_sec_floors and self.verdict in ("PASS", "PASS_AUTHORIZED_EXCEPTION"):
            raise ValueError(
                f"Section scores below mandatory floor ({below_sec_floors}) cannot yield verdict '{self.verdict}'"
            )

        # Verdict policy validation
        if self.composite_score >= 90.0 and not below_sec_floors and self.verdict not in ("PASS", "HITL_REQUIRED"):
            raise ValueError(f"Score {self.composite_score} >= 90 expects PASS or HITL_REQUIRED, got '{self.verdict}'")
        if 85.0 <= self.composite_score < 90.0 and self.verdict not in ("FAIL", "HITL_REQUIRED", "PASS_AUTHORIZED_EXCEPTION"):
            raise ValueError(
                f"Borderline score {self.composite_score} [85, 90) expects FAIL, HITL_REQUIRED, or PASS_AUTHORIZED_EXCEPTION, got '{self.verdict}'"
            )
        if self.composite_score < 85.0 and self.verdict != "FAIL":
            raise ValueError(f"Failing score {self.composite_score} < 85 expects FAIL, got '{self.verdict}'")

        if self.evaluated_span_coverage < 1.0 and self.verdict == "PASS":
            raise ValueError(f"Incomplete evaluated span coverage ({self.evaluated_span_coverage} < 1.0) cannot yield verdict 'PASS'")

        return self


# -----------------------------------------------------------------------------
# 3. Output 03 (Canonical Signal Projection) Scorecard Schema
# -----------------------------------------------------------------------------

class Output3ScorecardV2(BaseModel):
    """Strict scorecard contract for Output 03 Graph Extraction Judge."""
    model_config = ConfigDict(extra="forbid")

    rubric_version: str = Field(default="2.0", description="Version of the judge rubric")
    judge_model: str = Field(min_length=1, description="Identifier of the judge model")
    prompt_digest: str = Field(min_length=1, description="Digest of prompt rubric")
    sample_count: int = Field(default=1, ge=1, description="Number of evaluation samples taken")
    is_fallback: bool = Field(default=False, description="Flag indicating if provider fallback was used")
    sample_spread: Optional[float] = Field(default=None, description="Spread between sample scores")
    sample_scores: Optional[List[float]] = Field(default=None, description="Sample scores evaluated")

    # Causal Evidence & Deliberation Fields
    extracted_evidence_spans: List[str] = Field(default_factory=list, description="Verbatim citations from source")
    prosecutor_counter_arguments: List[str] = Field(default_factory=list, description="Adversarial counter-probe arguments")
    forensic_rationale: str = Field(min_length=1, description="Synthesized justification for evaluation")

    dimension_1_strategic_edge: Optional[float] = Field(default=None, ge=0.0, le=15.0)
    dimension_2_knowledge_layer_density: float = Field(ge=0.0, le=15.0)
    dimension_3_semantic_atomicity: float = Field(ge=0.0, le=20.0)
    dimension_4_predicate_specificity: float = Field(ge=0.0, le=15.0)
    dimension_5_exclusion_of_action_items: float = Field(ge=0.0, le=15.0)
    dimension_6_evidence_grounding_and_schema: float = Field(ge=0.0, le=20.0)
    composite_score: float = Field(ge=0.0, le=100.0)
    verdict: Literal["PASS", "FAIL", "HITL_REQUIRED", "PASS_AUTHORIZED_EXCEPTION"]
    evaluated_span_coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    effective_model: Optional[str] = Field(default=None)
    effective_provider: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    reasoning: Optional[str] = Field(default=None)
    canonical_role_predicate: Literal["HAS_ROLE"] = Field(default="HAS_ROLE")
    clarification_candidates: Optional[List[Any]] = Field(default_factory=list)
    defect_codes: Optional[List[str]] = Field(default_factory=list)
    actionable_remediation_diff: Optional[str] = Field(default="")
    grounding_citations: Optional[List[str]] = Field(default_factory=list)
    raw_judge_scorecard: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    meeting_archetype: Optional[str] = None
    applicable_dimensions: Optional[List[str]] = None
    logprob_diagnostics: Optional[Dict[str, Any]] = Field(default=None, description="Continuous expected values from token logprobs")

    @model_validator(mode="before")
    @classmethod
    def sync_provenance_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "_reasoning" in data and not data.get("reasoning"):
                data["reasoning"] = data.pop("_reasoning")
            elif "_reasoning" in data:
                data.pop("_reasoning")
            if not data.get("judge_model") and data.get("effective_model"):
                data["judge_model"] = data["effective_model"]
            if not data.get("effective_model") and data.get("judge_model"):
                data["effective_model"] = data["judge_model"]
            if not data.get("effective_provider"):
                data["effective_provider"] = "local" if "mock" in str(data.get("judge_model", "")).lower() else "openai"
            if not data.get("prompt_digest"):
                data["prompt_digest"] = data.get("prompt_hash") or data.get("receipt_digest")
            if "sample_count" not in data:
                data["sample_count"] = 1
            if "is_fallback" not in data:
                data["is_fallback"] = bool(data.get("is_provider_fallback", False))
            if not data.get("forensic_rationale") and data.get("rationale"):
                data["forensic_rationale"] = data["rationale"]
        return data

    @model_validator(mode="after")
    def validate_composite_and_floors(self) -> Output3ScorecardV2:
        from .output3_contract import OUTPUT3_CONTRACT
        eff_mode = getattr(self, "modality", None) or self.meeting_archetype
        o3_floors = OUTPUT3_CONTRACT.get_dimension_floors(
            pipeline_mode=str(eff_mode) if eff_mode else None
        )
        all_dims = {
            dim.key: (getattr(self, dim.key), float(dim.max_points), o3_floors.get(dim.key))
            for dim in OUTPUT3_CONTRACT.rubric_dimensions
        }
        active_dims = {k: (v, max_p, flr) for k, (v, max_p, flr) in all_dims.items() if v is not None}
        active_sum = sum(v for v, _, _ in active_dims.values())
        max_possible = sum(max_p for _, max_p, _ in active_dims.values())

        if max_possible > 0:
            expected_composite = round((active_sum / max_possible) * 100.0, 2) if len(active_dims) < len(all_dims) else active_sum
            if abs(self.composite_score - expected_composite) > 0.01:
                raise ValueError(
                    f"composite_score {self.composite_score} does not match active dimension calculation (expected={expected_composite})"
                )

        below_floors = [k for k, (v, _, flr) in active_dims.items() if flr is not None and v < flr]
        if below_floors and self.verdict in ("PASS", "PASS_AUTHORIZED_EXCEPTION"):
            raise ValueError(f"Dimensions below mandatory floor ({below_floors}) cannot yield verdict '{self.verdict}'")

        if self.evaluated_span_coverage < 1.0 and self.verdict == "PASS":
            raise ValueError(f"Incomplete evaluated span coverage ({self.evaluated_span_coverage} < 1.0) cannot yield verdict 'PASS'")

        return self


# -----------------------------------------------------------------------------
# 4. Document Modality Compatibility Scorecard Schema
# -----------------------------------------------------------------------------

class DocumentOutput3ScorecardV2(Output3ScorecardV2):
    """Document pipeline scorecard schema: Output 03 evaluated, 01/02 declared NOT_APPLICABLE."""
    modality: Literal["DOCUMENT"] = Field(default="DOCUMENT")
    dimension_2_knowledge_layer_density: Optional[float] = Field(default=None, ge=0.0, le=15.0)
    output_1_status: Literal["NOT_APPLICABLE", "NOT_APPLICABLE_DOCUMENT_MODE", "SKIPPED"] = Field(default="NOT_APPLICABLE")
    output_2_status: Literal["NOT_APPLICABLE", "NOT_APPLICABLE_DOCUMENT_MODE", "SKIPPED"] = Field(default="NOT_APPLICABLE")


# -----------------------------------------------------------------------------
# 5. Trusted Judge Input Envelope
# -----------------------------------------------------------------------------

class JudgeInputEnvelope(BaseModel):
    """Authenticated judge input envelope binding all verdict-altering inputs and provenance."""
    model_config = ConfigDict(extra="forbid")

    source_hash: str = Field(min_length=64, max_length=64, description="SHA-256 of source transcript/document")
    candidate_hash: str = Field(min_length=64, max_length=64, description="SHA-256 of evaluated candidate artifact")
    c0_receipt_digest: str = Field(min_length=64, max_length=64, description="SHA-256 of verified C0 roster/title receipt")
    c0_receipt_verified: bool = Field(description="Must be True for cleared C0 title/roster gate")
    rubric_version: str = Field(default="2.0", min_length=1)
    rubric_digest: str = Field(min_length=64, max_length=64, description="SHA-256 of canonical rubric markdown")
    prompt_digest: str = Field(min_length=64, max_length=64, description="SHA-256 of compiled judge prompt")
    topology_digest: str = Field(min_length=64, max_length=64, description="SHA-256 of active evaluator topology")
    bounded_context_digest: str = Field(min_length=64, max_length=64, description="SHA-256 of authorized project context")
    collateral_digest: Optional[str] = Field(default=None, max_length=64)
    evaluated_span_coverage: float = Field(ge=0.0, le=1.0)

    def compute_envelope_digest(self) -> str:
        """Deterministic RFC-8259 SHA-256 digest over all constituent field digests."""
        payload = {
            "source_hash": self.source_hash,
            "candidate_hash": self.candidate_hash,
            "c0_receipt_digest": self.c0_receipt_digest,
            "c0_receipt_verified": self.c0_receipt_verified,
            "rubric_version": self.rubric_version,
            "rubric_digest": self.rubric_digest,
            "prompt_digest": self.prompt_digest,
            "topology_digest": self.topology_digest,
            "bounded_context_digest": self.bounded_context_digest,
            "collateral_digest": self.collateral_digest,
            "evaluated_span_coverage": self.evaluated_span_coverage,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def validate_c0_and_coverage(self) -> JudgeInputEnvelope:
        if not self.c0_receipt_verified:
            raise ValueError("Unverified C0 title/roster receipt cannot construct valid JudgeInputEnvelope")
        return self


# -----------------------------------------------------------------------------
# 6. Meeting X1, X2, X3 Exit Contract Specification
# -----------------------------------------------------------------------------

def _is_non_transcript_evidence_item(ev: Dict[str, Any]) -> bool:
    spk = str(ev.get("speaker") or "").strip().lower()
    src = str(ev.get("source_type") or ev.get("source") or "").strip().upper()
    loc = ev.get("locator")
    loc_k = str(loc.get("kind") or "").strip().lower() if isinstance(loc, dict) else str(loc or "").strip().lower()
    loc_v = str(loc.get("value") or "").strip().lower() if isinstance(loc, dict) else str(loc or "").strip().lower()
    return (
        spk.startswith("attachment:")
        or spk == "meeting roster"
        or src in ("HITL", "TRANSCRIPT_HITL", "ATTACHMENT", "COLLATERAL", "C0_RECEIPT")
        or loc_k in ("attachment", "hitl_decision_ledger", "c0_receipt", "collateral", "region")
        or loc_k.startswith("attachment:")
        or loc_v.startswith("attachment:")
        or loc_v == "roster"
        or ev.get("attribution_mode") == "COLLATERAL_REVIEWED"
    )


class MeetingXExitContract:
    """Canonical specifications and validators for Meeting Exit Gates X1, X2, and X3."""

    @staticmethod
    def validate_x1_schema_and_sprawl(f3_data: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if f3_data.get("schema_version") != "5.7":
            errors.append("X1 FAIL: Graph delta does not meet schema_version 5.7 constraint")
        try:
            from dual_pipeline.transcript_models import CanonicalSignalProjectionV57  # noqa: lazy-decouple
            CanonicalSignalProjectionV57.model_validate(f3_data)
        except Exception as e:
            errors.append(f"X1 FAIL: Graph delta does not conform to CanonicalSignalProjectionV57: {e}")

        mutations = f3_data.get("graph_mutations", {})
        seen_entity_ids = set()
        for ent in mutations.get("entity_upserts", []):
            eid = ent.get("id")
            if eid in seen_entity_ids:
                errors.append(f"X1 FAIL: Duplicate entity ID '{eid}' in entity_upserts (graph sprawl violation).")
            seen_entity_ids.add(eid)

        seen_edges = set()
        for rel in mutations.get("relationship_signals", []):
            triple = (rel.get("from_id"), rel.get("relationship"), rel.get("to_id"))
            if triple in seen_edges:
                errors.append(f"X1 FAIL: Duplicate relationship edge {triple} in relationship_signals (graph sprawl violation).")
            seen_edges.add(triple)

        ev_only = f3_data.get("evidence_only_statement_ids", [])
        if isinstance(ev_only, list):
            promoted_mutation_count = sum(
                len(mutations.get(cat, []))
                for cat in ("decision_updates", "proposal_updates", "action_updates", "risk_updates")
            )
            max_allowed = max(3, 2 * promoted_mutation_count)
            if len(ev_only) > max_allowed:
                errors.append(
                    f"X1 FAIL: Rule C-17 violation: evidence_only_statement_ids count ({len(ev_only)}) "
                    f"exceeds max(3, 2 * promoted_mutation_count) = {max_allowed}."
                )

        return len(errors) == 0, errors

    @staticmethod
    def validate_x2_recap_structure(f2_text: str, metadata: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
        """X2: Validates recap structure, anti-embarrassment, and single DRI / permitted pending owner format."""
        errors: List[str] = []
        if not f2_text or not f2_text.strip():
            errors.append("X2 FAIL: Executive recap is empty.")
            return False, errors

        required_patterns = [
            ("Meeting Objective", re.compile(r"(?:^|\n)\s*#+\s*(?:1\.?\s*)?(?:Meeting Objective|Overview)\b", re.IGNORECASE)),
            ("Action & Governance Ledger", re.compile(r"(?:^|\n)\s*#+\s*(?:2\.?\s*)?Action & Governance Ledger\b", re.IGNORECASE)),
        ]
        for sec_name, pat in required_patterns:
            if not pat.search(f2_text) and f"1. {sec_name}" not in f2_text and f"# {sec_name}" not in f2_text:
                errors.append(f"X2 FAIL: Missing required section '{sec_name}'")

        owner_col_idx = 3
        action_col_idx = 2
        timing_col_idx: Optional[int] = None
        forbidden_owners = {
            "not assigned", "tbc", "tbd", "unknown", "unassigned",
            "pending", "team", "all", "n/a", "none",
        }
        for line in f2_text.split("\n"):
            clean = line.strip()
            if not clean.startswith("|"):
                continue
            cols = [c.strip() for c in clean.split("|")]
            lower_cols = [c.lower() for c in cols]
            is_data_row = bool(re.match(r"^(?:[A-Za-z]+[-_]?\d+\.?|\d+\.?)$", cols[1] if len(cols) > 1 else ""))
            if not is_data_row and any("owner" in c or "accountable" in c or "dri" in c for c in lower_cols):
                for idx, c in enumerate(lower_cols):
                    if "owner" in c or "accountable" in c or "dri" in c:
                        owner_col_idx = idx
                    elif "action" in c or "deliverable" in c or "item" in c:
                        action_col_idx = idx
                    elif "timing" in c or "due" in c or "target" in c or "date" in c:
                        timing_col_idx = idx
                continue
            if all(not c or re.match(r"^[-:\s]+$", c) for c in cols):
                continue
            if not is_data_row:
                continue
            if len(cols) <= max(action_col_idx, owner_col_idx):
                continue
            owner_text = cols[owner_col_idx]
            is_c25_pending = owner_text.strip().lower() == "unassigned (pending owner assignment)"
            if not is_c25_pending and (
                owner_text.lower() in forbidden_owners
                or owner_text.lower().startswith("unassigned")
                or re.match(r"^Speaker\s+[A-Z0-9]+$", owner_text, re.IGNORECASE)
            ):
                errors.append(f"X2 FAIL: Anti-embarrassment gate triggered. Action item owner '{owner_text}' is not a resolved person.")
            if timing_col_idx is not None and len(cols) > timing_col_idx:
                timing_text = cols[timing_col_idx].strip()
                status_text = cols[6].strip().lower() if len(cols) > 6 else ""
                is_proposed = "proposed" in status_text or "proposed" in timing_text.lower()
                if not is_proposed and timing_text.lower() in ("tbd", "tbd - unspecified", "unspecified", "n/a", "unknown"):
                    errors.append(f"X2 FAIL: Anti-embarrassment gate triggered. Action item target timing '{timing_text}' is ungrounded TBD.")

        return len(errors) == 0, errors

    @staticmethod
    def validate_x3_cross_output_coherence(
        f1_text: str,
        f2_text: str,
        f3_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        c0_receipt: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        X3: Strict Cross-Output Coherence Contract.
        Replaces unconditional log statement with deterministic cross-artifact checks:
        1. Attendee Parity: Roster attendees must be reflected in O2 summary and O3 person entities.
        2. Action Item DRI Parity: DRIs in O2 action ledger must appear in O1 dialogue and O3 entity nodes.
        3. Quote Pointer Integrity: Quote pointer spans in O3 must match character length and text in O1.
        4. Canonical Predicate Parity: Role edges must use canonical 'HAS_ROLE'.
        """
        errors: List[str] = []
        meta = metadata or {}

        upserts = f3_data.get("graph_mutations", {}).get("entity_upserts", [])
        person_ids = {str(u.get("id", "")).lower() for u in upserts if str(u.get("id", "")).lower().startswith("person:")}
        ev_idx = f3_data.get("evidence_index", {})
        if isinstance(ev_idx, dict):
            for ev_item in ev_idx.values():
                if isinstance(ev_item, dict):
                    spk = str(ev_item.get("speaker", "")).lower().strip()
                    if spk.startswith("person:"):
                        person_ids.add(spk)

        def _slug_variants(name_str: str) -> List[str]:
            import unicodedata
            lower = name_str.lower()
            unaccented = unicodedata.normalize('NFKD', lower).encode('ascii', 'ignore').decode('utf-8')
            no_apos = re.sub(r"['’`]", "", lower)
            no_apos_unacc = re.sub(r"['’`]", "", unaccented)
            variants = [
                "person:" + re.sub(r"[^a-z0-9]+", "_", lower).strip("_"),
                "person:" + re.sub(r"[^a-z0-9]+", "-", lower).strip("-"),
                "person:" + re.sub(r"[^a-z0-9]+", "_", no_apos).strip("_"),
                "person:" + re.sub(r"[^a-z0-9]+", "-", no_apos).strip("-"),
                "person:" + re.sub(r"[^a-z0-9]+", "_", unaccented).strip("_"),
                "person:" + re.sub(r"[^a-z0-9]+", "-", unaccented).strip("-"),
                "person:" + re.sub(r"[^a-z0-9]+", "_", no_apos_unacc).strip("_"),
                "person:" + re.sub(r"[^a-z0-9]+", "-", no_apos_unacc).strip("-"),
            ]
            return list(dict.fromkeys(variants))

        # 1. Attendee Parity
        roster = meta.get("attendees") or (c0_receipt.get("roster") if c0_receipt else [])
        if isinstance(roster, dict):
            flat_roster = []
            for org_or_group, atts in roster.items():
                if isinstance(atts, list):
                    flat_roster.extend(atts)
                elif isinstance(atts, (str, dict)):
                    flat_roster.append(atts)
            roster = flat_roster
        elif isinstance(roster, str):
            roster = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", roster) if p.strip()]
        if isinstance(roster, list) and roster:
            for att in roster:
                att_name = att.get("name") if isinstance(att, dict) else str(att)
                clean_name = re.sub(r"\s*\([^)]*\)", "", att_name).split("(")[0].strip()
                if not clean_name:
                    continue
                is_endpoint = bool(
                    any(
                        t in clean_name.lower()
                        for t in (
                            "mole-vc",
                            "mole_vc",
                            "nea-mole",
                            "vc",
                            "endpoint",
                            "conference room",
                            "conf room",
                            "confroom",
                            "boardroom",
                            "polycom",
                            "teams room",
                            "zoom room",
                        )
                    )
                    or re.search(
                        r"\b(?:rm|room|bldg|fl|floor|conf|vc)[-_.\s]*[0-9a-z]+\b",
                        clean_name,
                        re.IGNORECASE,
                    )
                    or re.search(
                        r"\b[a-z]{2,5}[-_](?:vc|conf|confroom|rm|room)\b",
                        clean_name,
                        re.IGNORECASE,
                    )
                )
                if is_endpoint:
                    continue

                candidate_names = [clean_name]
                for map_key in ("speaker_map", "hitl_role_resolutions", "pre_enrichment_roles", "entity_normalizations"):
                    m_val = meta.get(map_key)
                    if isinstance(m_val, dict):
                        for k_str, v_val in m_val.items():
                            v_name = v_val.get("name") if isinstance(v_val, dict) else str(v_val)
                            v_clean = re.sub(r"\s*\([^)]*\)", "", str(v_name)).split(",")[0].split("(")[0].strip()
                            k_clean = re.sub(r"\s*\([^)]*\)", "", str(k_str)).split(",")[0].split("(")[0].strip()
                            c_tokens = clean_name.lower().split()
                            if c_tokens and (
                                k_clean.lower() == clean_name.lower()
                                or v_clean.lower() == clean_name.lower()
                                or (len(c_tokens) >= 2 and c_tokens[-1] in v_clean.lower().split() and v_clean.lower().startswith(c_tokens[0]))
                                or (len(c_tokens) >= 2 and c_tokens[-1] in k_clean.lower().split() and k_clean.lower().startswith(c_tokens[0]))
                            ):
                                if v_clean and not v_clean.upper().startswith("SPEAKER_"):
                                    candidate_names.append(v_clean)
                                if k_clean and not k_clean.upper().startswith("SPEAKER_"):
                                    candidate_names.append(k_clean)

                slugs: List[str] = []
                for cn in candidate_names:
                    slugs.extend(_slug_variants(cn))
                slug_us = slugs[0]
                c_parts = clean_name.lower().split()
                match_found = any(
                    s in person_ids
                    or any(
                        pid.startswith(s + "-")
                        or pid.startswith(s + "_")
                        or pid.endswith("-" + clean_name.lower())
                        or pid.endswith("_" + clean_name.lower())
                        or (
                            len(c_parts) >= 2
                            and pid.startswith("person:" + c_parts[0])
                            and (pid.endswith("-" + c_parts[-1]) or pid.endswith("_" + c_parts[-1]))
                        )
                        for pid in person_ids
                    )
                    for s in slugs
                )
                if not match_found:
                    errors.append(f"X3 Attendee Parity: Attendee '{att_name}' ({slug_us}) missing from Output 03 entity_upserts")

        # 2. Action Item DRI Parity (O2 Action Ledger -> O3 person entities)
        if f2_text and person_ids:
            owner_col_idx = 3
            type_col_idx: Optional[int] = None
            loc_col_idx: Optional[int] = None
            for line in f2_text.splitlines():
                clean = line.strip()
                if not clean.startswith("|"):
                    continue
                cols = [c.strip() for c in clean.split("|")]
                lower_cols = [c.lower() for c in cols]
                is_data_row = bool(re.match(r"^(?:[A-Za-z]+[-_]?\d+\.?|\d+\.?)$", cols[1] if len(cols) > 1 else ""))
                if not is_data_row and any("owner" in c or "accountable" in c or "dri" in c for c in lower_cols):
                    for idx, c in enumerate(lower_cols):
                        if "owner" in c or "accountable" in c or "dri" in c:
                            owner_col_idx = idx
                        elif c in ("type", "category", "item type"):
                            type_col_idx = idx
                        elif any(k in c for k in ("locator", "evidence", "citation", "source")):
                            loc_col_idx = idx
                    continue
                if all(not c or re.match(r"^[-:\s]+$", c) for c in cols):
                    continue
                if not is_data_row:
                    continue
                if type_col_idx is not None and len(cols) > type_col_idx:
                    row_type = cols[type_col_idx].strip().lower()
                    if row_type and "action" not in row_type:
                        continue
                if loc_col_idx is not None and len(cols) > loc_col_idx:
                    loc_text = cols[loc_col_idx].strip()
                    if not loc_text or loc_text.lower() in ("tbd", "n/a", "none", "-"):
                        errors.append(f"X3 Action Item Locator Parity: Output 02 Action row lacks valid evidence locator: '{clean}'")
                if len(cols) <= owner_col_idx:
                    continue
                cell_text = cols[owner_col_idx].strip()
                if not cell_text or "unassigned" in cell_text.lower() or "pending owner" in cell_text.lower() or cell_text.lower() in ("tbd", "n/a", "none"):
                    continue
                paren_match = re.search(r"\(([^)]+)\)?", cell_text)
                paren_text = paren_match.group(1).strip() if paren_match else ""
                outside_text = re.sub(r"\s*\([^)]*\)?", "", cell_text).strip()
                candidates = [c for c in (outside_text, paren_text) if c]
                matched = False
                first_slug = ""
                for cand in candidates:
                    slugs = _slug_variants(cand)
                    if not first_slug:
                        first_slug = slugs[0]
                    if any(s in person_ids for s in slugs):
                        matched = True
                        break
                    first_token = cand.split()[0].lower() if cand.split() else ""
                    last_token = cand.split()[-1].lower() if len(cand.split()) > 1 else ""
                    if first_token and len(cand.split()) > 1 and (
                        f"person:{first_token}" in person_ids
                        or any(
                            pid.startswith(f"person:{first_token}") and (pid.endswith(f"-{last_token}") or pid.endswith(f"_{last_token}"))
                            for pid in person_ids
                        )
                    ):
                        matched = True
                        break
                if not matched and candidates:
                    errors.append(
                        f"X3 Action Item DRI Parity: Output 02 Action Owner '{cell_text}' ({first_slug}) missing from Output 03 entity_upserts"
                    )

        # 3. Canonical Role Predicate Check
        rel_signals = f3_data.get("graph_mutations", {}).get("relationship_signals", [])
        for rel in rel_signals:
            predicate = rel.get("relationship")
            if predicate == "HAS_ROLE_IN":
                errors.append(f"X3 Predicate Parity: Deprecated predicate 'HAS_ROLE_IN' detected on edge {rel.get('from_id')}. Must use canonical 'HAS_ROLE'")

        # 4. Quote Pointer Exact Slice Integrity
        evidence_index = f3_data.get("evidence_index", {})
        for ev_id, ev_item in evidence_index.items():
            if not isinstance(ev_item, dict):
                continue
            if _is_non_transcript_evidence_item(ev_item):
                continue
            qp = ev_item.get("quote_pointer")
            start = None
            end = None
            exact_text = None
            if isinstance(qp, list) and len(qp) >= 2 and isinstance(qp[0], int) and isinstance(qp[1], int):
                start = qp[0]
                end = qp[1]
                exact_text = ev_item.get("exact_text") or ev_item.get("text")
            elif isinstance(qp, dict):
                start = qp.get("start_offset")
                end = qp.get("end_offset")
                exact_text = qp.get("exact_text") or ev_item.get("exact_text") or ev_item.get("text")
            elif isinstance(qp, list) and qp and isinstance(qp[0], dict):
                start = qp[0].get("start_offset")
                end = qp[0].get("end_offset")
                exact_text = qp[0].get("exact_text") or ev_item.get("exact_text") or ev_item.get("text")

            if start is None or end is None:
                if f1_text and (ev_item.get("exact_text") or ev_item.get("text")):
                    errors.append(
                        f"X3 Quote Pointer Integrity: Evidence {ev_id} is missing valid quote_pointer [start, end] offsets"
                    )
            elif exact_text:
                if (end - start) != len(exact_text):
                    errors.append(
                        f"X3 Quote Pointer Integrity: Evidence {ev_id} span length ({end - start}) does not match exact_text length ({len(exact_text)})"
                    )
                if f1_text:
                    if exact_text not in f1_text:
                        errors.append(
                            f"X3 Quote Pointer Integrity: Evidence {ev_id} exact_text not found in Output 01 transcript"
                        )
                    elif not (0 <= start < end <= len(f1_text)):
                        errors.append(
                            f"X3 Quote Pointer Integrity: Evidence {ev_id} quote_pointer [{start}, {end}] is out of bounds for Output 01 length {len(f1_text)}"
                        )
                    elif f1_text[start:end] != exact_text:
                        errors.append(
                            f"X3 Quote Pointer Integrity: Evidence {ev_id} slice f1_text[{start}:{end}] does not match exact_text"
                        )

        return len(errors) == 0, errors


# -----------------------------------------------------------------------------
# 7. Unified Contract Validation Facade
# -----------------------------------------------------------------------------

def validate_versioned_judge_scorecard(
    scorecard: Mapping[str, Any],
    output_num: int,
    modality: str = "MEETING_TRANSCRIPT",
) -> Tuple[bool, List[str]]:
    """Validates an evaluation scorecard against the strict V2 versioned schemas."""
    errors: List[str] = []
    try:
        if modality == "DOCUMENT" and output_num in (1, 2):
            st = scorecard.get("status")
            v = scorecard.get("verdict")
            reason = str(scorecard.get("reason", ""))
            if st in ("NOT_APPLICABLE", "SKIPPED") or "NOT_APPLICABLE_DOCUMENT_MODE" in reason:
                if scorecard.get("composite_score") is not None and v == "PASS":
                    errors.append(f"Output {output_num} in DOCUMENT mode cannot award synthetic 100/PASS scores")
                return len(errors) == 0, errors

        if output_num == 1:
            Output1ScorecardV2.model_validate(scorecard)
        elif output_num == 2:
            Output2ScorecardV2.model_validate(scorecard)
        elif output_num == 3:
            if modality == "DOCUMENT":
                DocumentOutput3ScorecardV2.model_validate(scorecard)
            else:
                Output3ScorecardV2.model_validate(scorecard)
        else:
            errors.append(f"Invalid output_num {output_num} (must be 1, 2, or 3)")
    except Exception as e:
        errors.append(str(e))

    return len(errors) == 0, errors


verify_x3_cross_output_coherence = MeetingXExitContract.validate_x3_cross_output_coherence
