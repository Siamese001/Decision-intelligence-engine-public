"""Wave 2 adversarial: a write that claims success without physical proof must fail closed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.end_to_end.test_wave1_mutation_hardening_e2e as _wave1_fixture
from dual_pipeline.graph_mutation_proof import GraphMutationProofError
from dual_pipeline.graph_writer import GovernedGraphWriter


@pytest.fixture()
def fixture_case():
    case = _wave1_fixture.Wave1MutationHardeningE2ETests("test_e2e_mutation_wal_checkpoint_and_hash_change")
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


def _fake_commit(operations):
    def _inner(self, change_set, *, fail_after_operations=None):
        return {
            "status": "COMMITTED",
            "operation_count": len(operations),
            "operations": operations,
            "change_set_id": change_set["change_set_id"],
        }
    return _inner


def test_claimed_commit_with_no_disk_change_raises(fixture_case, monkeypatch):
    graph = fixture_case.graph_path
    monkeypatch.setattr(GovernedGraphWriter, "_apply_committed", _fake_commit([{"target_id": "ghost-assertion"}]))
    with pytest.raises(GraphMutationProofError) as err:
        GovernedGraphWriter(graph).apply(fixture_case._make_change_set("op-wave2-adv-1", "as-adv-1"))
    failed = {c["check"] for c in err.value.receipt["checks"] if c["status"] == "FAIL"}
    assert {"file_changed", "raw_bytes", "readback", "row_delta"} <= failed
    assert "PHYSICAL_PROOF_FAILED" in str(err.value)
    # Failure receipt is still written for audit.
    assert list((graph.parent / "graph_mutation_proofs").glob("*.json"))


def test_empty_operation_list_is_not_applicable_not_pass(fixture_case, monkeypatch):
    graph = fixture_case.graph_path
    monkeypatch.setattr(GovernedGraphWriter, "_apply_committed", _fake_commit([]))
    result = GovernedGraphWriter(graph).apply(fixture_case._make_change_set("op-wave2-adv-2", "as-adv-2"))
    assert result["physical_proof"]["overall"] == "NOT_APPLICABLE"
    assert not (graph.parent / "graph_mutation_proofs").exists()


def test_real_write_for_other_ids_cannot_vouch_for_claimed_ids(fixture_case, monkeypatch):
    """Writer really commits one row but its result claims a different target: proof must fail."""
    graph = fixture_case.graph_path
    real_apply = GovernedGraphWriter._apply_committed

    def _lying_apply(self, change_set, *, fail_after_operations=None):
        result = real_apply(self, change_set, fail_after_operations=fail_after_operations)
        result["operations"] = [{"target_id": "claimed-but-never-written"}]
        return result

    monkeypatch.setattr(GovernedGraphWriter, "_apply_committed", _lying_apply)
    with pytest.raises(GraphMutationProofError) as err:
        GovernedGraphWriter(graph).apply(fixture_case._make_change_set("op-wave2-adv-3", "as-adv-3"))
    failed = {c["check"] for c in err.value.receipt["checks"] if c["status"] == "FAIL"}
    assert "raw_bytes" in failed and "readback" in failed
    assert "file_changed" not in failed  # the file really did change; the claim did not match


def test_audit_event_count_mismatch_fails_row_delta(fixture_case, monkeypatch):
    """Writer commits one real operation but reports two: audit_event delta must catch it."""
    graph = fixture_case.graph_path
    real_apply = GovernedGraphWriter._apply_committed

    def _inflated_apply(self, change_set, *, fail_after_operations=None):
        result = real_apply(self, change_set, fail_after_operations=fail_after_operations)
        result["operations"].append(dict(result["operations"][0]))
        result["operation_count"] = 2
        return result

    monkeypatch.setattr(GovernedGraphWriter, "_apply_committed", _inflated_apply)
    with pytest.raises(GraphMutationProofError) as err:
        GovernedGraphWriter(graph).apply(fixture_case._make_change_set("op-wave2-adv-4", "as-adv-4"))
    delta = next(c for c in err.value.receipt["checks"] if c["check"] == "row_delta")
    assert delta["status"] == "FAIL"
    assert delta["detail"]["expected"] == {"audit_event": 2}
    assert delta["detail"]["actual"] == {"audit_event": 1}
    saved = json.loads(next(Path(graph.parent / "graph_mutation_proofs").glob("*.json")).read_text())
    assert saved["overall"] == "FAIL"


def test_inline_promotion_proves_canonical_node_and_relationship_and_deltas(fixture_case):
    """Approved relationship and internal entity promotion must sync node.status='canonical' and pass canonical readback probes."""
    from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file
    from dual_pipeline.contracts import create_artifact, seal_artifact

    graph = fixture_case.graph_path
    conn = connect_sqlite(graph)
    conn.execute("ALTER TABLE node ADD COLUMN node_type TEXT DEFAULT 'system'")
    conn.execute("ALTER TABLE node ADD COLUMN display_name TEXT DEFAULT 'Core System'")
    conn.execute("ALTER TABLE node ADD COLUMN description TEXT DEFAULT 'System node'")
    conn.execute("ALTER TABLE node ADD COLUMN status TEXT DEFAULT 'canonical'")
    conn.execute("ALTER TABLE node ADD COLUMN updated_at TEXT DEFAULT '2026-09-27T00:00:00Z'")
    conn.execute(
        "CREATE TABLE node_state (node_id TEXT PRIMARY KEY, lifecycle_state TEXT, validation_state TEXT, "
        "currentness_state TEXT, approval_state TEXT, rag_state TEXT, resolution_state TEXT, "
        "retrieval_eligibility TEXT, publication_state TEXT, review_state TEXT, legacy_status_text TEXT, "
        "intentional_orphan INTEGER, intentional_orphan_reason TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE node_type_registry (node_type_id TEXT PRIMARY KEY, canonical_name TEXT, definition TEXT, "
        "parent_type_id TEXT, required_properties_json TEXT, optional_properties_json TEXT, identity_rule TEXT, "
        "example_node_id TEXT, registry_state TEXT, replacement_type_id TEXT, retrieval_use TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO node_type_registry VALUES ('system', 'System', 'System type', NULL, '[]', '[]', 'rule', NULL, 'active', NULL, 'eligible', '2026-09-27T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO node VALUES ('node-cand-01', 'Core System', 'system', 1, '2026-09-27T00:00:00Z', 'system', 'Candidate System', 'Desc', 'candidate internal entity awaiting human review', '2026-09-27T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO node_state VALUES ('node-cand-01', 'active', 'needs_review', 'current', 'candidate', 'amber', 'resolved', 'eligible', 'internal', 'reviewed', 'candidate internal entity awaiting human review', 0, NULL, '2026-09-27T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    base_hash = sha256_file(graph)
    rel_id = "as-promo-rel-01"
    rel_payload = {
        "candidate": {
            "candidate_id": "cand-rel-01",
            "candidate_type": "assertion",
            "normalized_text": "Candidate System integrates with Core System",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_ids": ["ev-e2e-001"],
            "extraction_confidence": 0.95,
            "materiality": "high",
        },
        "subject_node_id": "node-cand-01",
        "predicate": "INTEGRATES_WITH",
        "object_node_id": "node-sys-01",
        "disposition": "APPROVED",
        "retrieval_audiences": ["engineering"],
    }
    ops = [
        {
            "operation_id": "op-rel-01",
            "sequence": 1,
            "operation": "add_relationship",
            "target_type": "relationship",
            "target_id": rel_id,
            "candidate_id": "cand-rel-01",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_ids": ["ev-e2e-001"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(rel_payload),
        },
    ]
    cs = seal_artifact(
        create_artifact(
            "graph-change-set",
            "cs-inline-promo",
            change_set_id="cs-inline-promo",
            run_id="run-e2e-001",
            trace_id="c" * 32,
            approval_id="app-e2e-001",
            status="approved",
            base_graph_snapshot="snapshot-e2e-01",
            base_graph_hash=base_hash,
            transaction_required=True,
            rollback_required=True,
            operations=ops,
            extensions={
                "com.partner_advisory.nee.source": {
                    "source_id": "source-e2e-01",
                    "source_version_id": "sv-e2e-01",
                    "name": "E2E Source",
                    "package_digest": "b" * 64,
                    "authority_rank": 7,
                    "source_type": "governed_internal",
                },
                "com.partner_advisory.nee.operation-payloads": {
                    "op-rel-01": rel_payload,
                },
            },
        )
    )
    result = GovernedGraphWriter(graph).apply(cs)
    proof_summary = result["physical_proof"]
    assert proof_summary["overall"] == "PASS"
    deltas = proof_summary["canonical_deltas"]
    assert deltas["canonical_nodes"] == 1
    assert deltas["approved_node_states"] == 1
    assert deltas["canonical_assertions"] == 1
    assert deltas["canonical_relationships"] == 1

    conn = connect_sqlite(graph, readonly=True)
    node_status = conn.execute("SELECT status FROM node WHERE node_id='node-cand-01'").fetchone()[0]
    ns_state = conn.execute("SELECT approval_state FROM node_state WHERE node_id='node-cand-01'").fetchone()[0]
    conn.close()
    assert node_status == "canonical"
    assert ns_state == "approved"


def test_approved_relationship_left_as_candidate_fails_promotion_readback(fixture_case, monkeypatch):
    """If an approved relationship commits as candidate without canonical promotion, readback proof must fail closed."""
    from dual_pipeline.contracts import seal_artifact
    from dual_pipeline.core import digest

    graph = fixture_case.graph_path
    real_apply = GovernedGraphWriter._apply_committed
    cs = fixture_case._make_change_set("op-wave2-adv-5", "as-adv-5")
    cs["operations"][0]["operation"] = "add_relationship"
    cs["operations"][0]["target_type"] = "relationship"
    payload = cs["extensions"]["com.partner_advisory.nee.operation-payloads"]["op-wave2-adv-5"]
    payload["disposition"] = "APPROVED"
    cs["operations"][0]["after_state_hash"] = digest(payload)
    cs = seal_artifact(cs)

    def _unpromoted_apply(self, change_set, *, fail_after_operations=None):
        from dual_pipeline.core import connect_sqlite
        res = real_apply(self, change_set, fail_after_operations=fail_after_operations)
        conn = connect_sqlite(self.graph_path)
        conn.execute("UPDATE assertion SET canonical_status='candidate' WHERE assertion_id='as-adv-5'")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        return res

    monkeypatch.setattr(GovernedGraphWriter, "_apply_committed", _unpromoted_apply)
    with pytest.raises(GraphMutationProofError) as err:
        GovernedGraphWriter(graph).apply(cs)
    readback = next(c for c in err.value.receipt["checks"] if c["check"] == "readback")
    assert readback["status"] == "FAIL"
    assert readback["detail"]["assertion_0"] is False
