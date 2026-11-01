"""End-to-end lifecycle tests for fast_promotion, transcript_turn_resolver, and prepare_evidence_envelope_intake notes fallback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.core import connect_sqlite, sha256_file
from dual_pipeline.graph_maintenance_core import transcript_turn_resolver
from dual_pipeline.graph_writer_core import fast_promotion
from tools import prepare_evidence_envelope_intake


def _seed_fast_promotion_runtime(rt_root: Path) -> tuple[Path, Path]:
    gen_dir = rt_root / "generations" / "gen_initial_001"
    gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = gen_dir / "graph.sqlite"
    sidecar_path = gen_dir / "sidecar.sqlite"

    conn = connect_sqlite(graph_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_type TEXT,
                source_name TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                scope_text TEXT,
                object_literal TEXT,
                assertion_type TEXT,
                confidence_score REAL,
                qualifiers_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                retrieval_eligible INTEGER,
                currentness_state TEXT,
                validation_state TEXT,
                superseded_by_assertion_id TEXT,
                superseded_at TEXT,
                approval_reference TEXT,
                primary_source_id TEXT,
                speaker_or_author TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT,
                PRIMARY KEY (assertion_id, evidence_id)
            );
            CREATE TABLE search_document (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                search_document_id TEXT UNIQUE,
                object_type TEXT,
                object_id TEXT,
                title TEXT,
                body TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                source_id TEXT,
                assertion_id TEXT,
                updated_at TEXT
            );
            CREATE VIRTUAL TABLE search_fts USING fts5(object_id, object_type, title, body);
            CREATE TABLE governance_approval (
                approval_id TEXT PRIMARY KEY,
                approval_reference TEXT,
                approval_type TEXT,
                status TEXT,
                approved_by TEXT,
                approved_at TEXT,
                created_at TEXT,
                proposal_sha256 TEXT,
                approval_statement_sha256 TEXT,
                release_version TEXT,
                scope_json TEXT,
                notes TEXT,
                active INTEGER
            );
            """
        )
        conn.execute("INSERT INTO node VALUES ('node_lens', 'Project LENS', 'workstream', 1)")
        conn.execute("INSERT INTO node VALUES ('node_s3', 'Amazon S3', 'system', 1)")
        conn.execute("INSERT INTO source VALUES ('src_mtg_01', 'meeting_transcript', 'LENS Architecture Sync')")

        # 1. Valid candidate to promote
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_cand_promote_1', 'node_lens', 'USES_COMPONENT', 'node_s3',
                'Project LENS uses Amazon S3 for inspection imagery storage.', 'Amazon S3',
                'fact', 0.91, '{"lane":"candidate"}', '2026-10-30T10:00:00Z', '2026-10-30T10:00:00Z',
                'candidate', 0, 1, 'current', 'verified', NULL, NULL, NULL, 'src_mtg_01', 'Eduardo'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_cand_promote_1', 'ev_1', 'supports')")

        # 2. Duplicate candidate of asrt_cand_promote_1 to deduplicate
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_cand_dup_2', 'node_lens', 'USES_COMPONENT', 'node_s3',
                'Project LENS uses Amazon S3 for inspection imagery storage [a1b2c3]', 'Amazon S3',
                'fact', 0.85, '{"lane":"candidate"}', '2026-10-30T10:05:00Z', '2026-10-30T10:05:00Z',
                'candidate', 0, 1, 'current', 'verified', NULL, NULL, NULL, 'src_mtg_01', 'Eduardo'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_cand_dup_2', 'ev_2', 'supports')")

        # 3. Blocked candidate (open_question) to skip
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt_cand_skip_3', 'node_lens', 'HAS_CLAIM_TEXT', NULL,
                'What is the Q4 budget for S3 storage?', 'What is the Q4 budget?',
                'open_question', 0.92, '{}', '2026-10-30T10:10:00Z', '2026-10-30T10:10:00Z',
                'candidate', 0, 1, 'current', 'verified', NULL, NULL, NULL, 'src_mtg_01', 'Eduardo'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_cand_skip_3', 'ev_3', 'supports')")
        conn.commit()
    finally:
        conn.close()

    sconn = connect_sqlite(sidecar_path)
    try:
        sconn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        sconn.execute("INSERT INTO metadata VALUES ('generation_id', 'gen_initial_001')")
        sconn.commit()
    finally:
        sconn.close()

    active_pair = {
        "generation_id": "gen_initial_001",
        "graph_path": "generations/gen_initial_001/graph.sqlite",
        "sidecar_path": "generations/gen_initial_001/sidecar.sqlite",
        "graph_sha256": sha256_file(graph_path),
        "sidecar_sha256": sha256_file(sidecar_path),
    }
    (rt_root / "active_pair.json").write_text(json.dumps(active_pair, indent=2), encoding="utf-8")
    return graph_path, sidecar_path


def test_fast_promotion_end_to_end_generation_clone_and_5point_proof(tmp_path: Path) -> None:
    """Verify end-to-end candidate fast promotion, duplicate supersession, FTS sync, and 5-point physical proof."""
    rt_root = tmp_path / "runtime"
    rt_root.mkdir(parents=True, exist_ok=True)
    old_graph, _ = _seed_fast_promotion_runtime(rt_root)
    old_sha = sha256_file(old_graph)

    # In-place mutation of live active_pair graph without promote_generation=True must fail closed
    with pytest.raises(RuntimeError, match="prohibited"):
        fast_promotion.fast_promote_candidates(
            old_graph, runtime_root=rt_root, promote_generation=False, dry_run=False
        )

    # Dry run leaves graph untouched
    dry_res = fast_promotion.fast_promote_candidates(
        old_graph, runtime_root=rt_root, promote_generation=False, dry_run=True
    )
    assert dry_res.status == "DRY_RUN"
    assert dry_res.promoted_count == 1
    assert dry_res.deduped_count == 1
    assert dry_res.skipped_count == 1
    assert sha256_file(old_graph) == old_sha

    # Full generation clone promotion with 5-point physical proof
    promo_res = fast_promotion.fast_promote_candidates(
        runtime_root=rt_root,
        min_confidence=0.80,
        approval_reference="APPROVAL-E2E-FAST-001",
        promote_generation=True,
        dry_run=False,
    )
    assert promo_res.status == "PROMOTED"
    assert promo_res.promoted_ids == ["asrt_cand_promote_1"]
    assert promo_res.deduped_ids == ["asrt_cand_dup_2"]
    assert promo_res.skipped_count == 1
    assert promo_res.physical_proof["overall"] == "PASS"

    # Verify old generation file was untouched and active_pair switched to new generation
    assert sha256_file(old_graph) == old_sha
    new_active = json.loads((rt_root / "active_pair.json").read_text(encoding="utf-8"))
    assert new_active["generation_id"] == promo_res.generation_id
    new_graph = rt_root / new_active["graph_path"]

    ro_conn = connect_sqlite(new_graph, readonly=True)
    try:
        r_prom = ro_conn.execute(
            "SELECT canonical_status, canonical_eligible, retrieval_eligible, approval_reference FROM assertion WHERE assertion_id='asrt_cand_promote_1'"
        ).fetchone()
        assert tuple(r_prom) == ("canonical", 1, 1, "APPROVAL-E2E-FAST-001")

        r_dup = ro_conn.execute(
            "SELECT canonical_status, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id='asrt_cand_dup_2'"
        ).fetchone()
        assert tuple(r_dup) == ("historical_candidate", 0, "asrt_cand_promote_1")

        ev_rows = ro_conn.execute(
            "SELECT evidence_id FROM assertion_evidence WHERE assertion_id='asrt_cand_promote_1' ORDER BY evidence_id"
        ).fetchall()
        assert [r[0] for r in ev_rows] == ["ev_1", "ev_2"]

        fts_cnt = ro_conn.execute(
            "SELECT COUNT(*) FROM search_fts WHERE object_id='asrt_cand_promote_1'"
        ).fetchone()[0]
        assert fts_cnt == 1
    finally:
        ro_conn.close()


def test_transcript_turn_resolver_and_intake_action_locator_e2e(tmp_path: Path) -> None:
    """Verify diarized and single-channel transcript turn speaker resolution and Output 02 notes action locator fallback."""
    transcript_turn_resolver._TRANSCRIPT_CACHE.clear()
    md_file = tmp_path / "01_10-30-2026_hitl_resolved_transcript.md"
    md_file.write_text(
        """# Forensic Meeting Transcript
Participants:
- **Amit Ayer** (PartnerAdvisory)
- **harper Hoffman** (Apex Global Enterprises)
- **Jen Stark** (PartnerAdvisory)

> Notice: This session was recorded through a single input channel without native speaker diarization.

### [04:15]
**Alex Host:** [04:15] We are trying to build the tool with my leadership for Q4 readiness.

### [08:40]
**Alex Host:** [08:40] You have been working on this and we need to constrain the domain for manufacturer metadata.

### [12:05]
**Alex Host:** [12:05] Let us recap what we said about the two weeks to do some OCR validation.
""",
        encoding="utf-8",
    )

    spk_mandy = transcript_turn_resolver.resolve_diarized_transcript_turn_speaker(
        str(md_file),
        "[04:15]",
        "We are trying to build the tool with my leadership for Q4 readiness.",
    )
    assert spk_mandy == "harper Hoffman"

    spk_amit = transcript_turn_resolver.resolve_diarized_transcript_turn_speaker(
        str(md_file),
        "00:08:40",
        "You have been working on this and we need to constrain the domain for manufacturer metadata.",
    )
    assert spk_amit == "Amit Ayer"

    spk_jen = transcript_turn_resolver.resolve_diarized_transcript_turn_speaker(
        str(md_file),
        "12:05",
        "Let us recap what we said about the two weeks to do some OCR validation.",
    )
    assert spk_jen == "Jen Stark"

    # Verify HIST-01 notes source action locator fallback when timestamp is absent from notes file
    notes_src = tmp_path / "source_notes.md"
    notes_src.write_text(
        """# Architecture Notes
## Action Items
Finalize the S3 bucket IAM policy for drone imagery ingestion before Friday.
""",
        encoding="utf-8",
    )
    recap_md = """# Executive Recap
# Action & Governance Ledger
| Action ID | Action Description | Owner | Due Date | Status | Source Locator |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ACT-01 | Finalize the S3 bucket IAM policy for drone imagery ingestion before Friday. | Eduardo | 2026-11-05 | Open | [14:22] |
"""
    actions = prepare_evidence_envelope_intake.parse_output02_action_ledger(
        recap_md, source_path=notes_src
    )
    assert len(actions) == 1
    assert actions[0]["action_id"] == "action-out02-01"
    # Because [14:22] is not in notes_src, it must fall back to section/line locator instead of failing timestamp validation
    assert actions[0]["locator"]["kind"] != "timestamp"
