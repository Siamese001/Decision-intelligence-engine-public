"""Dual-Pipeline Layer 0: Machine-Verifiable Prompt & Rubric Contracts.

SSOT prompt contract engine enforcing Law 6 (Policy Authority & Invariance).
Provides:
1. Typed, machine-verifiable obligations across generator and judge contracts.
2. Canonical obligation digests sensitive to changed obligations, weights, and overrides.
3. Assembled bundle fingerprinting for effective compiled prompt bundles.
4. Mutation detection and contract parity verification.
5. Strict single named individual DRI and owner rule validation.

Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from dual_pipeline.parameters import get_output2_section_score_specs
from dual_pipeline.parameters_ops import ReleaseGatesConfig
from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG, EnginePromptConfigSSOT, Output2Config

if not hasattr(EnginePromptConfigSSOT, "release_gates"):
    EnginePromptConfigSSOT.release_gates = property(lambda self: ReleaseGatesConfig())  # type: ignore[attr-defined]
if not hasattr(Output2Config, "section_specs"):
    Output2Config.section_specs = property(lambda self: get_output2_section_score_specs())  # type: ignore[attr-defined]
if not hasattr(Output2Config, "section_weights"):
    Output2Config.section_weights = property(lambda self: {k: mx for k, mx, _ in get_output2_section_score_specs()})  # type: ignore[attr-defined]
if not hasattr(Output2Config, "section_floors"):
    Output2Config.section_floors = property(lambda self: {k: flr for k, _, flr in get_output2_section_score_specs()})  # type: ignore[attr-defined]


def _canonical_json_bytes(data: Any) -> bytes:
    """Serializes arbitrary Python dicts/lists into RFC-8259 canonical JSON bytes."""
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class PromptContract:
    """Immutable, machine-verifiable contract governing generation and evaluation."""

    contract_id: str
    output_id: int
    stage_name: str
    version: str = "1.0.0"
    required_sections: Tuple[str, ...] = field(default_factory=tuple)
    conditional_sections: Tuple[str, ...] = field(default_factory=tuple)
    forbidden_sections: Tuple[str, ...] = field(default_factory=tuple)
    owner_rules: Dict[str, Any] = field(default_factory=dict)
    rubric_weights: Dict[str, int] = field(default_factory=dict)
    thresholds: Dict[str, float] = field(default_factory=dict)
    modality_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    prohibited_patterns: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Convert contract to dictionary representation."""
        return asdict(self)

    def canonical_digest(self) -> str:
        """Returns the canonical RFC-8259 SHA-256 digest of contract obligations."""
        return canonical_obligation_digest(self)

    def validate_integrity(self) -> Tuple[bool, List[str]]:
        """Validates contract internal consistency and point conservation."""
        issues: List[str] = []
        if not self.contract_id:
            issues.append("contract_id is required.")
        if self.output_id not in (1, 2, 3):
            issues.append(f"Invalid output_id: {self.output_id}. Must be 1, 2, or 3.")

        if self.rubric_weights:
            total_points = sum(self.rubric_weights.values())
            if total_points != 100:
                issues.append(f"Rubric weights sum to {total_points} (expected 100).")

        req_set = set(self.required_sections)
        cond_set = set(self.conditional_sections)
        forb_set = set(self.forbidden_sections)

        if req_set & cond_set:
            issues.append(f"Sections overlap between required and conditional: {req_set & cond_set}")
        if req_set & forb_set:
            issues.append(f"Sections overlap between required and forbidden: {req_set & forb_set}")
        if cond_set & forb_set:
            issues.append(f"Sections overlap between conditional and forbidden: {cond_set & forb_set}")

        return len(issues) == 0, issues


def canonical_obligation_digest(contract: PromptContract) -> str:
    """Computes deterministic SHA-256 over machine-verifiable contract obligations.

    Sensitive to:
    - required_sections, conditional_sections, forbidden_sections
    - rubric_weights
    - owner_rules
    - thresholds
    - modality_overrides
    - prohibited_patterns

    Prose adjustments that do not alter these machine-verifiable obligations do
    not change this digest.
    """
    payload = {
        "contract_id": contract.contract_id,
        "output_id": contract.output_id,
        "stage_name": contract.stage_name,
        "version": contract.version,
        "required_sections": list(contract.required_sections),
        "conditional_sections": list(contract.conditional_sections),
        "forbidden_sections": list(contract.forbidden_sections),
        "owner_rules": contract.owner_rules,
        "rubric_weights": dict(sorted(contract.rubric_weights.items())),
        "thresholds": dict(sorted(contract.thresholds.items())),
        "modality_overrides": contract.modality_overrides,
        "prohibited_patterns": list(contract.prohibited_patterns),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def compute_assembled_bundle_fingerprint(
    components: Sequence[Union[str, bytes, Path]],
) -> str:
    """Computes deterministic SHA-256 over an assembled bundle of prompt components.

    Normalizes newlines, strips trailing whitespace on each line, and separates
    components with a canonical boundary marker.
    """
    normalized_parts: List[str] = []
    for c in components:
        if isinstance(c, Path):
            text = c.read_text(encoding="utf-8")
        elif isinstance(c, bytes):
            text = c.decode("utf-8")
        else:
            text = str(c)
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
        normalized_parts.append("\n".join(lines).strip())
    bundled_text = "\n\n---CANONICAL_PROMPT_BUNDLE_BOUNDARY---\n\n".join(normalized_parts)
    return hashlib.sha256(bundled_text.encode("utf-8")).hexdigest()


def validate_owner_string(owner: str, contract: PromptContract) -> Tuple[bool, Optional[str]]:
    """Validates an owner value against contract owner rules.

    Enforces:
    1. Rejection of blank / empty owners.
    2. Rejection of prohibited tokens ('unassigned', 'pending', 'tbd', 'team', 'all', etc.).
    3. If require_single_named_dri is True: rejection of collective or multiple slash-separated owners.
    """
    if not owner or not owner.strip():
        return False, "Owner string is empty or blank."
    clean_owner = owner.strip()
    owner_lower = clean_owner.lower()

    prohibited = contract.owner_rules.get("prohibited_owner_tokens", ())
    for token in prohibited:
        if re.search(rf"\b{re.escape(token.lower())}\b", owner_lower):
            return False, f"Owner '{clean_owner}' contains prohibited placeholder or collective token '{token}'."

    if contract.owner_rules.get("require_single_named_dri", False):
        if any(sep in clean_owner for sep in (",", "/", "&", ";")) or re.search(r"\band\b", owner_lower):
            return False, f"Owner '{clean_owner}' designates multiple or collective owners; must be single named DRI."

    return True, None


def detect_contract_mutation(
    baseline: PromptContract, candidate: PromptContract
) -> Tuple[bool, List[str]]:
    """Detects differences in machine-verifiable obligations between two contracts."""
    diffs: List[str] = []
    if baseline.contract_id != candidate.contract_id:
        diffs.append(f"contract_id changed: {baseline.contract_id} != {candidate.contract_id}")
    if baseline.output_id != candidate.output_id:
        diffs.append(f"output_id changed: {baseline.output_id} != {candidate.output_id}")
    if set(baseline.required_sections) != set(candidate.required_sections):
        diffs.append(
            f"required_sections mutated: baseline={sorted(baseline.required_sections)} vs candidate={sorted(candidate.required_sections)}"
        )
    if set(baseline.conditional_sections) != set(candidate.conditional_sections):
        diffs.append(
            f"conditional_sections mutated: baseline={sorted(baseline.conditional_sections)} vs candidate={sorted(candidate.conditional_sections)}"
        )
    if set(baseline.forbidden_sections) != set(candidate.forbidden_sections):
        diffs.append(
            f"forbidden_sections mutated: baseline={sorted(baseline.forbidden_sections)} vs candidate={sorted(candidate.forbidden_sections)}"
        )
    if baseline.rubric_weights != candidate.rubric_weights:
        diffs.append(
            f"rubric_weights mutated: baseline={baseline.rubric_weights} vs candidate={candidate.rubric_weights}"
        )
    if baseline.owner_rules != candidate.owner_rules:
        diffs.append(
            f"owner_rules mutated: baseline={baseline.owner_rules} vs candidate={candidate.owner_rules}"
        )
    if baseline.thresholds != candidate.thresholds:
        diffs.append(
            f"thresholds mutated: baseline={baseline.thresholds} vs candidate={candidate.thresholds}"
        )
    if baseline.modality_overrides != candidate.modality_overrides:
        diffs.append(
            f"modality_overrides mutated: baseline={baseline.modality_overrides} vs candidate={candidate.modality_overrides}"
        )
    if set(baseline.prohibited_patterns) != set(candidate.prohibited_patterns):
        diffs.append(
            f"prohibited_patterns mutated: baseline={sorted(baseline.prohibited_patterns)} vs candidate={sorted(candidate.prohibited_patterns)}"
        )
    return len(diffs) > 0, diffs


def verify_contract_parity(
    generator_contract: PromptContract, judge_contract: PromptContract
) -> Tuple[bool, List[str]]:
    """Verifies alignment between generation prompt contract and judge rubric contract."""
    defects: List[str] = []
    if generator_contract.output_id != judge_contract.output_id:
        defects.append(
            f"Output ID mismatch: generator={generator_contract.output_id} vs judge={judge_contract.output_id}"
        )

    judge_total = sum(judge_contract.rubric_weights.values())
    if judge_total != 100:
        defects.append(f"Judge rubric weights sum to {judge_total} (must equal 100).")

    for req_sec in generator_contract.required_sections:
        if req_sec in judge_contract.forbidden_sections:
            defects.append(f"Section '{req_sec}' is required by generator but forbidden by judge.")

    for forb_sec in generator_contract.forbidden_sections:
        if forb_sec in judge_contract.required_sections:
            defects.append(f"Section '{forb_sec}' is forbidden by generator but required by judge.")

    gen_owner = generator_contract.owner_rules
    judge_owner = judge_contract.owner_rules
    if gen_owner.get("disallow_unassigned") != judge_owner.get("disallow_unassigned"):
        defects.append("Owner rule mismatch: disallow_unassigned diverges between generator and judge.")
    if gen_owner.get("require_single_named_dri") != judge_owner.get("require_single_named_dri"):
        defects.append("Owner rule mismatch: require_single_named_dri diverges between generator and judge.")

    return len(defects) == 0, defects


# Canonical SSOT Contracts
CANONICAL_OUTPUT1_PROMPT_CONTRACT = PromptContract(
    contract_id="OUTPUT_01_FORENSIC_TRANSCRIPT",
    output_id=1,
    stage_name="STAGE_01_FORENSIC",
    version="1.0.0",
    required_sections=(
        "Forensic Transcript",
        "Ambiguity Lock Register",
    ),
    conditional_sections=(),
    forbidden_sections=(),
    owner_rules={
        "disallow_unassigned": True,
        "require_single_named_dri": False,
        "prohibited_owner_tokens": ("unassigned", "pending", "tbd"),
    },
    rubric_weights={
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 25,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 25,
    },
    thresholds={"min_passing_score": float(CANONICAL_PROMPT_CONFIG.release_gates.output1_min_score)},
    prohibited_patterns=(
        "Fabricated dialogue",
        "Invented timestamps",
    ),
)

CANONICAL_OUTPUT2_PROMPT_CONTRACT = PromptContract(
    contract_id="OUTPUT_02_EXECUTIVE_RECAP",
    output_id=2,
    stage_name="STAGE_02_RECAP",
    version="1.0.0",
    required_sections=(
        "Meeting Objective",
        "Key Discussion Points & Findings",
        "Action & Governance Ledger",
        "Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
    ),
    conditional_sections=(
        "Strategic Synthesis",
        "Recommendations & Decisions Required",
        "Architectural Trade-Off Matrix",
        "Business Meaning and Value Case",
        "Immediate Delivery Scope & Release Boundaries",
    ),
    forbidden_sections=(
        "Active Technical Spikes & Experimental Inquiries",
    ),
    owner_rules={
        "disallow_unassigned": True,
        "disallow_collective": True,
        "require_single_named_dri": True,
        "prohibited_owner_tokens": (
            "unassigned",
            "pending",
            "tbd",
            "partner_advisory",
            "client",
            "team",
            "all",
        ),
    },
    rubric_weights={
        "dimension_1_executive_altitude": 25,
        "dimension_2_technical_substance": 25,
        "dimension_3_strategic_synthesis": 25,
        "dimension_4_scoping_and_modality": 25,
    },
    thresholds={"min_passing_score": float(CANONICAL_PROMPT_CONFIG.release_gates.output2_min_score)},
    modality_overrides={
        "ARCHITECTURE_DEEP_DIVE": {
            "forbidden_sections": (
                "Business Meaning and Value Case",
                "Immediate Delivery Scope & Release Boundaries",
                "Active Technical Spikes & Experimental Inquiries",
            ),
            "conditional_sections": (
                "Architectural Trade-Off Matrix",
            ),
        },
    },
    prohibited_patterns=(
        "aspirational operating concept",
        "evidenced rejection behavior",
        "hypothesized routing",
        "configuration persistence versus data portability",
        "manifest-controlled",
        "bounded execution wedge",
        "coaching " + "future candidates",
        "synthetic monday-through-friday",
        "tactical task-tracker",
        "no reliable utterance mapping",
        "lacks speaker mapping",
        "requires speaker-resolved evidence",
        "individual architectural position therefore remains unconfirmed",
        "individual position remains unconfirmed",
        "cannot safely be presented as",
        "neither agreement nor disagreement should be inferred",
        "individual stance is unconfirmed",
    ),
)

CANONICAL_OUTPUT3_PROMPT_CONTRACT = PromptContract(
    contract_id="OUTPUT_03_GRAPH_EXTRACTION",
    output_id=3,
    stage_name="STAGE_03_GRAPH",
    version="1.0.0",
    required_sections=(
        "Evidence Envelope",
        "Graph Mutations",
    ),
    conditional_sections=(),
    forbidden_sections=(),
    owner_rules={
        "disallow_unassigned": True,
        "require_single_named_dri": False,
        "prohibited_owner_tokens": ("unassigned", "pending", "tbd"),
    },
    rubric_weights={
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 20,
    },
    thresholds={"min_passing_score": float(CANONICAL_PROMPT_CONFIG.release_gates.output3_min_score)},
    prohibited_patterns=(
        "Generic predicates (RELATES_TO, ASSOCIATED_WITH)",
        "Action items in entity mutations",
    ),
)

CANONICAL_PROMPT_CONTRACTS: Dict[int, PromptContract] = {
    1: CANONICAL_OUTPUT1_PROMPT_CONTRACT,
    2: CANONICAL_OUTPUT2_PROMPT_CONTRACT,
    3: CANONICAL_OUTPUT3_PROMPT_CONTRACT,
}
