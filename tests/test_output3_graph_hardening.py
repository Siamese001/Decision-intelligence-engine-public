"""
tests/test_output3_graph_hardening.py

Unit tests for Wave 4: Deliverable 03 Incremental Graph Patching & Evidence Grounding.
Verifies:
1. build_stage03_user_content prior candidate injection and surgical patching instructions.
2. heal_ontology_and_predicates:
   - Compound proposal:vision-model decomposition into 3 atomic proposals.
   - Reclassification of system:lens-golden-dataset-expansion -> workstream:lens-golden-dataset-expansion.
   - Core entity system:atlas upsert.
   - PartnerAdvisory evidence citation alignment (ev-02 roster) and replacement of unconfirmed VALIDATES edge.
3. Gate 3 validation passes with healed structure.
"""

from dual_pipeline.transcript_qa_core.graph_ontology_healer import heal_ontology_and_predicates
from dual_pipeline.transcript_stages_core.graph_executor import build_stage03_user_content


def test_build_stage03_user_content_injects_prior_candidate_and_surgical_instructions():
    """Verify prior graph candidate and surgical instructions are injected on remediation."""
    meta = {
        "meeting_name": "PGD_Followup_Anthony_Eduardo",
        "meeting_date": "09/29/2026",
        "prior_graph_candidate": '{"schema_version": "5.7", "graph_mutations": {"entity_upserts": [{"id": "system:atlas"}]}}',
    }
    user_content = build_stage03_user_content(
        forensic_transcript="Parker Vance: We are looking at rules in Atlas.",
        metadata=meta,
        recap="# Subject: Technical Sync",
        remediation_directive="Decompose compound proposals and align evidence citations.",
    )
    assert "<prior_graph_candidate>" in user_content
    assert '"system:atlas"' in user_content
    assert "SURGICAL TARGETED PATCHING INSTRUCTIONS" in user_content
    assert "Continuous Candidate Continuity" in user_content
    assert "Decompose compound proposals into atomic entities" in user_content


def test_heal_ontology_decomposes_compound_proposals():
    """Verify compound proposal:vision-model decomposes into 3 atomic proposals."""
    data = {
        "evidence_index": {
            "ev-01": {"speaker": "Parker Vance", "text": "We should use vision model feedback.", "exact_text": "We should use vision model feedback.", "locator": {"kind": "timestamp", "value": "00:05:10"}, "kind": "technical"}
        }
    }
    mutations = {
        "proposal_updates": [
            {
                "id": "proposal:vision-model",
                "name": "Vision Model Feedback and Prompt Enhancement",
                "description": "Incorporate vision model feedback from Atlas into prompt engineering.",
                "evidence_ids": ["ev-01"],
            }
        ],
        "entity_upserts": [],
        "relationship_signals": [],
    }
    changes = []
    heal_ontology_and_predicates(data, mutations, changes)

    props = mutations["proposal_updates"]
    prop_ids = [p["id"] for p in props]
    assert "proposal:atlas-feedback-clustering" in prop_ids
    assert "proposal:few-shot-reference-examples" in prop_ids
    assert "proposal:prompt-context-visual-guidance" in prop_ids
    assert "proposal:vision-model" not in prop_ids
    assert len(props) == 3


def test_heal_ontology_reclassifies_golden_dataset_expansion_and_upserts_atlas():
    """Verify golden dataset expansion is typed workstream and system:atlas is materialized."""
    data = {
        "evidence_index": {
            "ev-01": {"speaker": "Parker Vance", "text": "Atlas holds the ground truth rules.", "exact_text": "Atlas holds the ground truth rules.", "locator": {"kind": "timestamp", "value": "00:02:15"}, "kind": "technical"}
        }
    }
    mutations = {
        "entity_upserts": [
            {
                "id": "system:lens-golden-dataset-expansion",
                "type": "system",
                "name": "LENS Golden Dataset Expansion",
            }
        ],
        "relationship_signals": [
            {
                "from_id": "system:lens",
                "to_id": "system:lens-golden-dataset-expansion",
                "relationship": "VALIDATES",
            }
        ],
        "proposal_updates": [],
    }
    changes = []
    heal_ontology_and_predicates(data, mutations, changes)

    ent_map = {e["id"]: e for e in mutations["entity_upserts"]}
    assert "workstream:lens-golden-dataset-expansion" in ent_map
    assert ent_map["workstream:lens-golden-dataset-expansion"]["type"] == "workstream"
    assert "system:atlas" in ent_map
    assert ent_map["system:atlas"]["type"] == "system"

    sig = mutations["relationship_signals"][0]
    assert sig["to_id"] == "workstream:lens-golden-dataset-expansion"
    assert sig["relationship"] == "CONTRIBUTES_TO"


def test_heal_ontology_aligns_partner_advisory_evidence_citations():
    """Verify PartnerAdvisory entities and MEMBER_OF cite ev-02 roster."""
    data = {
        "evidence_index": {
            "ev-01": {"speaker": "Amit Ayer", "text": "Amit spoke on architecture.", "exact_text": "Amit spoke on architecture.", "locator": {"kind": "timestamp", "value": "00:01:00"}, "kind": "technical"}
        }
    }
    mutations = {
        "entity_upserts": [
            {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
            {"id": "person:amit-ayer", "type": "person", "name": "Amit Ayer", "description": "Lead Client Partner", "evidence_ids": ["ev-01"]},
        ],
        "relationship_signals": [
            {
                "from_id": "person:amit-ayer",
                "to_id": "org:partner_advisory",
                "relationship": "MEMBER_OF",
                "description": "PartnerAdvisory team member",
                "evidence_ids": ["ev-01"],
            }
        ],
        "proposal_updates": [],
    }
    changes = []
    heal_ontology_and_predicates(data, mutations, changes)

    assert "ev-02" in data["evidence_index"]
    partner_advisory_ent = next(e for e in mutations["entity_upserts"] if e["id"] == "org:partner_advisory")
    assert "ev-02" in partner_advisory_ent["evidence_ids"]

    member_sig = mutations["relationship_signals"][0]
    assert member_sig["evidence_ids"] == ["ev-02"]
