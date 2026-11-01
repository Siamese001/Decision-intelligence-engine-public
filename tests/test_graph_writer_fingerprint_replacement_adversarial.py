"""Wave 2 adversarial tests for GovernedGraphWriter fingerprint replacement, canonical triggers, FK ordering, and intra-batch dedup."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import ZERO_HASH, digest, seal_artifact, sha256_file, utc_now
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from dual_pipeline.normalization_core.meeting_entities import process_projection_signals


def _seed_canonical_trigger_graph(graph_path: Path) -> None:
    conn = sqlite3.connect(str(graph_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, display_name TEXT,
            description TEXT, status TEXT, active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT, source_date TEXT,
            authority_rank INTEGER, source_posture TEXT, cold_archive_ref TEXT, notes TEXT, created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source(source_id),
            version_number INTEGER NOT NULL, source_fingerprint TEXT, content_hash TEXT NOT NULL,
            source_date_normalized TEXT, source_date_precision TEXT, source_date_status TEXT,
            authority_rank INTEGER, scope_text TEXT, posture_text TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY, algorithm_version TEXT,
            baseline_graph_sha256 TEXT, input_manifest_sha256 TEXT,
            started_at TEXT, completed_at TEXT, status TEXT, deterministic INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT, source_id TEXT NOT NULL REFERENCES source(source_id),
            source_version_id TEXT NOT NULL REFERENCES source_version(source_version_id),
            locator_kind TEXT NOT NULL, locator_value TEXT NOT NULL, page_number INTEGER,
            section_name TEXT, paragraph_number INTEGER, row_reference TEXT, slide_number INTEGER,
            transcript_timestamp TEXT, direct_quote TEXT, evidence_directness TEXT,
            source_authority_rank INTEGER, extraction_confidence REAL, corroboration_group TEXT,
            location_status TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT NOT NULL,
            subject_node_id TEXT NOT NULL REFERENCES node(node_id), predicate TEXT NOT NULL,
            legacy_predicate TEXT, object_node_id TEXT REFERENCES node(node_id),
            object_literal TEXT, object_literal_type TEXT, assertion_type TEXT,
            atomicity_state TEXT, scope_text TEXT, speaker_or_author TEXT,
            primary_source_id TEXT REFERENCES source(source_id),
            extraction_run_id TEXT REFERENCES extraction_run(extraction_run_id),
            extraction_method TEXT, origin_type TEXT, confidence_label TEXT,
            confidence_score REAL, validation_state TEXT, currentness_state TEXT,
            valid_from TEXT, valid_to TEXT, date_precision TEXT, date_status TEXT,
            recorded_at TEXT, superseded_at TEXT,
            superseded_by_assertion_id TEXT REFERENCES assertion(assertion_id),
            resolution_state TEXT, canonical_status TEXT, canonical_eligible INTEGER,
            retrieval_eligible INTEGER, materiality TEXT, classification TEXT,
            allowed_audiences_json TEXT, hitl_required INTEGER, validation_priority TEXT,
            validation_owner TEXT, qualifiers_json TEXT, legacy_record_type TEXT,
            legacy_record_ids_json TEXT, created_at TEXT, updated_at TEXT, approval_reference TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id),
            evidence_id TEXT NOT NULL REFERENCES evidence_item(evidence_id),
            evidence_relationship TEXT, independent_corroboration INTEGER, created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE assertion_label_state (
            assertion_id TEXT PRIMARY KEY REFERENCES assertion(assertion_id),
            extraction_confidence REAL, assertion_confidence_label TEXT,
            assertion_confidence_score REAL, validation_state TEXT, lifecycle_state TEXT,
            mapping_state TEXT, mapping_rule_id TEXT, updated_at TEXT
        );
        CREATE TABLE assertion_audience (
            assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id),
            audience_id TEXT NOT NULL, mapping_state TEXT, created_at TEXT,
            PRIMARY KEY (assertion_id, audience_id)
        );
        CREATE TABLE legacy_claim_assertion_map (
            claim_id TEXT PRIMARY KEY, assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id)
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT PRIMARY KEY, assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id)
        );
        CREATE TABLE object_facet_assignment (
            object_type TEXT NOT NULL, object_id TEXT NOT NULL, facet_id TEXT NOT NULL,
            PRIMARY KEY (object_type, object_id, facet_id)
        );
        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT UNIQUE,
            object_type TEXT, object_id TEXT, title TEXT, body TEXT, canonical_status TEXT,
            currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT,
            assertion_id TEXT REFERENCES assertion(assertion_id), updated_at TEXT
        );
        CREATE TABLE claim (claim_id TEXT PRIMARY KEY);
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            claim_id TEXT REFERENCES claim(claim_id),
            status TEXT
        );
        CREATE TABLE audit_event (
            audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT, action TEXT,
            record_type TEXT, record_id TEXT, previous_value_json TEXT, new_value_json TEXT,
            source_id TEXT, event_timestamp TEXT, reason TEXT, validation_reference TEXT,
            approval_reference TEXT, migration_or_ingestion_run_id TEXT, release_version TEXT
        );
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY, approval_reference TEXT, decision_scope TEXT,
            status TEXT, approved_by TEXT, approved_at TEXT, scope_json TEXT, active INTEGER DEFAULT 1
        );
        CREATE TRIGGER assertion_evidence_delete_guard_bd
        BEFORE DELETE ON assertion_evidence
        WHEN (SELECT canonical_status FROM assertion WHERE assertion_id = OLD.assertion_id) = 'canonical'
         AND (SELECT COUNT(*) FROM assertion_evidence WHERE assertion_id = OLD.assertion_id) <= 1
        BEGIN
            SELECT RAISE(ABORT, 'canonical assertion requires at least one assertion_evidence row');
        END;
        CREATE TRIGGER search_document_ai
        BEFORE INSERT ON search_document
        WHEN NEW.retrieval_eligible = 1 AND NEW.currentness_state != 'current'
        BEGIN
            SELECT RAISE(ABORT, 'historical search_document cannot be retrieval_eligible');
        END;
        """
    )
    now = utc_now()
    conn.execute(
        "INSERT INTO node VALUES ('node-01','workstream','Conduit','Conduit','Desc','approved',1,?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO source VALUES ('src-01','dual_pipeline_intake','Orals Meeting','2026-10-20',5,'derivative_internal','local://orals.md','Notes',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO source VALUES ('src-02','dual_pipeline_intake','Second Meeting','2026-10-21',5,'derivative_internal','local://second.md','Notes',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO source_version VALUES ('sv-01','src-01',1,'fp1',?,'2026-10-20','day','exact',5,'scope','derivative_internal',?)",
        ("a" * 64, now),
    )
    conn.execute(
        "INSERT INTO source_version VALUES ('sv-02','src-02',1,'fp2',?,'2026-10-21','day','exact',5,'scope','derivative_internal',?)",
        ("b" * 64, now),
    )
    conn.execute(
        "INSERT INTO extraction_run VALUES ('run-01','1.0','b','m',?,?,'completed',1)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-01','efp1','src-01','sv-01','timestamp','00:01',NULL,NULL,NULL,NULL,NULL,'00:01','Quote 1','derivative',5,0.9,'cg1','exact',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-02','efp2','src-01','sv-01','timestamp','00:02',NULL,NULL,NULL,NULL,NULL,'00:02','Quote 2','derivative',5,0.9,'cg1','exact',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO evidence_item VALUES ('ev-ext','efp3','src-02','sv-02','timestamp','00:05',NULL,NULL,NULL,NULL,NULL,'00:05','Cross quote','derivative',5,0.9,'cg2','exact',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO governance_approval VALUES ('app-01','app-01','all','approved','operator',?,?,'1')",
        (now, json.dumps({"allowed_actions": ["canonical_assertion_promotion"]})),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _make_change_set(graph_path: Path, ops: list[dict], payloads: dict[str, dict]) -> dict:
    raw = create_artifact(
        "graph-change-set",
        "cs-adv-01",
        change_set_id="cs-adv-01",
        run_id="run-adv-02",
        trace_id="a" * 32,
        status="staged",
        approval_id="app-01",
        base_graph_snapshot="snap-01",
        base_graph_hash=sha256_file(graph_path),
        transaction_required=True,
        rollback_required=True,
        operations=ops,
        extensions={
            "com.partner_advisory.nee.operation-payloads": payloads,
            "com.partner_advisory.nee.source": {
                "source_id": "src-01",
                "source_version_id": "sv-01",
                "name": "Orals Meeting",
                "date": "2026-10-20",
                "authority_rank": 5,
                "source_posture": "derivative_internal",
                "package_digest": "a" * 64,
                "evidence": [],
            },
        },
    )
    return seal_artifact(raw)


def test_canonical_and_historical_fingerprint_replacement_under_strict_triggers(tmp_path: Path) -> None:
    """Verify DEF-10-01..DEF-10-05: canonical trigger guard, FK child cleanup, historical retrieval_eligible=0, validation_queue schema, and independent_corroboration preservation."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_canonical_trigger_graph(graph_path)
    now = utc_now()

    text_canon = "Conduit enforces atomic SQLite transactions."
    fp_canon = digest(
        {
            "subject": "node-01",
            "predicate": "HAS_CLAIM_TEXT",
            "object_node_id": None,
            "object_literal": text_canon,
            "scope": text_canon,
            "source_id": "src-01",
        }
    )
    text_hist = "Deprecated v1 pipeline uses file locks."
    fp_hist = digest(
        {
            "subject": "node-01",
            "predicate": "HAS_CLAIM_TEXT",
            "object_node_id": None,
            "object_literal": text_hist,
            "scope": text_hist,
            "source_id": "src-01",
        }
    )

    conn = sqlite3.connect(str(graph_path))
    conn.execute("PRAGMA foreign_keys=ON")
    # 1. Seed a canonical assertion with child rows in all FK-bound tables and 1 same-source + 1 cross-source evidence link
    conn.execute(
        """INSERT INTO assertion VALUES (
            'assert-old-canon', ?, 'node-01', 'HAS_CLAIM_TEXT', NULL, NULL, ?, 'text',
            'decision', 'atomic', ?, 'Speaker_Alpha', 'src-01', 'run-01', 'dual_pipeline_structured_adapter',
            'human_source', 'high', 0.90, 'validated', 'current', '2026-10-20', NULL, 'day', 'exact',
            ?, NULL, NULL, 'resolved', 'canonical', 1, 1, 'high', 'internal', '["internal"]',
            0, NULL, NULL, '{}', 'claim', '[]', ?, ?, 'app-01'
        )""",
        (fp_canon, text_canon, text_canon, now, now, now),
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('assert-old-canon', 'ev-01', 'supports', 0, ?)", (now,))
    conn.execute("INSERT INTO assertion_evidence VALUES ('assert-old-canon', 'ev-ext', 'supports', 1, ?)", (now,))
    conn.execute("INSERT INTO assertion_label_state VALUES ('assert-old-canon', 0.9, 'high', 0.9, 'validated', 'active', 'native', NULL, ?)", (now,))
    conn.execute("INSERT INTO assertion_audience VALUES ('assert-old-canon', 'internal', 'native', ?)", (now,))
    conn.execute("INSERT INTO legacy_claim_assertion_map VALUES ('claim-legacy-1', 'assert-old-canon')")
    conn.execute("INSERT INTO object_facet_assignment VALUES ('assertion', 'assert-old-canon', 'facet-1')")
    conn.execute(
        "INSERT INTO search_document VALUES (NULL, 'sd-old-canon', 'assertion', 'assert-old-canon', ?, ?, 'canonical', 'current', 1, 'src-01', 'assert-old-canon', ?)",
        (text_canon, text_canon, now),
    )

    # 2. Seed a historical assertion that will be replaced by an APPROVED operation
    conn.execute(
        """INSERT INTO assertion VALUES (
            'assert-old-hist', ?, 'node-01', 'HAS_CLAIM_TEXT', NULL, NULL, ?, 'text',
            'assertion', 'atomic', ?, 'Speaker_Alpha', 'src-01', 'run-01', 'dual_pipeline_structured_adapter',
            'human_source', 'medium', 0.75, 'unvalidated', 'historical', '2026-10-20', NULL, 'day', 'exact',
            ?, ?, 'assert-old-canon', 'resolved', 'candidate', 0, 0, 'medium', 'internal', '["internal"]',
            0, NULL, NULL, '{}', 'claim', '[]', ?, ?, NULL
        )""",
        (fp_hist, text_hist, text_hist, now, now, now, now),
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('assert-old-hist', 'ev-01', 'supports', 0, ?)", (now,))
    conn.commit()
    conn.close()

    cand_canon = {
        "candidate_id": "cand-new-canon",
        "candidate_type": "decision",
        "assertion_type": "decision",
        "normalized_text": text_canon,
        "classification": "internal",
        "audiences": ["internal"],
        "evidence_ids": ["ev-02"],
        "extraction_confidence": 0.92,
        "materiality": "high",
    }
    payload_canon = {
        "candidate": cand_canon,
        "subject_node_id": "node-01",
        "predicate": "HAS_CLAIM_TEXT",
        "disposition": "APPROVED",
    }
    op_canon = {
        "operation_id": "op-new-canon",
        "sequence": 1,
        "operation": "add_approved_assertion",
        "target_type": "assertion",
        "target_id": "assert-new-canon",
        "candidate_id": "cand-new-canon",
        "classification": "internal",
        "audiences": ["internal"],
        "evidence_ids": ["ev-02"],
        "before_state_hash": ZERO_HASH,
        "after_state_hash": digest(payload_canon),
    }

    cand_hist = {
        "candidate_id": "cand-new-hist",
        "candidate_type": "assertion",
        "assertion_type": "candidate_assertion",
        "normalized_text": text_hist,
        "classification": "internal",
        "audiences": ["internal"],
        "evidence_ids": ["ev-02"],
        "extraction_confidence": 0.88,
        "materiality": "high",
    }
    payload_hist = {
        "candidate": cand_hist,
        "subject_node_id": "node-01",
        "predicate": "HAS_CLAIM_TEXT",
        "disposition": "APPROVED",
    }
    op_hist = {
        "operation_id": "op-new-hist",
        "sequence": 2,
        "operation": "add_approved_assertion",
        "target_type": "assertion",
        "target_id": "assert-new-hist",
        "candidate_id": "cand-new-hist",
        "classification": "internal",
        "audiences": ["internal"],
        "evidence_ids": ["ev-02"],
        "before_state_hash": ZERO_HASH,
        "after_state_hash": digest(payload_hist),
    }

    cs = _make_change_set(graph_path, [op_canon, op_hist], {"op-new-canon": payload_canon, "op-new-hist": payload_hist})
    res = GovernedGraphWriter(graph_path).apply(cs)
    assert res["status"] == "COMMITTED"

    conn = sqlite3.connect(str(graph_path))
    ev_rows = dict(
        conn.execute(
            "SELECT evidence_id, independent_corroboration FROM assertion_evidence WHERE assertion_id='assert-new-canon'"
        ).fetchall()
    )
    hist_a = conn.execute(
        "SELECT currentness_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id='assert-new-hist'"
    ).fetchone()
    hist_sd = conn.execute(
        "SELECT currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='assert-new-hist'"
    ).fetchone()
    conn.close()

    # Same-source prior evidence ('ev-01') must keep independent_corroboration == 0; cross-source ('ev-ext') keeps 1
    assert ev_rows["ev-01"] == 0
    assert ev_rows["ev-ext"] == 1
    assert ev_rows["ev-02"] == 0
    # Historical replacement keeps retrieval_eligible=0 and rebinds superseded_by_assertion_id to 'assert-new-canon'
    assert hist_a == ("historical", 0, "assert-new-canon")
    assert hist_sd == ("historical", 0)


def test_intra_batch_duplicate_fingerprint_and_slug_profile_dedup(tmp_path: Path) -> None:
    """Verify DEF-10-06 intra-batch duplicate fingerprint handling and undeclared relationship endpoint slug resolution."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_canonical_trigger_graph(graph_path)

    text_dup = "Intra-batch duplicate claim text."
    c1 = {
        "candidate_id": "cand-b1",
        "candidate_type": "assertion",
        "normalized_text": text_dup,
        "classification": "internal",
        "audiences": ["internal"],
        "evidence_ids": ["ev-01"],
        "extraction_confidence": 0.80,
        "materiality": "medium",
    }
    c2 = {
        "candidate_id": "cand-b2",
        "candidate_type": "assertion",
        "normalized_text": text_dup,
        "classification": "internal",
        "audiences": ["internal"],
        "evidence_ids": ["ev-02"],
        "extraction_confidence": 0.85,
        "materiality": "medium",
    }
    p1 = {"candidate": c1, "subject_node_id": "node-01", "predicate": "HAS_CLAIM_TEXT", "disposition": "CANDIDATE"}
    p2 = {"candidate": c2, "subject_node_id": "node-01", "predicate": "HAS_CLAIM_TEXT", "disposition": "CANDIDATE"}
    op1 = {
        "operation_id": "op-b1", "sequence": 1, "operation": "add_candidate_assertion",
        "target_type": "assertion", "target_id": "assert-b1", "candidate_id": "cand-b1",
        "classification": "internal", "audiences": ["internal"], "evidence_ids": ["ev-01"],
        "before_state_hash": ZERO_HASH, "after_state_hash": digest(p1),
    }
    op2 = {
        "operation_id": "op-b2", "sequence": 2, "operation": "add_candidate_assertion",
        "target_type": "assertion", "target_id": "assert-b2", "candidate_id": "cand-b2",
        "classification": "internal", "audiences": ["internal"], "evidence_ids": ["ev-02"],
        "before_state_hash": ZERO_HASH, "after_state_hash": digest(p2),
    }
    cs = _make_change_set(graph_path, [op1, op2], {"op-b1": p1, "op-b2": p2})
    res = GovernedGraphWriter(graph_path).apply(cs)
    assert res["status"] == "COMMITTED"
    assert all(op["after_row_hash"] != ZERO_HASH for op in res["operations"])

    # Also verify relationship endpoint slug resolution reuses declared entity profile
    projection = {
        "entity_upserts": [
            {"id": "person:brien_m", "canonical_name": "Speaker_Alpha M", "entity_type": "person", "evidence_ids": ["ev-01"]},
            {"id": "workstream:infra_auto", "canonical_name": "Infra Auto", "entity_type": "workstream", "evidence_ids": ["ev-01"]},
        ],
        "relationship_signals": [
            {"signal_id": "sig-01", "from_id": "stakeholder:brien-m", "to_id": "program:infra-auto", "predicate": "LEADS", "evidence_ids": ["ev-01"]},
        ],
    }
    cands: list[dict] = []
    profiles, _ = process_projection_signals(
        projection=projection,
        candidates=cands,
        evidence=[{"evidence_id": "ev-01", "locator": {"kind": "timestamp", "value": "00:01"}}],
        evidence_by_identifier={},
        run_id="run-slug-test",
        meeting_title="Orals",
        classification="internal",
        default_audiences=["internal"],
    )
    rel_cand = next(c for c in cands if c["candidate_type"] == "architecture_relationship")
    sids = [m["stable_identifier"] for m in rel_cand["entity_mentions"]]
    assert sids == ["person:brien_m", "workstream:infra_auto"]
    assert all(p.get("cross_source_dedup") is True for p in profiles.values())
