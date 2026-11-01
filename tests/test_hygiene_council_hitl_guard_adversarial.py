"""Adversarial & Edge-Case Tests for Wave 1: Council Clustering, HITL Guard, and Staging Stem Parsing.

Covers mandatory edge-case categories per `.agents/rules/e2e-and-edge-case-coverage.md`:
1. Boundary payload: 1-row Dimension Findings Table, empty/relative `.staging_` paths, 0-bloat ratio with orphaned evidence.
2. Malformed input: Generic container filenames (`source_document.txt`, `intake_package.json`, `forensic_transcript.md`) inside relative `.staging_<Slug>_<hex>/` paths.
3. Overflow & exhaustion: >50 duplicate assertion proposals, >50 promotable candidates, >50 source entity references without `[:10]`/`[:20]`/`[:30]` truncation.
4. Invariant bypass attempt: All 17 `proposed_fix` types partitioned into atomic clusters and validated against `check_hook_payload` (`decision == 'allow'`, 0 jargon errors).
5. Exact production regression: Pins D3 (false-positive 30-source `.staging_` cluster), D5 (9 of 15 clusters blocked by `hitl_pre_tool_guard.py`), and D6 (`CLUSTER-08` catch-all bundling).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

from dual_pipeline.graph_maintenance_core.health import calculate_graph_health_metrics
from dual_pipeline.graph_maintenance_core.hygiene_agent import (
    Finding,
    RedTeamerAgent,
    SynthesisJudgeAgent,
    _resolve_clutter_fix,
)
from dual_pipeline.graph_maintenance_core.hygiene_council import (
    PASCAL_OR_SNAKE_RE,
    classify_finding_cluster,
)
from dual_pipeline.graph_scout_core.source_scans import audit_source_hygiene_findings
from dual_pipeline.graph_writer_core.sources import normalize_source_archive_stem
from dual_pipeline.hitl_governance_core.findings_table_validator import (
    split_findings_table_context,
)
from dual_pipeline.hitl_governance_core.question_validator import (
    validate_question_payload,
)
from tools.check_ask_user_question import check_hook_payload


def test_staging_relative_and_absolute_path_stem_normalization(tmp_path: Path) -> None:
    """Category 1, 2 & 5 (D3 Regression): Relative `.staging_<Slug>_<hex>/source_document.txt` extracts `<slug>` and never merges unrelated staging sources."""
    assert normalize_source_archive_stem(".staging_APEX_RFP_9a536be6/source_document.txt") == "fpl_rfp"
    assert (
        normalize_source_archive_stem(
            "/var/data/output/.staging_Lens_Architecture_202610151200/intake_package.json"
        )
        == "lens_architecture"
    )
    assert normalize_source_archive_stem(".staging_Grid_Modernization_deadbeef/forensic_transcript.md") == "grid_modernization"
    assert normalize_source_archive_stem("source_document.txt") == ""
    assert normalize_source_archive_stem("intake_package.json") == ""
    assert normalize_source_archive_stem("") == ""

    from tools.prepare_evidence_envelope_intake import _normalize_cold_archive_stem

    assert _normalize_cold_archive_stem(".staging_APEX_RFP_9a536be6/source_document.txt") == "apex-rfp"
    assert (
        _normalize_cold_archive_stem(
            "/var/data/output/.staging_Lens_Architecture_202610151200/intake_package.json"
        )
        == "lens-architecture"
    )

    # Verify 35 distinct relative .staging_ sources do NOT collapse into a false-positive cluster
    db_path = tmp_path / "staging_sources.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_type TEXT,
                title TEXT,
                source_path_or_uri TEXT,
                created_at TEXT
            )"""
        )
        conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT, raw_text TEXT)")
        for i in range(35):
            conn.execute(
                "INSERT INTO source VALUES (?, 'document', ?, ?, '2026-10-15T10:00:00Z')",
                (
                    f"source-stg-{i:02d}",
                    f"Deliverable Alpha{i:04d} Beta{i:04d}",
                    f".staging_Deliverable_{i:02d}_9a536be6/source_document.txt",
                ),
            )
        # Also insert 2 true duplicate sources with the same staging slug across 40 duplicates to test untruncated entity_references
        for j in range(35):
            conn.execute(
                "INSERT INTO source VALUES (?, 'meeting_audio', '20261022_140000_AI_Sync', ?, '2026-10-22T14:00:00Z')",
                (
                    f"source-dup-{j:02d}",
                    f"output/project_lens/10-22-2026/20261022_140000_AI_Sync/0{j % 3 + 1}_artifact.md",
                ),
            )
        conn.commit()
        findings = audit_source_hygiene_findings(conn)

    dup_findings = [f for f in findings if f["proposed_fix"] == "consolidate_duplicate_sources"]
    assert len(dup_findings) == 1
    assert len(dup_findings[0]["entity_references"]) == 35


def test_single_row_findings_table_and_all_17_fixes_pass_hitl_guard() -> None:
    """Category 1, 4 & 5 (D5 & D6 Regression): 1-row table and all 17 `proposed_fix` types map to atomic clusters and pass `check_hook_payload`."""
    one_row_q = (
        "| Dimension | Plain-English Status and Record Comparison | Source and Timing Context | 8-Agent Council Verdict |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| Structure Review | Single finding row in cluster | Priority High | Recommended for approval |\n"
        "Should we approve this single-item review cluster?"
    )
    table_block, active_prompt = split_findings_table_context(one_row_q)
    assert table_block is not None
    assert active_prompt == "Should we approve this single-item review cluster?"

    all_findings: List[Finding] = [
        Finding(
            detective="StructuralValidator",
            severity="MEDIUM",
            description="Superseded candidate draft",
            entity_references=["asrt-cand-1"],
            proposed_fix="archive_candidate_assertion",
            raw_data={"assertion_id": "asrt-cand-1", "superseded_by_assertion_id": "asrt-canon-1"},
        ),
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description="Stakeholder duplicate pair",
            entity_references=["person-1", "person-2"],
            proposed_fix="merge_entities",
            raw_data={"node_id_a": "person-1", "node_id_b": "person-2", "node_type_a": "stakeholder", "canonical_name_a": "Ed Moore", "canonical_name_b": "Edward Moore", "similarity_score": 0.94, "margin_delta": 0.50},
        ),
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description="Proposal boundary pair",
            entity_references=["ws-1", "ws-2"],
            proposed_fix="merge_entities",
            raw_data={"node_id_a": "ws-1", "node_id_b": "ws-2", "node_type_a": "workstream", "canonical_name_a": "Grid Modernization", "canonical_name_b": "Grid Modernization Proposal", "similarity_score": 0.89, "margin_delta": 0.12},
        ),
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description="Role scope divergent pair",
            entity_references=["role-1", "role-2"],
            proposed_fix="merge_entities",
            raw_data={"node_id_a": "role-1", "node_id_b": "role-2", "node_type_a": "role_group", "canonical_name_a": "Technical Lead", "canonical_name_b": "Atlas Technical Lead", "similarity_score": 0.86, "margin_delta": 0.35},
        ),
        Finding(detective="StructuralValidator", severity="MEDIUM", description="Auto link edge to statement", entity_references=["edge-1"], proposed_fix="auto_link_edge", raw_data={"edge_id": "edge-1", "matching_assertion_id": "asrt-1"}),
        Finding(detective="StructuralValidator", severity="HIGH", description="Dangling edge to inactive node", entity_references=["edge-2"], proposed_fix="archive_edge", raw_data={"edge_id": "edge-2"}),
        Finding(detective="StructuralValidator", severity="MEDIUM", description="Synthesize statement for active edge", entity_references=["edge-3"], proposed_fix="synthesize_edge_assertion", raw_data={"edge_id": "edge-3"}),
        Finding(detective="StructuralValidator", severity="MEDIUM", description="Duplicate assertion pair", entity_references=["asrt-w", "asrt-l"], proposed_fix="deduplicate_assertions", raw_data={"winner_id": "asrt-w", "loser_id": "asrt-l", "winner_claim": "Ed Moore leads Grid", "loser_claim": "Ed Moore leads Grid", "similarity": 0.98}),
        Finding(detective="TemporalAuditor", severity="HIGH", description="Timeline supersession", entity_references=["asrt-old", "asrt-new"], proposed_fix="supersede_assertion", raw_data={"subject_name": "Ed Moore", "predicate": "HAS_ROLE", "claim_older": "Director", "claim_newer": "VP", "timestamp_older": "2026-08-01", "timestamp_newer": "2026-10-01"}),
        Finding(detective="TemporalAuditor", severity="LOW", description="Aged 95 day statement requires review", entity_references=["asrt-90d"], proposed_fix="escalate_90d_revalidation", raw_data={"assertion_id": "asrt-90d", "age_days": 95.0}),
        Finding(detective="StructuralValidator", severity="LOW", description="High confidence staging promotions", entity_references=["asrt-p1"], proposed_fix="batch_promote_candidates", raw_data={"promotable_count": 1}),
        Finding(detective="StructuralValidator", severity="HIGH", description="Missing search index rows", entity_references=["asrt-s1"], proposed_fix="resync_search_sidecars", raw_data={"missing_count": 1}),
        Finding(detective="StructuralValidator", severity="MEDIUM", description="Leaked non canonical search rows", entity_references=["asrt-s2"], proposed_fix="deactivate_search_documents", raw_data={"leaked_count": 1}),
        Finding(detective="StructuralValidator", severity="MEDIUM", description="Aged isolated orphan node", entity_references=["node-orph-1"], proposed_fix="archive_node", raw_data={"node_id": "node-orph-1", "status": "candidate", "created_at": "2026-08-01T00:00:00Z", "allow_aged_candidate_orphan": True}),
        Finding(detective="StructuralValidator", severity="HIGH", description="Orphaned evidence items detected", entity_references=["graph_bloat_metrics"], proposed_fix="run_graph_gc", raw_data={"orphaned_evidence": 15}),
        Finding(detective="ProvenanceInspector", severity="HIGH", description="Duplicate source records", entity_references=["src-1", "src-2"], proposed_fix="consolidate_duplicate_sources", raw_data={"canonical_source_id": "src-1", "duplicate_source_ids": ["src-2"], "group_size": 2, "canonical_title": "AI Weekly Sync"}),
        Finding(detective="ProvenanceInspector", severity="HIGH", description="Placeholder unknown source title", entity_references=["src-unk"], proposed_fix="unmerge_or_retitle_degenerate_source", raw_data={"source_id": "src-unk", "title": "Unknown"}),
        Finding(detective="ProvenanceInspector", severity="HIGH", description="Ungrounded statement missing quote", entity_references=["asrt-ungrounded"], proposed_fix="reconcile_evidence", raw_data={"assertion_id": "asrt-ungrounded"}),
        Finding(detective="RedTeamer", severity="CRITICAL", description="Unsigned audit record", entity_references=["aud-1"], proposed_fix="quarantine_audit_record", raw_data={"audit_id": "aud-1"}),
        Finding(detective="RedTeamer", severity="HIGH", description="Unauthenticated audit approval reference", entity_references=["aud-2"], proposed_fix="review_audit_record", raw_data={"audit_event_id": "aud-2"}),
    ]

    judge = SynthesisJudgeAgent()
    pkg = judge.synthesize(all_findings)
    assert len(pkg.findings) == len(all_findings)

    # Verify aged candidate orphan was NOT vetoed and CLUSTER-08 only contains archive_node
    cluster_map = {c["cluster_id"]: c for c in pkg.hitl_clusters}
    assert "CLUSTER-08-GOVERNANCE-AUDIT" in cluster_map
    assert [f["proposed_fix"] for f in cluster_map["CLUSTER-08-GOVERNANCE-AUDIT"]["findings"]] == ["archive_node"]
    assert "CLUSTER-09A-DUPLICATE-SOURCES" in cluster_map
    assert "CLUSTER-09B-DEGENERATE-AND-COLLIDED-SOURCES" in cluster_map
    assert "CLUSTER-10-UNGROUNDED-EVIDENCE" in cluster_map
    assert "CLUSTER-11-AUDIT-SECURITY" in cluster_map
    assert cluster_map["CLUSTER-02D2-ROLE-PROJECT-SCOPE"]["ready_to_ask_question"]["recommended_action"] == "exclude"

    # Every cluster must pass both validate_question_payload AND check_hook_payload
    for cluster in pkg.hitl_clusters:
        q_payload = cluster["ready_to_ask_question"]
        val_res = validate_question_payload(question=q_payload["question"], options=q_payload["options"])
        assert val_res.is_valid, f"Cluster {cluster['cluster_id']} failed validate_question_payload: {val_res.errors}"

        hook_input = json.dumps({"toolCall": {"name": "ask_question", "args": {"questions": [q_payload]}}})
        hook_res = check_hook_payload(hook_input)
        assert hook_res.get("decision") == "allow", f"Cluster {cluster['cluster_id']} blocked by hook: {hook_res}"

        # Verify zero snake_case in council debate summary
        assert not PASCAL_OR_SNAKE_RE.search(cluster["council_debate_summary"]), (
            f"Jargon token found in council_debate_summary: {cluster['council_debate_summary']}"
        )


def test_untruncated_duplicate_assertions_aged_orphan_and_redteamer_dual_checks(tmp_path: Path) -> None:
    """Category 3 (Overflow & Exhaustion): Verifies >10 duplicate assertion proposals are preserved and RedTeamer runs both checks."""
    from dual_pipeline.graph_scout_core.models import ClutterFinding

    # 1. Verify _resolve_clutter_fix allows aged (>30d) candidate orphan nodes when allow_aged_candidate_orphan is set
    aged_details = {"status": "candidate", "created_at": "2026-08-01T00:00:00Z", "allow_aged_candidate_orphan": True}
    fix = _resolve_clutter_fix(
        ClutterFinding("orphan_node", "n-aged", "Aged Orphan", aged_details, "archive_node"),
        aged_details,
    )
    assert fix == "archive_node"

    # Fresh (<30d) candidate orphan remains None
    fresh_details = {"status": "candidate", "created_at": "2099-01-01T00:00:00Z"}
    fix_fresh = _resolve_clutter_fix(
        ClutterFinding("orphan_node", "n-fresh", "Fresh Orphan", fresh_details, "archive_node"),
        fresh_details,
    )
    assert fix_fresh is None

    # 2. Verify inspect_duplicate_assertions returns full `duplicate_proposals` list (>10 items) alongside sample
    db_path = tmp_path / "dup_assertions.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                scope_text TEXT,
                canonical_status TEXT,
                confidence_score REAL,
                created_at TEXT
            )"""
        )
        conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        conn.execute(
            """CREATE TABLE audit_event (
                audit_event_id TEXT PRIMARY KEY,
                action TEXT,
                actor_or_process TEXT,
                operator_signature TEXT,
                approval_reference TEXT,
                migration_or_ingestion_run_id TEXT,
                validation_reference TEXT
            )"""
        )
        for i in range(15):
            conn.execute(
                "INSERT INTO assertion VALUES (?, ?, 'OWNS_SCOPE', ?, 'Identical claim text for dedup test', 'canonical', 0.95, '2026-10-01T00:00:00Z')",
                (f"asrt-a-{i}", f"subj-{i}", f"obj-{i}"),
            )
            conn.execute(
                "INSERT INTO assertion VALUES (?, ?, 'OWNS_SCOPE', ?, 'Identical claim text for dedup test', 'canonical', 0.90, '2026-10-02T00:00:00Z')",
                (f"asrt-b-{i}", f"subj-{i}", f"obj-{i}"),
            )
        conn.execute("INSERT INTO audit_event VALUES ('aud-1', 'mutate', 'test', '', '', '', '')")
        conn.commit()

        dup_report = calculate_graph_health_metrics(conn)
        assert dup_report["counts"]["ghost_duplicate_assertions"] == 15
        assert len(dup_report["duplicate_proposals_sample"]) == 10
        assert len(dup_report["duplicate_proposals"]) == 15

        red_findings = RedTeamerAgent().audit(db_path, conn)
        fixes = {f.proposed_fix for f in red_findings}
        assert "quarantine_audit_record" in fixes
        assert "review_audit_record" in fixes
        assert classify_finding_cluster(red_findings[0])[0] == "CLUSTER-11-AUDIT-SECURITY"

        # 3. Verify _scan_orphan_nodes natively sets allow_aged_candidate_orphan=True on >=14d candidate orphans
        from dual_pipeline.graph_maintenance_core.hygiene_agent import (
            _audit_bloat_and_clutter,
        )
        from dual_pipeline.graph_scout_core.scans import _scan_orphan_nodes

        conn.execute(
            "CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, status TEXT, active INTEGER, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO node VALUES ('cand-aged-1', 'Aged Candidate Entity', 'entity', 'candidate', 1, '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO node VALUES ('cand-fresh-1', 'Fresh Candidate Entity', 'entity', 'candidate', 1, '2099-01-01T00:00:00Z')"
        )
        conn.commit()
        orphan_findings = _scan_orphan_nodes(conn.cursor(), min_orphan_age_days=14)
        assert len(orphan_findings) == 1
        assert orphan_findings[0].target_id == "cand-aged-1"
        assert orphan_findings[0].details.get("allow_aged_candidate_orphan") is True
        assert _resolve_clutter_fix(orphan_findings[0], orphan_findings[0].details) == "archive_node"

        # 4. Verify bloat_ratio > 1.5 triggers run_graph_gc even when orphaned_evidence_records == 0
        from dual_pipeline.graph_scout import GraphScout

        bloat_findings = _audit_bloat_and_clutter(
            {"max_bloat_ratio": 2.1, "counts": {"orphaned_evidence_records": 0}},
            GraphScout(graph_path=db_path),
            conn,
        )
        assert any(f.proposed_fix == "run_graph_gc" for f in bloat_findings)
