"""Judge criterion applicability, deduction arithmetic, and scorecard validation contracts.

Enforces:
1. Strict type, NaN, infinity, and range bounds on scorecards (no defaults for missing/corrupted values).
2. Authoritative section sum aggregation for Output 02 with exact display dimension parity.
3. Strict 6-dimension schema and floors for Output 03.
4. Source-bound archetype and criteria applicability resolution.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype


class CriterionApplicability(str, Enum):
    """Applicability scopes for judge criteria."""
    ALL = "ALL"
    MULTI_ATTENDEE = "MULTI_ATTENDEE"
    ARCHETYPE_SPECIFIC = "ARCHETYPE_SPECIFIC"
    CONDITIONAL = "CONDITIONAL"


@dataclass(frozen=True)
class CriterionDeduction:
    """Itemized deduction record for an observed defect."""
    criterion_id: str
    observed_defect: str
    points_deducted: float
    location: str
    supported_correction: str


@dataclass(frozen=True)
class JudgeCriterion:
    """Versioned specification of an individual judge evaluation criterion."""
    criterion_id: str
    output_num: int
    title: str
    max_points: float
    floor: Optional[float] = None
    authority: str = "CANONICAL_RUBRIC"
    applicability: CriterionApplicability = CriterionApplicability.ALL
    applicable_archetypes: Tuple[MeetingArchetype, ...] = ()

    def is_applicable(
        self,
        archetype: MeetingArchetype,
        attendee_count: int = 1,
        turn_count: int = 1,
        is_conditional_active: bool = True,
    ) -> bool:
        """Determines if criterion applies to the target meeting context."""
        if self.applicability == CriterionApplicability.ALL:
            return True
        if self.applicability == CriterionApplicability.MULTI_ATTENDEE:
            return attendee_count > 1 and turn_count >= 10
        if self.applicability == CriterionApplicability.ARCHETYPE_SPECIFIC:
            return archetype in self.applicable_archetypes
        if self.applicability == CriterionApplicability.CONDITIONAL:
            return bool(is_conditional_active)
        return True


@dataclass
class ScorecardValidationResult:
    """Outcome of validating a judge evaluation scorecard."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    validated_composite: Optional[float] = None
    logprob_diagnostics: Optional[Dict[str, Any]] = None


def validate_score_value(val: Any, min_val: float, max_val: float, field_name: str) -> Optional[str]:
    """Validates that a score value is a finite number within [min_val, max_val]."""
    if val is None:
        return f"{field_name} is missing (None)"
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return f"{field_name} must be numeric, got {type(val).__name__} ({val!r})"
    if math.isnan(val) or math.isinf(val):
        return f"{field_name} must be finite, got {val!r}"
    if val < min_val or val > max_val:
        return f"{field_name} value {val} out of bounds [{min_val}, {max_val}]"
    return None


def validate_output1_scorecard(scorecard: Mapping[str, Any]) -> ScorecardValidationResult:
    """Validates Output 01 forensic judge scorecard."""
    errors: List[str] = []
    dim_keys = (
        "dimension_1_forensic_grounding_and_fidelity",
        "dimension_2_attribution_and_room_mic_hygiene",
        "dimension_3_chronological_and_timestamp_integrity",
        "dimension_4_asr_cleanup_and_domain_preservation",
    )
    dim_sum = 0.0
    for key in dim_keys:
        err = validate_score_value(scorecard.get(key), 0, 25, key)
        if err:
            errors.append(err)
        else:
            dim_sum += float(scorecard[key])

    comp_err = validate_score_value(scorecard.get("composite_score"), 0, 100, "composite_score")
    if comp_err:
        errors.append(comp_err)
    elif scorecard.get("composite_score") != dim_sum:
        errors.append(f"composite_score {scorecard.get('composite_score')} does not match dimension sum {dim_sum}")

    if errors:
        return ScorecardValidationResult(is_valid=False, errors=errors)
    return ScorecardValidationResult(
        is_valid=True,
        validated_composite=dim_sum,
        logprob_diagnostics=scorecard.get("logprob_diagnostics"),
    )


def _validate_output2_display_dimensions(
    scorecard: Mapping[str, Any], sec_sum: float, errors: List[str]
) -> None:
    dims = (
        "dimension_1_executive_altitude",
        "dimension_2_technical_substance",
        "dimension_3_strategic_synthesis",
        "dimension_4_scoping_and_modality",
    )
    if not any(scorecard.get(d) is not None for d in dims):
        return
    dim_sum = 0.0
    for d in dims:
        err = validate_score_value(scorecard.get(d), 0, 25, d)
        if err:
            errors.append(err)
        else:
            val = float(scorecard[d])
            dim_sum += val
            if val < 14.0:
                errors.append(f"{d} score {val} is below mandatory floor 14.0")
    if not errors and abs(dim_sum - sec_sum) > 1.0:
        errors.append(f"Display dimension sum {dim_sum} diverges from authoritative section sum {sec_sum}")


def validate_output2_scorecard(scorecard: Mapping[str, Any]) -> ScorecardValidationResult:
    """Validates Output 02 recap judge scorecard under authoritative section sum basis."""
    errors: List[str] = []
    sec_scores = scorecard.get("section_scores")
    if not isinstance(sec_scores, Mapping):
        return ScorecardValidationResult(is_valid=False, errors=["section_scores dictionary is required for Output 02"])

    from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
    section_specs = CANONICAL_PROMPT_CONFIG.output2.section_specs
    sec_sum = 0.0
    for sec_key, max_pts, floor_pts in section_specs:
        err = validate_score_value(sec_scores.get(sec_key), 0, max_pts, f"section_scores.{sec_key}")
        if err:
            errors.append(err)
        else:
            val = float(sec_scores[sec_key])
            sec_sum += val
            if val < floor_pts:
                errors.append(f"section_scores.{sec_key} score {val} is below mandatory floor {floor_pts}")

    comp_err = validate_score_value(scorecard.get("composite_score"), 0, 100, "composite_score")
    if comp_err:
        errors.append(comp_err)
    elif scorecard.get("composite_score") != sec_sum:
        errors.append(f"composite_score {scorecard.get('composite_score')} does not match authoritative section sum {sec_sum}")

    _validate_output2_display_dimensions(scorecard, sec_sum, errors)

    if errors:
        return ScorecardValidationResult(is_valid=False, errors=errors)
    return ScorecardValidationResult(
        is_valid=True,
        validated_composite=sec_sum,
        logprob_diagnostics=scorecard.get("logprob_diagnostics"),
    )


def validate_output3_scorecard(scorecard: Mapping[str, Any]) -> ScorecardValidationResult:
    """Validates Output 03 graph judge scorecard under 6-dimension schema with floors."""
    from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT
    eff_mode = (
        scorecard.get("pipeline_mode")
        or scorecard.get("modality")
        or scorecard.get("meeting_archetype")
        or scorecard.get("mode")
    )
    floors = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode=str(eff_mode) if eff_mode else None)
    dim_specs = tuple(
        (dim.key, dim.max_points, floors.get(dim.key), dim.is_conditional)
        for dim in OUTPUT3_CONTRACT.rubric_dimensions
    )
    errors: List[str] = []
    dim_sum = 0.0
    max_sum = 0.0
    for key, max_pts, floor_pts, is_conditional in dim_specs:
        v = scorecard.get(key)
        if v is None and is_conditional:
            continue
        err = validate_score_value(v, 0, max_pts, key)
        if err:
            errors.append(err)
        else:
            val = float(v)
            dim_sum += val
            max_sum += max_pts
            if floor_pts is not None and val < floor_pts:
                errors.append(f"{key} score {val} is below mandatory floor {floor_pts}")

    expected_composite = round((dim_sum / max_sum) * 100.0, 2) if max_sum > 0 and max_sum < 100.0 else dim_sum

    comp_err = validate_score_value(scorecard.get("composite_score"), 0, 100, "composite_score")
    if comp_err:
        errors.append(comp_err)
    elif abs(float(scorecard.get("composite_score", 0)) - expected_composite) > 0.01:
        errors.append(f"composite_score {scorecard.get('composite_score')} does not match 6-dimension sum {expected_composite}")

    if errors:
        return ScorecardValidationResult(is_valid=False, errors=errors)
    return ScorecardValidationResult(
        is_valid=True,
        validated_composite=expected_composite,
        logprob_diagnostics=scorecard.get("logprob_diagnostics"),
    )


def _get_criteria_floors(output_id: int) -> Dict[str, float]:
    from dual_pipeline.prompts.contracts import get_dimension_floors_for_output
    return get_dimension_floors_for_output(output_id)


OUTPUT1_CRITERIA: Sequence[JudgeCriterion] = (
    JudgeCriterion("CRIT-OUT1-GROUNDING-01", 1, "Forensic dialogue grounding and turn fidelity", 25,
                   floor=_get_criteria_floors(1).get("dimension_1_forensic_grounding_and_fidelity")),
    JudgeCriterion("CRIT-OUT1-COLLAPSE-02", 1, "Speaker attribution and single-speaker collapse threshold", 25,
                   applicability=CriterionApplicability.MULTI_ATTENDEE,
                   floor=_get_criteria_floors(1).get("dimension_2_attribution_and_room_mic_hygiene")),
    JudgeCriterion("CRIT-OUT1-CHRONO-03", 1, "Chronological order and timestamp format integrity", 25,
                   floor=_get_criteria_floors(1).get("dimension_3_chronological_and_timestamp_integrity")),
    JudgeCriterion("CRIT-OUT1-DOMAIN-04", 1, "ASR cleanup, acronym preservation, and domain terminology", 25,
                   floor=_get_criteria_floors(1).get("dimension_4_asr_cleanup_and_domain_preservation")),
)

OUTPUT2_CRITERIA: Sequence[JudgeCriterion] = (
    JudgeCriterion("CRIT-OUT2-HEADER-01", 2, "Header, metadata, and wall-clock time window", 10, floor=5.5),
    JudgeCriterion("CRIT-OUT2-SUBSTANCE-02", 2, "Key updates and technical substance grounding", 35, floor=19.0),
    JudgeCriterion("CRIT-OUT2-LEDGER-03", 2, "Conditional extensions and action & governance ledger", 20, floor=11.0),
    JudgeCriterion("CRIT-OUT2-APPENDIX-04", 2, "Appendix stakeholder perspectives and contribution breakdown", 35, floor=19.0),
)

OUTPUT3_CRITERIA: Sequence[JudgeCriterion] = (
    JudgeCriterion("CRIT-OUT3-STRATEGIC-01", 3, "Strategic edge and principal technical relationships", 15,
                   floor=_get_criteria_floors(3).get("dimension_1_strategic_edge")),
    JudgeCriterion("CRIT-OUT3-DENSITY-02", 3, "Knowledge layer density and core cloud system entities", 15,
                   floor=_get_criteria_floors(3).get("dimension_2_knowledge_layer_density")),
    JudgeCriterion("CRIT-OUT3-ATOMICITY-03", 3, "Semantic atomicity of assertion proposals", 20,
                   floor=_get_criteria_floors(3).get("dimension_3_semantic_atomicity")),
    JudgeCriterion("CRIT-OUT3-PREDICATES-04", 3, "Predicate specificity and canonical taxonomy", 15,
                   floor=_get_criteria_floors(3).get("dimension_4_predicate_specificity")),
    JudgeCriterion("CRIT-OUT3-ACTION-EXCLUSION-05", 3, "Exclusion of tactical action items and sprint tasks", 15,
                   floor=_get_criteria_floors(3).get("dimension_5_exclusion_of_action_items")),
    JudgeCriterion("CRIT-OUT3-GROUNDING-SCHEMA-06", 3, "Evidence grounding, speaker citation, and schema compliance", 20,
                   floor=_get_criteria_floors(3).get("dimension_6_evidence_grounding_and_schema")),
)


def resolve_applicable_criteria(
    output_num: int,
    archetype: MeetingArchetype,
    attendee_count: int = 1,
    turn_count: int = 1,
) -> List[JudgeCriterion]:
    """Resolves applicable criteria for the given output and meeting context."""
    registry = {1: OUTPUT1_CRITERIA, 2: OUTPUT2_CRITERIA, 3: OUTPUT3_CRITERIA}
    criteria = registry.get(output_num, ())
    return [c for c in criteria if c.is_applicable(archetype, attendee_count, turn_count)]


@dataclass(frozen=True)
class AtomicSubCriterion:
    """Atomic sub-criterion evaluated via Behaviorally Anchored Rating Scales (BARS) or binary checklists."""
    subcriterion_id: str
    parent_criterion_id: str
    output_num: int
    title: str
    scale_type: str = "BARS_4"
    weight: float = 1.0
    description: str = ""


OUTPUT1_ATOMIC_SUBCRITERIA: Sequence[AtomicSubCriterion] = (
    AtomicSubCriterion("CRIT-OUT1-SUB-GROUNDING-ENTAILMENT", "CRIT-OUT1-GROUNDING-01", 1,
                       "Pragmatic dialogue entailment and factual fidelity", "BARS_4", 1.0,
                       "Verifies spoken dialogue turns are pragmatically entailed by raw source without hallucination."),
    AtomicSubCriterion("CRIT-OUT1-SUB-HEDGING-MODALITY", "CRIT-OUT1-GROUNDING-01", 1,
                       "Epistemic stance, hedging, and qualification preservation", "BARS_4", 1.0,
                       "Verifies hedging ('maybe', 'I think') and uncertainty markers are preserved without over-commitment."),
    AtomicSubCriterion("CRIT-OUT1-SUB-POWER-TOPOLOGY", "CRIT-OUT1-COLLAPSE-02", 1,
                       "Conversational power topology and turn distribution", "BARS_4", 1.0,
                       "Verifies turn distribution reflects multi-attendee dynamics and corporate affiliations without artificial collapse."),
    AtomicSubCriterion("CRIT-OUT1-SUB-ACRONYM-SEMANTICS", "CRIT-OUT1-DOMAIN-04", 1,
                       "Domain lexicon and proper noun fidelity", "BARS_4", 1.0,
                       "Verifies specialized cloud, architecture, and proper noun terms are preserved intact without phonetic corruption."),
)

OUTPUT2_ATOMIC_SUBCRITERIA: Sequence[AtomicSubCriterion] = (
    AtomicSubCriterion("CRIT-OUT2-SUB-EXECUTIVE-ALTITUDE", "CRIT-OUT2-SUBSTANCE-02", 2,
                       "Executive altitude and strategic context", "BARS_4", 1.0,
                       "Verifies executive summary elevates discussion to strategic operational significance rather than transcript parroting."),
    AtomicSubCriterion("CRIT-OUT2-SUB-MODALITY-INFLATION", "CRIT-OUT2-LEDGER-03", 2,
                       "Exploration vs commitment boundary", "BARS_4", 1.0,
                       "Verifies tentative brainstorming or exploratory options are not inflated into committed executive decisions."),
    AtomicSubCriterion("CRIT-OUT2-SUB-SENTIMENT-FRICTION", "CRIT-OUT2-SUBSTANCE-02", 2,
                       "Stakeholder friction and risk fidelity", "BARS_4", 1.0,
                       "Verifies genuine technical dissent, demo friction, and architectural blockers are preserved without whitewashing."),
    AtomicSubCriterion("CRIT-OUT2-SUB-PLAIN-ENGLISH", "CRIT-OUT2-HEADER-01", 2,
                       "Plain English and zero word salad", "BARS_4", 1.0,
                       "Verifies language is crisp, direct, and free from vacuous consulting jargon and pseudo-intellectual abstractions."),
)

OUTPUT3_ATOMIC_SUBCRITERIA: Sequence[AtomicSubCriterion] = (
    AtomicSubCriterion("CRIT-OUT3-SUB-SEMANTIC-ATOMICITY", "CRIT-OUT3-ATOMICITY-03", 3,
                       "Propositional atomicity of assertion proposals", "BARS_4", 1.0,
                       "Verifies assertions are individually falsifiable, single-relation claims rather than compound paragraphs."),
    AtomicSubCriterion("CRIT-OUT3-SUB-TAXONOMY-PREDICATES", "CRIT-OUT3-PREDICATES-04", 3,
                       "Ontological predicate specificity", "BARS_4", 1.0,
                       "Verifies predicates use canonical ontology types rather than generic, uninformative verbs ('HAS', 'RELATES_TO')."),
    AtomicSubCriterion("CRIT-OUT3-SUB-STRATEGIC-EDGE", "CRIT-OUT3-STRATEGIC-01", 3,
                       "Latent dependencies and architectural wedges", "BARS_4", 1.0,
                       "Verifies extraction captures latent systemic dependencies, vendor lock-in risks, and consulting leverage."),
    AtomicSubCriterion("CRIT-OUT3-SUB-BIPARTITE-RECALL", "CRIT-OUT3-GROUNDING-SCHEMA-06", 3,
                       "Evidence grounding and zero entity hallucination", "BARS_4", 1.0,
                       "Verifies all extracted nodes and edges bind directly to verbatim dialogue turns with verified speaker locators."),
)


def resolve_applicable_atomic_subcriteria(output_num: int) -> List[AtomicSubCriterion]:
    """Resolves atomic sub-criteria for the given output."""
    registry = {1: OUTPUT1_ATOMIC_SUBCRITERIA, 2: OUTPUT2_ATOMIC_SUBCRITERIA, 3: OUTPUT3_ATOMIC_SUBCRITERIA}
    return list(registry.get(output_num, ()))

