"""Wave 3 adversarial and edge-case tests for manifest synthesis and publication coverage verifier."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
)
from dual_pipeline.canonical_pipeline.manifest_synthesis import build_manifest_from_output_03
from dual_pipeline.canonical_pipeline.publication_verifier import verify_fact_coverage_in_graph


def test_speaker_role_verified_via_assertion_description_and_attr_index(tmp_path: Path) -> None:
    """Exact production regression: verify SPEAKER_ROLE resolves via HAS_ROLE_IN assertion, node_attribute_index, and node.description when properties_json is absent."""
    db_path = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT,
            description TEXT, node_type TEXT, active INTEGER DEFAULT 1
        );
        CREATE TABLE node_attribute_index (
            node_id TEXT, attribute_key TEXT, attribute_value TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
            object_node_id TEXT, object_literal TEXT, scope_text TEXT,
            currentness_state TEXT
        );
        """
    )
    # Person 1: role only in HAS_ROLE_IN assertion pointing to role node
    conn.execute("INSERT INTO node VALUES ('entity-brien', 'Speaker_Alpha', 'Speaker_Alpha', '', 'stakeholder', 1)")
    conn.execute("INSERT INTO node VALUES ('entity-role-arch', 'Lead Architect', 'Lead Architect', '', 'role_group', 1)")
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-role-1', 'entity-brien', 'HAS_ROLE_IN', 'entity-role-arch', NULL, 'Speaker_Alpha HAS_ROLE_IN Lead Architect.', 'current')"
    )
    # Person 2: role only in node_attribute_index
    conn.execute("INSERT INTO node VALUES ('entity-ninan', 'Speaker_Beta', 'Speaker_Beta', '', 'stakeholder', 1)")
    conn.execute("INSERT INTO node_attribute_index VALUES ('entity-ninan', 'role', 'Delivery Director')")
    # Person 3: role only in node.description
    conn.execute(
        "INSERT INTO node VALUES ('entity-alex', 'Alex', 'Alex', 'Alex serves as Platform Security Lead.', 'stakeholder', 1)"
    )
    conn.commit()
    conn.close()

    manifest = EvidenceCoverageManifest(
        manifest_id="cov-adv-01",
        run_id="run-adv-01",
        source_digest="a" * 64,
        items=[
            EvidenceItem(
                item_id="role-brien",
                item_type=ItemType.SPEAKER_ROLE.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": "speakers[0]"},
                disposition_reason="Grounded in Output 03",
                proposed_fact={"subject": "Speaker_Alpha", "predicate": "ROLE", "object": "Lead Architect"},
            ),
            EvidenceItem(
                item_id="role-ninan",
                item_type=ItemType.SPEAKER_ROLE.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": "speakers[1]"},
                disposition_reason="Grounded in Output 03",
                proposed_fact={"subject": "Speaker_Beta", "predicate": "ROLE", "object": "Delivery Director"},
            ),
            EvidenceItem(
                item_id="role-alex",
                item_type=ItemType.SPEAKER_ROLE.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": "speakers[2]"},
                disposition_reason="Grounded in Output 03",
                proposed_fact={"subject": "Alex", "predicate": "ROLE", "object": "Platform Security Lead"},
            ),
        ],
    )
    ok, violations, receipt = verify_fact_coverage_in_graph(
        graph_db=db_path,
        manifest=manifest,
        proposal_digest="b" * 64,
        activated_generation_id="gen-adv-01",
    )
    assert ok is True, f"Unexpected violations: {violations}"
    assert receipt["coverage_summary"]["committed_count"] == 3
    assert receipt["coverage_summary"]["missing_count"] == 0


def test_boundary_and_malformed_manifest_inputs(tmp_path: Path) -> None:
    """Boundary payload and malformed input handling for build_manifest_from_output_03 and verify_fact_coverage_in_graph."""
    # Boundary: empty / non-dict mutations and malformed list elements
    malformed_output_03 = {
        "meeting_id": "mtg-empty",
        "graph_mutations": {
            "entity_upserts": [None, "bad", {"name": ""}, {"id": "workstream:core", "name": "Core", "type": "workstream"}],
            "relationship_signals": [None, {"from_id": "workstream:core", "to_id": "", "predicate": "USES"}],
            "assertion_updates": [None, {"claim": ""}],
            "decision_updates": ["not-a-dict", {"decision_text": "Approve Wave 3"}],
            "action_updates": [{"action_text": "Verify coverage"}],
        },
        "speakers": [None, {"name": "Speaker_Alpha", "role": ""}],
    }
    manifest = build_manifest_from_output_03(malformed_output_03, run_id="run-malformed")
    # Should include valid entity + decision + action (bound to default subject 'workstream:core') without crashing
    assert len(manifest.items) == 3

    # Boundary: missing graph file returns (False, violations, {}) cleanly
    ok, errs, rec = verify_fact_coverage_in_graph(
        graph_db=tmp_path / "nonexistent.sqlite",
        manifest=manifest,
        proposal_digest="0" * 64,
        activated_generation_id="gen-0",
    )
    assert ok is False
    assert errs
    assert rec == {}
