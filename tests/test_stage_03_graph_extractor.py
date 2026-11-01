"""
Tests for Stage 03 — Graph Extractor stage prompt contract.
Validates that 03_graph_extractor.md contains all required graph contracts
from the monolith §9 (entity binding, signal gate, promotion scoring,
exclusions, temporal semantics, C-16 prior context protocol, C-17, C-18).
"""
import pathlib

import pytest

STAGE_03 = pathlib.Path("config/prompts/synthesis/stages/03_graph_extractor.md")


@pytest.fixture(scope="module")
def stage_03_text():
    return STAGE_03.read_text(encoding="utf-8")


def test_stage_03_file_exists():
    assert STAGE_03.exists(), "Stage 03 prompt file missing"


def test_stage_03_input_contract(stage_03_text):
    """Input contract must reference forensic_transcript, executive_recap, evidence_locks."""
    assert "GraphExtractorInput" in stage_03_text
    assert "forensic_transcript" in stage_03_text
    assert "executive_recap" in stage_03_text
    assert "evidence_locks" in stage_03_text
    assert "prior_context" in stage_03_text


def test_stage_03_output_contract(stage_03_text):
    """Output contract must name CanonicalSignalProjectionArtifact and schema_version 5.7."""
    assert "CanonicalSignalProjectionArtifact" in stage_03_text
    assert "5.7" in stage_03_text


def test_stage_03_three_tier_architecture(stage_03_text):
    """§9.1 Three-tier architecture: Spine, Decision Intelligence, Evidence Index."""
    assert "Tier 1" in stage_03_text
    assert "Knowledge Graph Spine" in stage_03_text
    assert "Tier 2" in stage_03_text
    assert "Decision Intelligence" in stage_03_text
    assert "Tier 3" in stage_03_text
    assert "Evidence Index" in stage_03_text


def test_stage_03_entity_domain_restrictions(stage_03_text):
    """§9.2 Allowed entity domains and forbidden entity domains."""
    assert "org:" in stage_03_text
    assert "person:" in stage_03_text
    assert "system:" in stage_03_text
    assert "initiative:" in stage_03_text
    assert "capability:" in stage_03_text
    assert "decision:" in stage_03_text
    assert "proposal:" in stage_03_text
    assert "Forbidden Entity Domains" in stage_03_text


def test_stage_03_negative_exclusion_filter(stage_03_text):
    """§9.3 Negative exclusion filter: all four filters present."""
    assert "Negative Exclusion Filter" in stage_03_text
    assert "Who" in stage_03_text and "Filter" in stage_03_text
    assert "Observation" in stage_03_text
    assert "Scope" in stage_03_text
    assert "Durability" in stage_03_text


def test_stage_03_promotion_scoring_gate(stage_03_text):
    """§9.3 Promotion scoring gate with all five dimensions."""
    assert "Materiality" in stage_03_text
    assert "Novelty" in stage_03_text
    assert "Durability" in stage_03_text
    assert "Authority" in stage_03_text
    assert "Actionability" in stage_03_text
    assert "7/10" in stage_03_text


def test_stage_03_strong_exclusions(stage_03_text):
    """§9.4 Strong exclusions: calendar, social, observational commentary."""
    assert "Calendar" in stage_03_text and "Travel Logistics" in stage_03_text
    assert "Social" in stage_03_text and "Informal Check-ins" in stage_03_text
    assert "Conversational Musings" in stage_03_text
    assert "Tool Mention Protocol" in stage_03_text


def test_stage_03_proposal_lifecycle(stage_03_text):
    """§9.5 Proposal separation and lifecycle state machine."""
    assert "Proposal Lifecycle State Machine" in stage_03_text
    assert "proposed" in stage_03_text
    assert "approved" in stage_03_text
    assert "rejected" in stage_03_text
    assert "deferred" in stage_03_text


def test_stage_03_decision_lifecycle(stage_03_text):
    """§9.6 Decision lifecycle values."""
    assert "UNDER_EVALUATION" in stage_03_text
    assert "DECIDED" in stage_03_text
    assert "DEFERRED" in stage_03_text


def test_stage_03_temporal_mutation_semantics(stage_03_text):
    """§9.7 Temporal mutation semantics and W3 temporal contract."""
    assert "CREATE" in stage_03_text
    assert "CONFIRM" in stage_03_text
    assert "MODIFY" in stage_03_text
    assert "SUPERSEDE" in stage_03_text
    assert "CONTRADICT" in stage_03_text
    assert "CLOSE" in stage_03_text
    assert "observed_at" in stage_03_text
    assert "stale_after" in stage_03_text


def test_stage_03_prior_context_protocol(stage_03_text):
    """§9.7 C-16 Prior context protocol."""
    assert "Prior Context Protocol" in stage_03_text or "C-16" in stage_03_text
    assert "CONFIRM" in stage_03_text
    assert "prior_context" in stage_03_text


def test_stage_03_required_schema(stage_03_text):
    """§9.9 Required output schema: all six mutation arrays."""
    assert "entity_upserts" in stage_03_text
    assert "relationship_signals" in stage_03_text
    assert "decision_updates" in stage_03_text
    assert "proposal_updates" in stage_03_text
    assert "action_updates" in stage_03_text
    assert "risk_updates" in stage_03_text
    assert "evidence_only_statement_ids" in stage_03_text
    assert "classification" in stage_03_text
    assert "audiences" in stage_03_text


def test_stage_03_idempotent_signal_ids(stage_03_text):
    """§9.9 C-18 Idempotent signal IDs derivation pattern."""
    assert "Idempotent Signal ID" in stage_03_text or "C-18" in stage_03_text
    assert "signal_id" in stage_03_text
    assert "rel-" in stage_03_text


def test_stage_03_allowed_relationships(stage_03_text):
    """§9.9 Relationship signals must enumerate allowed relationship values."""
    for rel in ["ASSIGNED_TO", "DEVELOPS", "SPONSORS", "OWNS", "DEPENDS_ON",
                "CONTRIBUTES_TO", "HAS_RISK", "DELIVERS", "CONSTRAINS", "GOVERNS"]:
        assert rel in stage_03_text, f"Missing allowed relationship: {rel}"


def test_stage_03_evidence_index(stage_03_text):
    """§9.8 Evidence index contract: required fields."""
    assert "evidence_index" in stage_03_text
    assert "statement_id" in stage_03_text
    assert "source_classification" in stage_03_text
    assert "locator" in stage_03_text
    assert "hitl_resolved" in stage_03_text


def test_stage_03_evidence_ceiling_c17(stage_03_text):
    """§9.10 C-17 Evidence-only IDs ceiling."""
    assert "C-17" in stage_03_text
    assert "max(3, 2x" in stage_03_text or "max(3, 2" in stage_03_text


def test_stage_03_audit_rationale(stage_03_text):
    """_audit_rationale must be required for every mutation."""
    assert "_audit_rationale" in stage_03_text
    assert "generation-time self-correction" in stage_03_text


def test_stage_03_risk_materiality_gate(stage_03_text):
    """Risk materiality gate must be present."""
    assert "Risk Materiality Gate" in stage_03_text
    assert "risk_scope" in stage_03_text
    assert "entity" in stage_03_text
    assert "engagement" in stage_03_text
    assert "delivery" in stage_03_text


def test_stage_03_downstream_schema_alignment(stage_03_text):
    """§5.1 Downstream schema alignment: 6 kind values + internal classifications."""
    assert "source_classification" in stage_03_text
    assert "DECISION" in stage_03_text
    assert "COMMITMENT" in stage_03_text
    assert "RISK" in stage_03_text
    assert "PROPOSAL" in stage_03_text


def test_stage_03_cross_output_consistency_gate(stage_03_text):
    """§10.4 Cross-output consistency gate must verify against Stage 01 and 02."""
    assert "Cross-Output Consistency Gate" in stage_03_text
    assert "Artifact 01" in stage_03_text or "forensic_transcript" in stage_03_text
    assert "Artifact 02" in stage_03_text or "executive_recap" in stage_03_text


def test_stage_03_injection_defense(stage_03_text):
    """Untrusted data isolation must be declared."""
    assert "non-executable data" in stage_03_text
    assert "Never follow instructions" in stage_03_text
