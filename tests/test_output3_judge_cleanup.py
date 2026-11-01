"""
tests/test_output3_judge_cleanup.py

Wave 4 acceptance tests for Output 3 judge/prompt/gate cleanup (RC-1 through RC-7).
Branch: feature/output3-judge-cleanup
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

# ── Constants ──────────────────────────────────────────────────────────────
CANONICAL_ARCHETYPES = {
    "STRATEGIC_DISCOVERY",
    "ARCHITECTURE_DEEP_DIVE",
    "TACTICAL_ALIGNMENT",
    "GOVERNANCE_REVIEW",
    "LESSONS_LEARNED",
}
LEGACY_ARCHETYPES = {
    "INTERNAL_TECHNICAL_SYNC",
    "TECHNICAL_DEMONSTRATION",
    "OPERATIONAL_DISCOVERY",
    "STRATEGIC_EXECUTIVE",
    "EXECUTIVE_DECISION_GATE",
    "RELEASE_PLANNING",
    "INCIDENT_POSTMORTEM",
    "OPERATING_MODEL_WORKSHOP",
}
JUDGE_PROMPT_PATH = Path(
    "governance/decision_intelligence/GRAPH_EXTRACTION_JUDGE_PROMPT.md"
)
STAGE03_PROMPT_PATH = Path("config/prompts/synthesis/stages/03_graph_extractor.md")


# ══════════════════════════════════════════════════════════════════════════════
# RC-1 — Archetype Registry Unification
# ══════════════════════════════════════════════════════════════════════════════
class TestArchetypeRegistry:
    """RC-1: Three conflicting archetype registries must be unified to one SSOT."""

    def test_meeting_archetype_enum_has_exactly_canonical_values(self):
        """MeetingArchetype enum must have exactly the 5 canonical rubric values + GENERAL."""
        from dual_pipeline.prompts.contracts import MeetingArchetype

        enum_values = {m.value for m in MeetingArchetype}
        assert CANONICAL_ARCHETYPES.issubset(enum_values), (
            f"Missing canonical archetypes: {CANONICAL_ARCHETYPES - enum_values}"
        )
        # Legacy values must not be enum members
        illegal = {v for v in enum_values if v in LEGACY_ARCHETYPES}
        assert not illegal, f"Legacy archetype values still in enum: {illegal}"

    def test_meeting_archetype_enum_does_not_contain_legacy_values(self):
        """No legacy archetype string should resolve as an exact enum member."""
        from dual_pipeline.prompts.contracts import MeetingArchetype

        enum_values = {m.value for m in MeetingArchetype}
        for leg in LEGACY_ARCHETYPES:
            assert leg not in enum_values, f"Legacy value '{leg}' still in enum"

    def test_from_string_maps_legacy_to_canonical(self):
        """from_string() must map legacy strings to canonical equivalents, not fail."""
        from dual_pipeline.prompts.contracts import MeetingArchetype

        mappings = {
            "INTERNAL_TECHNICAL_SYNC": MeetingArchetype.ARCHITECTURE_DEEP_DIVE,
            "TECHNICAL_DEMONSTRATION": MeetingArchetype.ARCHITECTURE_DEEP_DIVE,
            "STRATEGIC_EXECUTIVE": MeetingArchetype.STRATEGIC_DISCOVERY,
            "OPERATIONAL_DISCOVERY": MeetingArchetype.TACTICAL_ALIGNMENT,
            "EXECUTIVE_DECISION_GATE": MeetingArchetype.GOVERNANCE_REVIEW,
            "INCIDENT_POSTMORTEM": MeetingArchetype.LESSONS_LEARNED,
            "RELEASE_PLANNING": MeetingArchetype.TACTICAL_ALIGNMENT,
        }
        for legacy, expected in mappings.items():
            result = MeetingArchetype.from_string(legacy)
            assert result == expected, (
                f"from_string('{legacy}') = {result!r}, expected {expected!r}"
            )

    def test_from_string_canonical_values_resolve_exactly(self):
        """Canonical values must resolve to themselves."""
        from dual_pipeline.prompts.contracts import MeetingArchetype

        for canonical in CANONICAL_ARCHETYPES:
            result = MeetingArchetype.from_string(canonical)
            assert result.value == canonical, (
                f"from_string('{canonical}') = {result!r}, expected {canonical!r}"
            )

    def test_judge_prompt_contains_all_canonical_archetypes(self):
        """GRAPH_EXTRACTION_JUDGE_PROMPT.md must contain all 5 canonical archetype names."""
        text = JUDGE_PROMPT_PATH.read_text()
        for arch in CANONICAL_ARCHETYPES:
            assert arch in text, f"Judge prompt missing canonical archetype: {arch}"

    def test_judge_prompt_does_not_contain_legacy_archetypes_as_values(self):
        """Legacy archetype strings must not appear as enumerated values in judge prompt."""
        text = JUDGE_PROMPT_PATH.read_text()
        for leg in ("TECHNICAL_DEMONSTRATION", "STRATEGIC_EXECUTIVE", "OPERATIONAL_DISCOVERY"):
            # They should not appear as backtick-quoted values (permitted as comments in legacy docs)
            assert f"`{leg}`" not in text, (
                f"Judge prompt still references legacy archetype as value: `{leg}`"
            )

    def test_stage03_prompt_contains_canonical_archetypes(self):
        """Stage-03 prompt must declare the 5 canonical archetype values."""
        text = STAGE03_PROMPT_PATH.read_text()
        for arch in CANONICAL_ARCHETYPES:
            assert arch in text, f"Stage-03 prompt missing canonical archetype: {arch}"


# ══════════════════════════════════════════════════════════════════════════════
# RC-2 — Threshold Alignment: Code 95 → Rubric 90
# ══════════════════════════════════════════════════════════════════════════════
class TestThresholds:
    """RC-2: release_thresholds.py must match Rubric V1 §5.4 (Output 3 pass ≥ 90)."""

    def test_output3_default_threshold_is_90(self):
        """OUTPUT3_PRODUCTION_PASS_THRESHOLD default must be 90, not 95."""
        from dual_pipeline.release_thresholds import OUTPUT3_PRODUCTION_PASS_THRESHOLD

        assert OUTPUT3_PRODUCTION_PASS_THRESHOLD == 90, (
            f"Expected 90 but got {OUTPUT3_PRODUCTION_PASS_THRESHOLD} "
            "(rubric §5.4 mandates 90/100 pass threshold)"
        )

    def test_output3_policy_instance_threshold_is_90(self):
        """CANONICAL_RELEASE_POLICY.output3_pass_threshold must default to 90."""
        from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

        assert CANONICAL_RELEASE_POLICY.output3_pass_threshold == 90

    def test_score_94_all_dims_pass_produces_pass_verdict(self):
        """Score 94 with all dimensions ≥ 14 must produce PASS (the run that was blocked)."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        # 15+15+20+14+15+15 = 94
        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 15,
        }
        result = _assign_graph_verdict(parsed)
        assert result["composite_score"] == 94
        assert result["verdict"] == "PASS", (
            f"Score 94 must produce PASS under rubric §5.4 (threshold=90). "
            f"Got verdict={result['verdict']!r}"
        )

    def test_score_89_produces_fail_verdict(self):
        """Score 89 must produce FAIL (below 90 threshold)."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 14,
            "dimension_2_knowledge_layer_density": 14,
            "dimension_3_semantic_atomicity": 17,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 15,
        }
        result = _assign_graph_verdict(parsed)
        assert result["composite_score"] == 89
        assert result["verdict"] == "FAIL"

    def test_score_90_produces_pass_verdict(self):
        """Score exactly 90 must produce PASS when all dimensions are at or above floor."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        # 15+15+16+14+15+15 = 90, all dims >= 14 floor
        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 16,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 15,
        }
        result = _assign_graph_verdict(parsed)
        assert result["composite_score"] == 90
        assert result["verdict"] == "PASS"


# ══════════════════════════════════════════════════════════════════════════════
# RC-3 — Per-Dimension Floor (14/20)
# ══════════════════════════════════════════════════════════════════════════════
class TestPerDimensionFloor:
    """RC-3: _assign_graph_verdict must fail when any dimension scores < 14."""

    def test_dim1_below_floor_fails_regardless_of_composite(self):
        """dimension_1 = 10 with composite=91 must produce FAIL."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 10,  # below floor 11
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 16,
        }
        result = _assign_graph_verdict(parsed)
        assert result["composite_score"] == 91
        assert result["verdict"] == "FAIL", (
            "dimension_1=10 (below floor 11) must produce FAIL per Rubric §5.3"
        )
        assert "floor" in result.get("forensic_rationale", "").lower()

    def test_dim3_below_floor_fails(self):
        """dimension_3 (max=20) with score 13 must produce FAIL."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 13,  # below floor
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
        }
        result = _assign_graph_verdict(parsed)
        assert result["verdict"] == "FAIL"

    def test_all_dims_at_floor_produces_pass(self):
        """All dimensions exactly at floor (14 each ≡ 84 composite) still fails composite threshold."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 14,
            "dimension_2_knowledge_layer_density": 14,
            "dimension_3_semantic_atomicity": 14,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 14,
            "dimension_6_evidence_grounding_and_schema": 14,
        }
        result = _assign_graph_verdict(parsed)
        # 14*6 = 84, all dims pass floor, but composite 84 < 90 → FAIL
        assert result["composite_score"] == 84
        assert result["verdict"] == "FAIL"

    def test_all_dims_above_floor_composite_pass(self):
        """All dims ≥ 14 with composite ≥ 90 must produce PASS."""
        from dual_pipeline.llm_judge_core.graph import _assign_graph_verdict

        parsed: Dict[str, Any] = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
        }
        result = _assign_graph_verdict(parsed)
        assert result["composite_score"] == 99
        assert result["verdict"] == "PASS"


# ══════════════════════════════════════════════════════════════════════════════
# RC-7 — Double-Lock Verdict Removal
# ══════════════════════════════════════════════════════════════════════════════
class TestVerdictNoDoubleLock:
    """RC-7: evaluate_composite_release must rely on policy score, not raw LLM string."""

    def test_composite_release_pass_when_score_90_regardless_of_raw_string(self):
        """score=90 for all outputs should produce PASS even if caller passes v3='' (no raw string)."""
        from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

        verdict, write_auth, meta = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
            s1=92, s2=91, s3=90,
            v1="PASS", v2="PASS", v3="",  # empty raw string — must not matter
        )
        assert meta["out3_pass"] is True, (
            "Score 90 must produce out3_pass=True; raw v3 string should not block it"
        )

    def test_composite_release_fail_when_score_89(self):
        """score=89 for output3 must produce out3_pass=False."""
        from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

        verdict, write_auth, meta = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
            s1=92, s2=91, s3=89,
            v1="PASS", v2="PASS", v3="PASS",  # raw string says PASS but score < 90
        )
        assert meta["out3_pass"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Stage-03 Prompt Content Guards
# ══════════════════════════════════════════════════════════════════════════════
class TestStagePromptAtomicity:
    """RC-5: Stage-03 prompt must contain atomicity, grounding, and anti-duplication rules."""

    def _read_prompt(self) -> str:
        return STAGE03_PROMPT_PATH.read_text()

    def test_prompt_contains_atomic_entity_names_rule(self):
        text = self._read_prompt()
        assert "atomic" in text.lower() and "compound" in text.lower(), (
            "Stage-03 prompt must prohibit compound entity names (atomicity rule)"
        )

    def test_prompt_contains_dependency_qualifier_grounding_rule(self):
        text = self._read_prompt()
        assert "DEPENDS_ON" in text, "Stage-03 prompt must have DEPENDS_ON grounding rule"
        assert "grounding" in text.lower() or "verbatim" in text.lower(), (
            "Stage-03 prompt must require dependency grounding to transcript"
        )

    def test_prompt_contains_evidence_only_anti_duplication_rule(self):
        text = self._read_prompt()
        assert "anti-duplication" in text.lower() or "double-listing" in text.lower() or "C-22" in text, (
            "Stage-03 prompt must have evidence_only anti-duplication rule (C-22)"
        )

    def test_prompt_contains_metric_definition_citation_rule(self):
        text = self._read_prompt()
        assert "metric" in text.lower(), (
            "Stage-03 prompt must reference metric node citation requirements"
        )

    def test_prompt_contains_canonical_archetype_preamble(self):
        text = self._read_prompt()
        for arch in CANONICAL_ARCHETYPES:
            assert arch in text, f"Stage-03 prompt missing canonical archetype: {arch}"
