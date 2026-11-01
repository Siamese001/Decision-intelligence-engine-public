"""Wave 3 End-to-End tests for Sidecar Parity, Lock-Safe Promotion, & Governance Harmonization (Defects D8, D9, D11, D12)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.index_core.records import render_retrieval_records
from tests.test_hygiene_generation_promotion_e2e import _init_minimal_runtime_pair
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation
from tools.semantic_diff_reviewer import StorageConcurrencyAuditor


def test_e2e_sidecar_missing_records_insert_and_release_manifests(tmp_path: Path) -> None:
    """Defects D8 & D9: Promoted generation inserts newly promoted records into sidecar.sqlite and writes release/activation manifests under active_pair.lock."""
    rt_root = tmp_path / "runtime_root"
    graph_path, sidecar_path, initial_gen_id = _init_minimal_runtime_pair(rt_root)

    # Add a high-confidence candidate assertion that is NOT in initial sidecar.sqlite
    conn = sqlite3.connect(str(graph_path))
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, legacy_predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state, confidence_score
        ) VALUES (
            'assertion-cand-new-promo', 'stakeholder_jose_medina', 'LEADS', NULL, 'org_nea',
            'Jose Medina leads Apex Global Analytics strategy.', 'candidate', 1, 0, 'current', 'accepted_legacy', 0.94
        )"""
    )
    conn.execute(
        "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) "
        "VALUES ('assertion-cand-new-promo', 'ev-1', 'supports', '2026-10-01T00:00:00Z')"
    )
    # Also add a pre-existing redirect + falsely historical assertion (legacy_predicate IS NULL, superseded_by_assertion_id IS NULL)
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) "
        "VALUES ('stakeholder_old_retired', 'stakeholder', 'J. Medina Old', 'J. Medina Old', 'Retired alias', 'merged', 0, '2026-08-01T00:00:00Z', '2026-10-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO entity_redirect VALUES ('stakeholder_old_retired', 'stakeholder_jose_medina', 'merge-old-1', 1, '2026-10-25T12:59:13Z')"
    )
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, legacy_predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state, confidence_score, updated_at
        ) VALUES (
            'assertion-falsely-hist-1', 'stakeholder_old_retired', 'ADVISES', NULL, 'org_nea',
            'Jose Medina advises Apex Global Analytics board.', 'historical', 1, 0, 'historical', 'accepted_legacy', 0.91, '2026-10-25T12:59:13Z'
        )"""
    )
    conn.execute(
        "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) "
        "VALUES ('assertion-falsely-hist-1', 'ev-1', 'supports', '2026-10-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    pkg_path = tmp_path / "pkg_wave3.json"
    pkg_path.write_text(
        json.dumps(
            {
                "strict_package_gating": True,
                "findings": [
                    {
                        "detective": "ProvenanceInspector",
                        "severity": "MEDIUM",
                        "description": "Promote high-confidence candidate",
                        "entity_references": ["assertion-cand-new-promo"],
                        "proposed_fix": "batch_promote_candidates",
                        "raw_data": {"promotable_ids": ["assertion-cand-new-promo"]},
                    },
                    {
                        "detective": "StructuralValidator",
                        "severity": "HIGH",
                        "description": "Merge duplicate stakeholder",
                        "entity_references": ["stakeholder_jose_medina", "stakeholder_jose_m_dup"],
                        "proposed_fix": "merge_entities",
                        "raw_data": {
                            "node_id_a": "stakeholder_jose_medina",
                            "node_id_b": "stakeholder_jose_m_dup",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt_path = tmp_path / "run-10-25-2026" / "wave3_receipt.json"
    receipt = apply_hygiene_remediation(
        package_path=pkg_path,
        receipt_path=receipt_path,
        promote_generation=True,
        runtime_root=rt_root,
    )
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["candidates_promoted"] >= 1
    assert receipt["remediation_stats"]["falsely_historical_assertions_restored"] >= 1

    promo = receipt["promoted_generation"]
    new_gen_id = promo["generation_id"]
    assert new_gen_id != initial_gen_id

    # Verify active_pair.lock, release-<gen>.json, and activation-<gen>.json exist
    assert (rt_root / "active_pair.lock").is_file()
    rel_file = rt_root / "releases" / f"release-{new_gen_id}.json"
    act_file = rt_root / "releases" / f"activation-{new_gen_id}.json"
    assert rel_file.is_file()
    assert act_file.is_file()
    assert json.loads(rel_file.read_text())["generation_id"] == new_gen_id
    assert json.loads(act_file.read_text())["status"] == "ACTIVE"

    # Verify sidecar.sqlite has 100% of render_retrieval_records(new_graph_path)
    new_graph_path = rt_root / promo["graph_path"]
    new_sidecar_path = rt_root / promo["sidecar_path"]
    active_pair = json.loads((rt_root / "active_pair.json").read_text())
    expected_records = render_retrieval_records(new_graph_path, active_pair["graph_snapshot"])

    sconn = sqlite3.connect(str(new_sidecar_path))
    actual_rids = {r[0] for r in sconn.execute("SELECT record_id FROM retrieval_record").fetchall()}
    dv_count = sconn.execute("SELECT COUNT(*) FROM dense_vector").fetchone()[0]
    distinct_dv = sconn.execute("SELECT COUNT(DISTINCT vector) FROM dense_vector").fetchone()[0]
    meta_rc = dict(sconn.execute("SELECT key, value FROM metadata").fetchall())["record_count"]
    sconn.close()

    expected_rids = {r["record_id"] for r in expected_records}
    assert actual_rids == expected_rids
    assert int(meta_rc) == len(expected_records)
    assert dv_count == len(expected_records)
    assert distinct_dv == len(expected_records)

    # Verify falsely historical assertion was restored and repointed to stakeholder_jose_medina
    gconn = sqlite3.connect(str(new_graph_path))
    restored_row = gconn.execute(
        "SELECT subject_node_id, canonical_status, currentness_state, retrieval_eligible FROM assertion WHERE assertion_id='assertion-falsely-hist-1'"
    ).fetchone()
    gconn.close()
    assert restored_row == ("stakeholder_jose_medina", "canonical", "current", 1)


def test_storage_concurrency_auditor_allows_governed_maintenance_writers() -> None:
    """Defect D11: StorageConcurrencyAuditor must allow governed maintenance writers while blocking ungoverned writes."""
    auditor = StorageConcurrencyAuditor()
    added_lines = {
        "tools/apply_graph_hygiene_remediation.py": [
            (100, 'conn.execute("UPDATE assertion SET canonical_status = \'canonical\' WHERE assertion_id = ?", (aid,))')
        ],
        "tools/tier2_dedup_core/hygiene_promotion.py": [
            (200, 'conn.execute("DELETE FROM source WHERE source_id = ?", (loser_id,))')
        ],
        "dual_pipeline/graph_consolidation_core/repoint.py": [
            (150, 'conn.execute("UPDATE assertion SET subject_node_id = ? WHERE assertion_id = ?", (surv, aid))')
        ],
        "dual_pipeline/some_ungoverned_module.py": [
            (42, 'conn.execute("DELETE FROM node WHERE node_id = ?", (nid,))')
        ],
    }
    findings, _ = auditor.audit(added_lines)
    critical_files = [f.file for f in findings if f.severity == "CRITICAL"]
    assert critical_files == ["dual_pipeline/some_ungoverned_module.py"]


def test_graph_hygiene_prompt_harmonized() -> None:
    """Defect D8: Verify automations/prompts/graph_hygiene_agent.md harmonizes --promote-generation, --dry-run, and cluster flags."""
    prompt_text = Path("automations/prompts/graph_hygiene_agent.md").read_text(encoding="utf-8")
    assert "--promote-generation" in prompt_text
    assert "--dry-run" in prompt_text
    assert "--approved-clusters" in prompt_text
    assert "--excluded-clusters" in prompt_text
    assert "authorize-merge" in prompt_text
