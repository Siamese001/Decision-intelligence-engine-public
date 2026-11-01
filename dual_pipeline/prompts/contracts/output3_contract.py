"""
dual_pipeline/prompts/contracts/output3_contract.py

Declarative SSOT Contract for Output 03 (Canonical Signal Projection & Evidence Envelope).
Governs generation, QA Gate 3, and GraphExtractionJudge evaluation.
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


def build_output3_contract() -> OutputContract:
    """Build canonical Output 03 contract."""
    sections = {
        "Evidence Envelope": SectionSpecification(
            header="Evidence Index",
            description="Source-grounded evidence statements adhering to the 6 closed kind values.",
            aliases=("Evidence Envelope", "Evidence Index Contract", "evidence_index"),
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance="Link statements to verifiable timestamp locators. Enforce kind in {summary, decision, risk, dependency, technical, strategic}.",
            anti_patterns=(
                "Unanchored assertions",
                "Invalid kind enums (must be one of 6 closed kind values)",
                "Missing observed_at or stale_after on decisions/risks",
                "Non-empty action_updates",
            ),
        ),
        "Graph Mutations": SectionSpecification(
            header="Graph Mutations",
            description="Atomic entity upserts and canonical predicate relationships.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance="Entity IDs must use governed prefixes (person:, org:, system:, policy:, metric:, etc.). Must extract key attendee person: nodes with organization affiliations (MEMBER_OF) and workstream/system ownership (OWNS_OR_LEADS, OWNS, DELIVERS). Predicates must use W2 spine.",
            anti_patterns=(
                "Generic predicates (RELATES_TO, ASSOCIATED_WITH)",
                "Action items in entity mutations",
                "Missing observed_at / stale_after",
                "Non-empty action_updates (tactical task tracking belongs exclusively in Output 02)",
                "Omission of active meeting attendees in entity_upserts",
                "Empty decision_updates when architectural principles or policies are ratified",
            ),
        ),
    }

    rubric_dimensions = (
        RubricDimension(
            name="Strategic Edge & Enterprise Graph Elevation",
            key="dimension_1_strategic_edge",
            max_points=15,
            description="Captures high-leverage architectural and strategic entities rather than trivial conversation trivia.",
            applicable_archetypes=(
                MeetingArchetype.STRATEGIC_DISCOVERY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE,
                MeetingArchetype.GOVERNANCE_REVIEW,
                MeetingArchetype.LESSONS_LEARNED,
                MeetingArchetype.GENERAL,
            ),
            is_conditional=True,
            floor=11.0,
        ),
        RubricDimension(
            name="Knowledge Layer Density",
            key="dimension_2_knowledge_layer_density",
            max_points=15,
            description=(
                "High-signal entity and relationship selection reflecting core technical and strategic "
                "substance without low-signal node sprawl (entity-volume floor retired in DOCUMENT_MODE "
                "where narrative density belongs to Output 02)."
            ),
            is_conditional=True,
            floor=11.0,
        ),
        RubricDimension(
            name="Semantic Atomicity & Strict Entity Scoping",
            key="dimension_3_semantic_atomicity",
            max_points=20,
            description="Atomic entity names and single concepts, zero conversational fragments in entity labels.",
            floor=14.0,
        ),
        RubricDimension(
            name="Predicate Specificity & Canonical Taxonomy",
            key="dimension_4_predicate_specificity",
            max_points=15,
            description="Canonical W2 predicates only (INTEGRATES_WITH, DEPENDS_ON, EVALUATES); generic predicates forbidden.",
            floor=11.0,
        ),
        RubricDimension(
            name="Strict Exclusion of Action Items",
            key="dimension_5_exclusion_of_action_items",
            max_points=15,
            description="Action items and sprint tasks strictly partitioned out of knowledge graph entity mutations.",
            floor=11.0,
        ),
        RubricDimension(
            name="Evidence Grounding & Zero Hallucination",
            key="dimension_6_evidence_grounding_and_schema",
            max_points=20,
            description="Every mutation references verifiable evidence entries adhering to 6 closed kinds, governed prefixes, and C-07 temporal hygiene.",
            criteria=(
                "Source Factuality & Substantive Grounding (10.0 pts max, 7.0 floor): Assertions grounded in transcript; zero fabricated entities or synthetic decisions.",
                "Schema Strictness & Governed Enums (5.0 pts max, 3.5 floor): Strict adherence to 6 closed kinds and governed entity prefixes (person:, org:, system:, workstream:, project:, metric:, role:, policy:, deliverable:, milestone:, forum:, model:, initiative:, capability:, pattern:, decision:, proposal:, stakeholder:, document:).",
                "Timestamp & Locator Precision (5.0 pts max, 3.5 floor): Verifiable [HH:MM:SS] locators; minor bracket or timestamp variance capped at -1 to -2 pt graduated deduction.",
            ),
            floor=14.0,
        ),
    )

    class _Output3ContractImpl(OutputContract):
        def compile_stage_prompt(self, archetype: MeetingArchetype, extra_context: Optional[Dict[str, Any]] = None) -> str:
            from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
            base = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_GRAPH")
            if archetype == MeetingArchetype.ARCHITECTURE_DEEP_DIVE:
                override = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_ARCHITECTURE_DEEP_DIVE_OVERRIDE")
                return f"{base}\n\n{override}"
            return base

        def compile_judge_prompt(self, archetype: MeetingArchetype) -> str:
            from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
            return CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_GRAPH")

    return _Output3ContractImpl(
        output_id=3,
        name="Canonical Signal Projection & Evidence Envelope",
        description="Source-bound graph delta and evidence envelope with classification/audience passthrough.",
        sections=sections,
        rubric_dimensions=rubric_dimensions,
        prohibited_patterns=("Action items in graph mutations", "Generic predicates"),
        min_word_count=50,
        max_word_count=20000,
    )


OUTPUT3_CONTRACT = build_output3_contract()
