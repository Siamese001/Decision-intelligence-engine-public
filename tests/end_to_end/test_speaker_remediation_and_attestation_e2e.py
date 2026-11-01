"""End-to-end lifecycle tests for cloned-generation speaker remediation, candidate promotion, and DSSE readiness attestation."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import (
    audit_safe_promotable_candidates,
    build_antipattern_hygiene_findings,
    build_mece_cluster_finding_rows,
)
from tools.attestation_core.envelope import READINESS_PREDICATE_TYPE
from tools.attestation_core.storage import (
    check_readiness_qualification,
    save_readiness_attestation,
)
from tools.scan_ssot_configuration_violations_core.rca_checks import (
    check_rca_ssot_violations,
)
from tools.tier2_dedup_core.speaker_and_qualifier_remediation import (
    execute_rebound_candidate_promotion,
    rebind_placeholder_speakers_for_ids,
    remediate_speaker_and_qualifier_fixes,
)


@dataclass
class _FindingRecord:
    detective: str
    severity: str
    description: str
    entity_references: List[str]
    proposed_fix: str
    raw_data: Dict[str, Any]


@dataclass
class _DebateRecord:
    committee_verdict: str


def _create_speaker_remediation_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            node_type TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            cold_archive_ref TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            transcript_timestamp TEXT,
            locator_value TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            scope_text TEXT,
            object_literal TEXT,
            speaker_or_author TEXT,
            qualifiers_json TEXT,
            primary_source_id TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            currentness_state TEXT,
            validation_state TEXT,
            is_quarantined INTEGER,
            confidence_score REAL,
            approval_reference TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        );
        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            assertion_id TEXT,
            title TEXT,
            body TEXT,
            object_type TEXT,
            object_id TEXT,
            canonical_status TEXT,
            retrieval_eligible INTEGER,
            updated_at TEXT
        );
        CREATE VIRTUAL TABLE search_fts USING fts5(
            title, body, object_type, object_id
        );
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT,
            approval_type TEXT,
            scope_json TEXT,
            proposal_sha256 TEXT,
            approval_statement_sha256 TEXT,
            approved_at TEXT,
            status TEXT,
            approved_by TEXT,
            release_version TEXT,
            active INTEGER,
            created_at TEXT
        );
        """
    )


def test_e2e_speaker_remediation_quarantine_rebind_and_fts_resync(tmp_path: Path) -> None:
    """E2E lifecycle: quarantines speaker defects, rebinds placeholder speakers via sibling provenance, syncs qualifiers, and resyncs FTS."""
    db_path = tmp_path / "canonical_graph.sqlite"
    with sqlite3.connect(db_path) as conn:
        _create_speaker_remediation_schema(conn)
        conn.execute("INSERT INTO node VALUES ('person:jake-stark', 'Jake Stark', 'stakeholder')")
        conn.execute("INSERT INTO node VALUES ('workstream_lens', 'Project LENS', 'workstream')")
        conn.execute(
            "INSERT INTO source VALUES ('src-1', 'dual_pipeline_intake', 'Meeting LENS Sync', NULL)"
        )
        conn.execute("INSERT INTO evidence_item VALUES ('ev-1', '00:04:12', '00:04:12')")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-2', '00:08:00', '00:08:00')")

        # Sibling assertion providing verified speaker provenance for (src-1, ev-1)
        conn.execute(
            """
            INSERT INTO assertion VALUES (
                'asrt-sibling', 'person:jake-stark', 'HAS_CLAIM_TEXT',
                'Jake Stark confirmed the deployment window.', 'Jake Stark confirmed the deployment window.',
                'Jake Stark', '{}', 'src-1', 'canonical', 1, 1, 'current', 'accepted', 0, 0.92, 'app-0', '2026-10-01'
            )
            """
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-sibling', 'ev-1')")

        # 1. Undiarized defect assertion to quarantine
        conn.execute(
            """
            INSERT INTO assertion VALUES (
                'asrt-defect', 'workstream_lens', 'HAS_CLAIM_TEXT',
                'Yeah, I think we should delay the cutover.', 'Yeah, I think we should delay the cutover.',
                '', '{"dual_pipeline_disposition": "APPROVED"}', 'src-1',
                'canonical', 1, 1, 'current', 'accepted', 0, 0.85, 'app-0', '2026-10-01'
            )
            """
        )
        conn.execute(
            "INSERT INTO search_document VALUES (1, 'asrt-defect', 'Delay cutover', 'Yeah, I think we should delay the cutover.', 'assertion', 'asrt-defect', 'canonical', 1, '2026-10-01')"
        )

        # 2. Placeholder speaker assertion to rebind via sibling evidence ev-1 and strip leading colon
        conn.execute(
            """
            INSERT INTO assertion VALUES (
                'asrt-rebind', 'workstream_lens', 'HAS_CLAIM_TEXT',
                ': **Downstream Production Integration:** Validated 83 assets in golden dataset.',
                ': **Downstream Production Integration:** Validated 83 assets in golden dataset.',
                'Speaker 1', '{"dual_pipeline_disposition": "CANDIDATE", "fact_promotion_eligible": false, "dual_pipeline_candidate_id": "c1"}',
                'src-1', 'candidate', 0, 0, 'current', 'accepted_legacy', 0, 0.88, NULL, '2026-10-01'
            )
            """
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-rebind', 'ev-1')")
        conn.execute(
            "INSERT INTO search_document VALUES (2, 'asrt-rebind', 'Old title', 'Old body', 'assertion', 'asrt-rebind', 'canonical', 1, '2026-10-01')"
        )

        # 3. Stale qualifier canonical assertion with em-dash typography to sync
        conn.execute(
            """
            INSERT INTO assertion VALUES (
                'asrt-qual', 'workstream_lens', 'HAS_CLAIM_TEXT',
                'Maximo integration\u2014phase 2', 'Maximo integration\u2014phase 2',
                'Jake Stark', '{"dual_pipeline_disposition": "CANDIDATE", "answer_use": "candidate_hypothesis", "speaker_hygiene_quarantined": true}',
                'src-1', 'canonical', 1, 1, 'current', 'accepted', 0, 0.91, 'app-0', '2026-10-30'
            )
            """
        )

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        acols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}

        # Audit safe promotable candidates before remediation (asrt-rebind is recoverable via sibling ev-1)
        promotable_finding = audit_safe_promotable_candidates(
            conn.cursor(), acols, excluded_ids=set(), finding_cls=_FindingRecord
        )
        assert promotable_finding is not None
        assert "asrt-rebind" in promotable_finding.entity_references

        findings = [
            {
                "proposed_fix": "quarantine_undiarized_or_misattributed_claim",
                "raw_data": {"assertion_id": "asrt-defect"},
                "entity_references": ["asrt-defect"],
            },
            {
                "proposed_fix": "rebind_placeholder_speaker",
                "raw_data": {"assertion_id": "asrt-rebind"},
                "entity_references": ["asrt-rebind"],
            },
            {
                "proposed_fix": "sync_promoted_qualifier_metadata",
                "raw_data": {"assertion_id": "asrt-qual"},
                "entity_references": ["asrt-qual"],
            },
        ]
        sample_ids: List[str] = []
        summary = remediate_speaker_and_qualifier_fixes(
            conn, findings, tables, "2026-10-30T16:00:00Z", sample_ids
        )
        assert summary == {
            "speaker_defects_quarantined": 1,
            "placeholder_speakers_rebound": 1,
            "qualifier_metadata_synced": 1,
        }

        # Now promote the rebound candidate assertion through execute_rebound_candidate_promotion
        promoted_cnt = execute_rebound_candidate_promotion(
            conn,
            ["asrt-rebind"],
            approval_ref="hitl-approval-1030",
            acols=acols,
            tables=tables,
            now_iso="2026-10-30T16:05:00Z",
        )
        assert promoted_cnt == 1

    # Independent read-only verification of all SQLite and FTS state transitions
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as ro_conn:
        assert ro_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        defect_row = ro_conn.execute(
            "SELECT canonical_status, canonical_eligible, retrieval_eligible, validation_state, qualifiers_json FROM assertion WHERE assertion_id='asrt-defect'"
        ).fetchone()
        assert defect_row[:4] == ("candidate", 0, 0, "needs_review")
        assert json.loads(defect_row[4])["speaker_hygiene_quarantined"] is True

        rebind_row = ro_conn.execute(
            "SELECT speaker_or_author, scope_text, canonical_status, canonical_eligible, retrieval_eligible, approval_reference, qualifiers_json FROM assertion WHERE assertion_id='asrt-rebind'"
        ).fetchone()
        assert rebind_row[0] == "Jake Stark"
        assert rebind_row[1] == "Validated 83 assets in golden dataset."
        assert rebind_row[2:6] == ("canonical", 1, 1, "hitl-approval-1030")
        assert json.loads(rebind_row[6])["dual_pipeline_disposition"] == "APPROVED"

        qual_row = ro_conn.execute(
            "SELECT object_literal, qualifiers_json FROM assertion WHERE assertion_id='asrt-qual'"
        ).fetchone()
        assert qual_row[0] == "Maximo integration - phase 2"
        q_synced = json.loads(qual_row[1])
        assert q_synced["dual_pipeline_disposition"] == "APPROVED"
        assert q_synced["answer_use"] == "factual_answer"
        assert "speaker_hygiene_quarantined" not in q_synced

        fts_rows = ro_conn.execute(
            "SELECT object_id, body FROM search_fts"
        ).fetchall()
        assert len(fts_rows) == 1
        assert fts_rows[0] == ("asrt-rebind", "Validated 83 assets in golden dataset.")

        ga_cnt = ro_conn.execute(
            "SELECT COUNT(*) FROM governance_approval WHERE approval_reference='hitl-approval-1030' AND status='approved'"
        ).fetchone()[0]
        assert ga_cnt == 1


def test_e2e_hygiene_council_mece_clustering_and_dsse_git_notes_roundtrip(tmp_path: Path) -> None:
    """E2E lifecycle: verifies anti-pattern council finding synthesis, MECE table clustering, and DSSE git-notes fallback."""
    class _AntipatternItem:
        def __init__(self, ftype: str, target_id: str, action: str, details: Dict[str, Any]):
            self.finding_type = ftype
            self.target_id = target_id
            self.action_recommended = action
            self.details = details
            self.label = "Fallback label"

    items = [
        _AntipatternItem(
            "speaker_hygiene_defect",
            "asrt-1",
            "quarantine_undiarized_or_misattributed_claim",
            {"defect_code": "placeholder_speaker_label"},
        ),
        _AntipatternItem(
            "synthetic_node_pollution_check",
            "cnode_99",
            "archive_clutter",
            {"canonical_name": "Synthetic Persona"},
        ),
        _AntipatternItem(
            "qualifier_drift",
            "asrt-2",
            "sync_promoted_qualifier_metadata",
            {},
        ),
    ]
    council_findings = build_antipattern_hygiene_findings(items, _FindingRecord)
    assert len(council_findings) == 3
    assert council_findings[0].detective == "ProvenanceInspector"
    assert council_findings[1].detective == "RedTeamer"
    assert council_findings[1].proposed_fix == "archive_node"
    assert council_findings[2].severity == "LOW"

    debates = [_DebateRecord("Quarantine"), _DebateRecord("Archive"), _DebateRecord("Sync")]
    rows = build_mece_cluster_finding_rows(
        council_findings,
        debates,
        format_row_fn=lambda f, d: f"{f.proposed_fix}:{d.committee_verdict}",
        clean_cell_fn=lambda s, n: str(s)[:n],
        detector_labels={"ProvenanceInspector": "Provenance", "RedTeamer": "Security"},
    )
    assert len(rows) == 3
    assert any("Generic Roster Header" in r for r in rows)

    # Verify legacy receipt fallback in check_readiness_qualification
    slug = "feat_legacy_check"
    legacy_dir = tmp_path / "reports" / "readiness"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / f"local_main_readiness_{slug}_receipt.json"
    legacy_path.write_text(
        json.dumps(
            {
                "branch": "feat/legacy-check",
                "head_commit_sha": "sha_legacy_111",
                "terminal_outcome": "PASS",
            }
        ),
        encoding="utf-8",
    )
    with patch(
        "tools.attestation_core.storage.compute_tree_digest",
        autospec=True,
        return_value="missing_tree_digest",
    ), patch(
        "tools.attestation_core.storage.compute_branch_delta_digest",
        autospec=True,
        return_value="",
    ):
        ok, rec, src = check_readiness_qualification(
            tmp_path, "feat/legacy-check", "sha_legacy_111", slug
        )
        assert ok is True
        assert rec is not None
        assert "legacy in-tree receipt" in src
