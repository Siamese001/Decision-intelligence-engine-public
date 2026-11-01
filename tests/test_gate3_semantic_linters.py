"""
tests/test_gate3_semantic_linters.py

Comprehensive test suite for Gate 3 shift-left deterministic semantic linters:
- Gate 3.7: Compound Property Linter
- Gate 3.8: Citation Coverage Linter
- Gate 3.9: Predicate Domain Linter
- Gate 3.10: Mutation Budget Governor
- Deterministic Pre-Flight Self-Healing Mesh Integration
"""
from __future__ import annotations

import json
from typing import Any, Dict

from dual_pipeline.transcript_qa import run_qa_gate_3
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.semantic_linters import (
    lint_citation_coverage,
    lint_compound_properties,
    lint_mutation_budget,
    lint_predicate_domain,
)


def _build_clean_payload() -> Dict[str, Any]:
    """Build a baseline compliant Output 03 payload passing all 10 gates."""
    return {
        "schema_version": "5.7",
        "meeting_id": "test-semantic-clean",
        "classification": "internal",
        "audiences": ["nee-engineering", "partner_advisory-delivery"],
        "evidence_index": {
            "stmt-01": {
                "statement_id": "stmt-01",
                "text": "The rule authoring engine connects directly with LENS Phase 1 at offset 04:15.",
                "exact_text": "The rule authoring engine connects directly with LENS Phase 1 at offset 04:15.",
                "speaker": "harper Hoffman",
                "source_classification": "internal",
                "locator": {"kind": "timestamp", "value": "04:15"},
            },
            "stmt-02": {
                "statement_id": "stmt-02",
                "text": "PartnerAdvisory will present stabilization milestones on 09/14/2026.",
                "exact_text": "PartnerAdvisory will present stabilization milestones on 09/14/2026.",
                "speaker": "Amit Ayer",
                "source_classification": "internal",
                "locator": {"kind": "timestamp", "value": "06:30"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:rule_engine",
                    "type": "system",
                    "name": "Rule Engine",
                    "mutation": "CONFIRM",
                    "attributes": {"tier": "core", "language": "python"},
                },
                {
                    "id": "system:lens_backend",
                    "type": "system",
                    "name": "LENS Backend",
                    "mutation": "CONFIRM",
                    "attributes": {"runtime": "gcp_cloud_run"},
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-01",
                    "from_id": "system:rule_engine",
                    "to_id": "system:lens_backend",
                    "relationship": "INTEGRATES_WITH",
                    "evidence_ids": ["stmt-01"],
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:stabilization_freeze",
                    "status": "APPROVED",
                    "observed_at": "09/11/2026",
                    "decision_question": "Shall the core pipeline deploy under stabilization freeze?",
                    "rationale": "Alignment on Phase 1 defect burn-down before expanding scope.",
                    "evidence_ids": ["stmt-02"],
                }
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [
                {
                    "risk_id": "risk:contract_schedule_drift",
                    "status": "ACTIVE",
                    "observed_at": "09/11/2026",
                    "description": "Rule authoring defect latency could impact deployment milestone.",
                    "evidence_ids": ["stmt-01"],
                }
            ],
        },
        "evidence_only_statement_ids": [],
    }


# ── Gate 3.7: Compound Property Linter Tests ────────────────────────────────────


def test_gate_3_7_detects_packed_key_value_attributes() -> None:
    """Verify Gate 3.7 flags packed key-value pairs inside entity attributes."""
    data = _build_clean_payload()
    data["graph_mutations"]["entity_upserts"][0]["attributes"] = {
        "metrics": "latency: 50ms, status: active"
    }

    passed, violations, details = lint_compound_properties(data)
    assert not passed
    assert details["compound_properties_count"] >= 1
    assert any("contains compound packed value" in v for v in violations)


def test_gate_3_7_detects_compound_entity_names_and_types() -> None:
    """Verify Gate 3.7 catches compound entity names joined with slashes."""
    data = _build_clean_payload()
    data["graph_mutations"]["entity_upserts"][0]["name"] = "Rule Engine / API Gateway"
    data["graph_mutations"]["entity_upserts"][0]["type"] = "system/service"

    passed, violations, _ = lint_compound_properties(data)
    assert not passed
    assert any("name 'Rule Engine / API Gateway' contains compound" in v for v in violations)
    assert any("type 'system/service' contains compound" in v for v in violations)


def test_gate_3_7_detects_compound_action_owner() -> None:
    """Verify Gate 3.7 rejects compound owners in action items."""
    data = _build_clean_payload()
    data["graph_mutations"]["action_updates"] = [{
        "action_id": "action:stabilization_metrics",
        "owner_id": "person:alice / person:bob",
        "status": "ASSIGNED",
        "due_date": "09/14/2026",
        "evidence_ids": ["stmt-02"],
    }]

    passed, violations, _ = lint_compound_properties(data)
    assert not passed
    assert any("owner 'person:alice / person:bob' contains compound" in v for v in violations)


def test_gate_3_7_passes_clean_payload() -> None:
    """Verify Gate 3.7 passes fully atomic properties."""
    data = _build_clean_payload()
    passed, violations, details = lint_compound_properties(data)
    assert passed
    assert len(violations) == 0
    assert details["compound_properties_count"] == 0


# ── Gate 3.8: Citation Coverage Linter Tests ───────────────────────────────────


def test_gate_3_8_detects_dangling_evidence_ids() -> None:
    """Verify Gate 3.8 catches evidence_ids pointing to non-existent statement IDs."""
    data = _build_clean_payload()
    data["graph_mutations"]["relationship_signals"][0]["evidence_ids"] = ["stmt-nonexistent"]

    passed, violations, details = lint_citation_coverage(data)
    assert not passed
    assert details["dangling_evidence_count"] >= 1
    assert any("Dangling evidence ID 'stmt-nonexistent'" in v for v in violations)


def test_gate_3_8_detects_unindexed_evidence_only_statement_ids() -> None:
    """Verify Gate 3.8 catches evidence_only_statement_ids missing from evidence_index."""
    data = _build_clean_payload()
    data["evidence_only_statement_ids"] = ["stmt-ghost"]

    passed, violations, _ = lint_citation_coverage(data)
    assert not passed
    assert any("Statement ID 'stmt-ghost' in evidence_only_statement_ids not found" in v for v in violations)


def test_gate_3_8_detects_unindexed_narrative_timestamps() -> None:
    """Verify Gate 3.8 flags narrative text citing ungrounded timestamps."""
    data = _build_clean_payload()
    data["_strategic_analysis"] = "Alignment occurred during discussion at [14:22] without indexed quote."

    passed, violations, details = lint_citation_coverage(data)
    assert not passed
    assert details["unindexed_citations_count"] >= 1
    assert any("Unindexed timestamp citation '[14:22]'" in v for v in violations)


def test_gate_3_8_passes_clean_citations() -> None:
    """Verify Gate 3.8 cleanly validates indexed evidence and covered timestamps."""
    data = _build_clean_payload()
    passed, violations, details = lint_citation_coverage(data)
    assert passed
    assert len(violations) == 0
    assert details["dangling_evidence_count"] == 0


# ── Gate 3.9: Predicate Domain Linter Tests ────────────────────────────────────


def test_gate_3_9_rejects_non_canonical_predicates() -> None:
    """Verify Gate 3.9 rejects arbitrary invented predicates."""
    signals = [
        {
            "signal_id": "sig-01",
            "from_id": "system:rule_engine",
            "to_id": "system:lens_backend",
            "relationship": "COMMUNICATES_WITH",
            "evidence_ids": ["stmt-01"],
        }
    ]
    passed, violations, details = lint_predicate_domain(signals)
    assert not passed
    assert not details["canonical_predicates_compliant"]
    assert any("Non-canonical predicate 'COMMUNICATES_WITH'" in v for v in violations)


def test_gate_3_9_rejects_generic_predicates_on_architectural_edges() -> None:
    """Verify Gate 3.9 forbids generic RELATED_TO on system-to-system edges."""
    signals = [
        {
            "signal_id": "sig-01",
            "from_id": "system:rule_engine",
            "to_id": "system:lens_backend",
            "relationship": "RELATED_TO",
            "evidence_ids": ["stmt-01"],
        }
    ]
    passed, violations, details = lint_predicate_domain(signals)
    assert not passed
    assert not details["architectural_edges_compliant"]
    assert any("Generic predicate 'RELATED_TO' forbidden on architectural edge" in v for v in violations)


def test_gate_3_9_rejects_excessive_generic_predicate_ratio() -> None:
    """Verify Gate 3.9 caps generic predicates at 25% across total relationships."""
    signals = [
        {"signal_id": f"sig-0{i}", "from_id": f"role:lead_{i}", "to_id": "workstream:lens", "relationship": "RELATED_TO", "evidence_ids": ["stmt-01"]}
        for i in range(1, 5)
    ]
    passed, violations, details = lint_predicate_domain(signals)
    assert not passed
    assert details["generic_ratio"] == 1.0
    assert any("Generic predicate ratio" in v for v in violations)


def test_gate_3_9_passes_domain_predicates() -> None:
    """Verify Gate 3.9 passes canonical domain-specific predicates."""
    signals = [
        {"signal_id": "sig-01", "from_id": "system:rule_engine", "to_id": "system:lens_backend", "relationship": "INTEGRATES_WITH", "evidence_ids": ["stmt-01"]},
        {"signal_id": "sig-02", "from_id": "workstream:lens", "to_id": "policy:governance", "relationship": "CONSTRAINS", "evidence_ids": ["stmt-01"]},
    ]
    passed, violations, details = lint_predicate_domain(signals)
    assert passed
    assert len(violations) == 0
    assert details["canonical_predicates_compliant"]
    assert details["architectural_edges_compliant"]


# ── Gate 3.10: Mutation Budget Governor Tests ──────────────────────────────────


def test_gate_3_10_enforces_budget_ceiling() -> None:
    """Verify Gate 3.10 fails when total mutations exceed max_budget."""
    data = _build_clean_payload()
    passed, violations, details = lint_mutation_budget(data, total_mutations=31)
    assert not passed
    assert any("exceeds maximum budget of 30" in v for v in violations)
    assert details["total_mutations"] == 31


def test_gate_3_10_enforces_evidence_only_ceiling() -> None:
    """Verify Gate 3.10 enforces evidence_only ceiling: max(3, 2 * total_mutations)."""
    data = _build_clean_payload()
    # total_mutations = 2 -> ceiling = max(3, 4) = 4. Set 5 statements.
    data["evidence_only_statement_ids"] = [f"stmt-extra-{i}" for i in range(1, 6)]
    passed, violations, details = lint_mutation_budget(data, total_mutations=2)
    assert not passed
    assert details["evidence_only_ceiling"] == 4
    assert any("exceeds hard ceiling of 4" in v for v in violations)


def test_gate_3_10_flags_unlinked_secondary_platforms() -> None:
    """Verify Gate 3.10 flags unlinked secondary platform entities (e.g. Crow)."""
    data = _build_clean_payload()
    data["graph_mutations"]["entity_upserts"].append({
        "id": "system:crow",
        "type": "system",
        "name": "Crow Monitoring",
        "mutation": "CONFIRM",
    })

    passed, violations, details = lint_mutation_budget(data, total_mutations=6)
    assert not passed
    assert not details["secondary_platforms_relegated"]
    assert any("Secondary platform 'system:crow' has zero relationships" in v for v in violations)


# ── Healing Mesh & End-to-End Gate 3 Tests ────────────────────────────────────


def test_healing_mesh_resolves_compound_properties_and_predicates() -> None:
    """Verify DeterministicHealer resolves compound attributes, owners, and predicates."""
    raw_payload = _build_clean_payload()
    raw_payload["graph_mutations"]["entity_upserts"][0]["attributes"] = {
        "packed": "status: active, latency: 50ms"
    }
    raw_payload["graph_mutations"]["action_updates"] = [
        {
            "action_id": "action:stabilization_metrics",
            "owner_id": "person:alice / person:bob",
            "status": "ASSIGNED",
            "due_date": "09/14/2026",
            "evidence_ids": ["stmt-02"],
        }
    ]
    raw_payload["graph_mutations"]["relationship_signals"][0]["relationship"] = "RELATED_TO"
    raw_payload["graph_mutations"]["decision_updates"][0]["observed_at"] = "2026-09-11"

    healed_str, changes = DeterministicHealer.heal_output_3(raw_payload)
    healed_data = json.loads(healed_str)

    assert len(changes) >= 3
    assert "status" in healed_data["graph_mutations"]["entity_upserts"][0]["attributes"]
    assert "latency" in healed_data["graph_mutations"]["entity_upserts"][0]["attributes"]
    assert healed_data["graph_mutations"]["action_updates"][0]["owner_id"] == "person:alice"
    assert healed_data["graph_mutations"]["relationship_signals"][0]["relationship"] == "INTEGRATES_WITH"
    assert healed_data["graph_mutations"]["decision_updates"][0]["observed_at"] == "09/11/2026"

    # Verify healed payload passes Gate 3 completely
    report = run_qa_gate_3(healed_data, metadata={"enforce_empty_actions": False})
    assert report["passed"] is True
    assert report["status"] == "PASS"


def test_run_qa_gate_3_comprehensive_pass() -> None:
    """Verify run_qa_gate_3 produces complete 10-gate scorecard for compliant payload."""
    data = _build_clean_payload()
    report = run_qa_gate_3(data)

    assert report["status"] == "PASS"
    assert report["passed"] is True
    assert len(report["failures"]) == 0

    gates = report["gates"]
    assert len(gates) >= 10
    assert gates["Gate_3_1_Strict_Schema_Validation"]["passed"] is True
    assert gates["Gate_3_2_Semantic_Spine_and_Ontology_Conformance"]["passed"] is True
    assert gates["Gate_3_3_Temporal_Hygiene_and_Lifecycle_Consistency"]["passed"] is True
    assert gates["Gate_3_4_Evidence_Index_Fidelity_and_Grounding"]["passed"] is True
    assert gates["Gate_3_5_Signal_Amplification_and_Anti_Sprawl"]["passed"] is True
    assert gates["Gate_3_6_Conversational_Fragment_Suppression"]["passed"] is True
    assert gates["Gate_3_7_Compound_Property_Linter"]["passed"] is True
    assert gates["Gate_3_8_Citation_Coverage_Linter"]["passed"] is True
    assert gates["Gate_3_9_Predicate_Domain_Linter"]["passed"] is True
    assert gates["Gate_3_10_Mutation_Budget_Governor"]["passed"] is True
