"""
dual_pipeline/prompts/contracts/output1_contract.py

Declarative SSOT Contract for Output 01 (Forensic Source-of-Truth Transcript).
Governs generation, QA Gate 1, and ForensicTranscriptJudge evaluation.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .output_contract import (
    MeetingArchetype,
    OutputContract,
    RubricDimension,
    SectionRequirement,
    SectionSpecification,
)


def build_output1_contract() -> OutputContract:
    """Build canonical Output 01 contract."""
    sections = {
        "Forensic Transcript": SectionSpecification(
            header="Forensic Transcript",
            description="Chronological, verbatim speaker turns with HH:MM:SS timestamps.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance="Verbatim transcript with clean speaker attribution and preserved domain terms.",
            anti_patterns=(
                "Fabricated dialogue",
                "Missing speaker labels",
                "Invented timestamps",
                "Single-speaker collapse (>=85% turns to one speaker in multi-attendee meetings)",
                "Generic speaker tags >15%",
            ),
        ),
        "Ambiguity Lock Register": SectionSpecification(
            header="Ambiguity Lock Register",
            description="Itemized register tracking homophones, unclear terms, acronyms, or phonetic ambiguities.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance="Lock unresolved terms without inventing definitions.",
            anti_patterns=("Unresolved phonetic guesses presented as fact",),
        ),
    }

    rubric_dimensions = (
        RubricDimension(
            name="Forensic Grounding & Fidelity",
            key="dimension_1_forensic_grounding_and_fidelity",
            max_points=25,
            description="Verbatim accuracy against reference source text, zero hallucinated dialogue or invented turns.",
            criteria=("100% fidelity to reference dialogue", "No fabricated utterances", "Accurate technical terminology"),
            floor=14.0,
        ),
        RubricDimension(
            name="Attribution & Room Mic Hygiene",
            key="dimension_2_attribution_and_room_mic_hygiene",
            max_points=25,
            description="Correct speaker identification, clean turn separation, and attribution hygiene.",
            criteria=(
                "Accurate speaker attribution",
                "No cross-turn contamination",
                "Single-speaker collapse (>=85% turns across >=10 turns) prevented in multi-attendee meetings",
                "Company floor attribution when speaker identification ambiguous",
                "Generic speaker tags <= 15%",
            ),
            floor=14.0,
        ),
        RubricDimension(
            name="Chronological & Timestamp Integrity",
            key="dimension_3_chronological_and_timestamp_integrity",
            max_points=25,
            description="Deterministic timestamp monotonicity, non-collapse verification, and format standard [HH:MM:SS].",
            criteria=(
                "Monotonic timestamps",
                "HH:MM:SS standard",
                "Explicit gap markers [Recording gap: ...]",
                "Non-collapsed timestamps (>=25% unique and <=15% zero)",
            ),
            floor=14.0,
        ),
        RubricDimension(
            name="ASR Cleanup & Domain Preservation",
            key="dimension_4_asr_cleanup_and_domain_preservation",
            max_points=25,
            description="Clean removal of stutters/hesitations while strictly preserving domain jargon, acronyms, and names.",
            criteria=("Cleaned disfluencies", "Preserved domain jargon", "Preserved proper nouns and technical terms", "Whisper near-silence hallucinations removed"),
            floor=14.0,
        ),
    )

    class _Output1ContractImpl(OutputContract):
        single_speaker_collapse_threshold: float = 0.85

        def compile_stage_prompt(self, archetype: MeetingArchetype, extra_context: Optional[Dict[str, Any]] = None) -> str:
            from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
            return CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_01_FORENSIC")

        def compile_judge_prompt(self, archetype: MeetingArchetype) -> str:
            from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
            return CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_FORENSIC")

        def validate_c16_timestamp_monotonicity(self, f1_text: str) -> Tuple[bool, List[str]]:
            from dual_pipeline.llm_judge_core.forensic_checks import check_timestamp_integrity
            passed, defect_codes, metrics = check_timestamp_integrity(f1_text)
            return passed, metrics.get("defects", [])

    return _Output1ContractImpl(
        output_id=1,
        name="Forensic Source-of-Truth Transcript",
        description="Forensic transcript with speaker attribution, timestamps, ASR cleanup, and Ambiguity Lock Register.",
        sections=sections,
        rubric_dimensions=rubric_dimensions,
        prohibited_patterns=(
            "Fabricated speaker turns",
            "Inline editorial commentary",
            "Single-speaker collapse (>=85% turns across >=10 turns in multi-attendee meetings)",
            "Generic un-attributed speaker tags > 15%",
            "Unattributed multi-org dialogue",
        ),
        min_word_count=100,
        max_word_count=50000,
    )


OUTPUT1_CONTRACT = build_output1_contract()
