"""
End-to-End tests for Wave 5 (CAT-4 P0 & P1):
1. Frozen artifact gate fails closed when final_release_decision.json rejects output_2 without bootstrapping stage_seals.json.
2. Publication verifier filters historical and quarantined assertions and binds subject_node_id in text fallback.
3. Gate 30 mutex resolution updates currentness_state='historical' and canonical_status='superseded' and demotes sidecar records.
4. _record_attempt_artifacts persists metadata.json and try_resume_passing_attempt verifies input_hash and binding_sha256.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.attachments import try_resume_passing_attempt
from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
)
from dual_pipeline.canonical_pipeline.lifecycle import _record_attempt_artifacts
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.publication_verifier import (
    record_publication_fact_coverage,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.graph_signal_core.evaluator import evaluate_graph_signal
from dual_pipeline.release_core.helpers import (
    _attempt_interactive_mutex_resolution,
    _only_preexisting_mutex_conflicts,
)
from dual_pipeline.remediation_core.artifact_validator import (
    validate_frozen_artifact_gate,
)


def _create_graph_and_sidecar(tmp_path: Path) -> tuple[Path, Path]:
    g_path = tmp_path / "graph.sqlite"
    s_path = tmp_path / "sidecar.sqlite"
    conn_g = sqlite3.connect(str(g_path))
    conn_g.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            claim_text TEXT,
            scope_text TEXT,
            assertion_type TEXT DEFAULT 'factual',
            confidence_score REAL DEFAULT 0.9,
            origin_type TEXT DEFAULT 'EXTRACTED',
            validation_state TEXT DEFAULT 'VALIDATED',
            currentness_state TEXT DEFAULT 'current',
            canonical_status TEXT DEFAULT 'canonical',
            retrieval_eligible INTEGER DEFAULT 1,
            is_quarantined INTEGER DEFAULT 0,
            primary_source_id TEXT DEFAULT 'src-1',
            recorded_at TEXT DEFAULT '2026-10-28T00:00:00Z',
            created_at TEXT DEFAULT '2026-10-28T00:00:00Z',
            updated_at TEXT DEFAULT '2026-10-28T00:00:00Z'
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_relationship TEXT DEFAULT 'supports'
        );
        CREATE TABLE search_document (
            object_id TEXT PRIMARY KEY,
            currentness_state TEXT DEFAULT 'current',
            canonical_status TEXT DEFAULT 'canonical',
            retrieval_eligible INTEGER DEFAULT 1
        );
        """
    )
    conn_g.execute("INSERT INTO source VALUES ('src-1')")
    conn_g.commit()
    conn_g.close()

    conn_s = sqlite3.connect(str(s_path))
    conn_s.executescript(
        """
        CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            object_id TEXT,
            lane TEXT,
            artifact_json TEXT
        );
        """
    )
    conn_s.commit()
    conn_s.close()
    return g_path, s_path


def test_frozen_artifact_gate_rejects_failed_final_release_decision(tmp_path: Path) -> None:
    out_dir = tmp_path / "meeting_out"
    out_dir.mkdir(parents=True)
    draft_2 = out_dir / "02_Standup_10-28-2026_executive_recap.md"
    draft_2.write_text("# Executive Recap\n\n" + ("Detailed summary text. " * 20), encoding="utf-8")

    dec = {
        "status": "FAIL",
        "output_1_pass": True,
        "output_2_pass": False,
        "output_2_deterministic_gates_pass": False,
        "output_2_llm_score_pass": False,
    }
    (out_dir / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")

    res = validate_frozen_artifact_gate(out_dir, "output_2", {"meeting_name": "Standup"})
    assert res.valid is False
    assert not (out_dir / "stage_seals.json").exists()


def test_publication_verifier_rejects_historical_and_quarantined_assertions(tmp_path: Path) -> None:
    g_path, _ = _create_graph_and_sidecar(tmp_path)
    conn = sqlite3.connect(str(g_path))
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('person:alice', 'PERSON', 'Alice', 'Alice')"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('person:bob', 'PERSON', 'Bob', 'Bob')"
    )
    # Alice's role is historical; Bob has a current assertion mentioning Alice in scope_text
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state, is_quarantined) "
        "VALUES ('a-hist', 'person:alice', 'HAS_ROLE', 'Architect', 'Alice is Architect', 'historical', 0)"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state, is_quarantined) "
        "VALUES ('a-quar', 'person:alice', 'HAS_ROLE', 'Architect', 'Alice is Architect', 'current', 1)"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state, is_quarantined) "
        "VALUES ('a-bob', 'person:bob', 'HAS_ROLE', 'Lead', 'Alice and Bob discussed HAS_ROLE Architect', 'current', 0)"
    )
    conn.commit()
    conn.close()

    item = EvidenceItem(
        item_id="ev-1",
        item_type=ItemType.SPEAKER_ROLE.value,
        is_eligible_graph_fact=True,
        state=EvidenceState.PROPOSED.value,
        source_origin={"source_type": "TRANSCRIPT", "source_hash": "a" * 64},
        disposition_reason="Proposed in intake",
        proposed_fact={"subject": "person:alice", "predicate": "HAS_ROLE", "object": "Architect"},
    )
    manifest = EvidenceCoverageManifest(
        manifest_id="mf-1",
        run_id="run-1",
        source_digest="b" * 64,
        items=[item],
    )
    passed, violations, receipt = verify_fact_coverage_in_graph(
        graph_db=g_path,
        manifest=manifest,
        proposal_digest="c" * 64,
        activated_generation_id="gen-1",
        output_dir=tmp_path,
    )
    assert passed is False
    assert receipt["status"] == "FAILED"
    assert len(violations) == 1

    written: dict[str, str] = {}
    with pytest.raises(RuntimeError, match="Publication fact coverage verification failed"):
        record_publication_fact_coverage(
            res={"status": "COMPLETE"},
            written=written,
            auth_info={"proposal_digest": "c" * 64},
            metadata={
                "target_graph": str(g_path),
                "evidence_manifest": manifest,
                "strict_publication_coverage": True,
            },
            output_dir=tmp_path,
            activated_generation_id="gen-1",
        )


def test_gate30_mutex_resolution_updates_historical_and_syncs_sidecar(tmp_path: Path) -> None:
    g_path, s_path = _create_graph_and_sidecar(tmp_path)
    conn_g = sqlite3.connect(str(g_path))
    for nid, name in [("person:alice", "Alice"), ("person:bob", "Bob"), ("workstream:ws1", "WS1")]:
        conn_g.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES (?, 'ENTITY', ?, ?)",
            (nid, name, name),
        )
    conn_g.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, currentness_state, canonical_status) "
        "VALUES ('a-1', 'person:alice', 'OWNS_OR_LEADS', 'workstream:ws1', 'Alice leads WS1', 'current', 'canonical')"
    )
    conn_g.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, currentness_state, canonical_status) "
        "VALUES ('a-2', 'person:bob', 'OWNS_OR_LEADS', 'workstream:ws1', 'Bob leads WS1', 'current', 'canonical')"
    )
    conn_g.execute("INSERT INTO assertion_evidence VALUES ('a-1', 'supports'), ('a-2', 'supports')")
    conn_g.execute("INSERT INTO search_document VALUES ('a-1', 'current', 'canonical', 1), ('a-2', 'current', 'canonical', 1)")
    conn_g.commit()
    conn_g.close()

    conn_s = sqlite3.connect(str(s_path))
    conn_s.execute(
        "INSERT INTO retrieval_record VALUES ('a-1', 'a-1', 'approved', '{\"currentness_state\": \"current\", \"canonical_status\": \"canonical\"}')"
    )
    conn_s.execute(
        "INSERT INTO retrieval_record VALUES ('a-2', 'a-2', 'approved', '{\"currentness_state\": \"current\", \"canonical_status\": \"canonical\"}')"
    )
    conn_s.commit()
    conn_s.close()

    post_eval = evaluate_graph_signal(g_path, s_path)
    assert len(post_eval["mutex_conflicts"]) == 1
    assert post_eval["mutex_conflicts"][0]["subjects"] == ["person:alice", "person:bob"]

    # Subject-set replacement with identical scalar count must NOT be treated as pre-existing
    fake_pre = {"mutex_conflicts": [{"target": "workstream:ws1", "predicate": "OWNS_OR_LEADS", "count": 2, "subjects": ["person:alice", "person:charlie"]}]}
    assert _only_preexisting_mutex_conflicts(fake_pre, post_eval) is False

    mock_store = MagicMock()
    mock_store.get_decisions.return_value = []
    mock_store.calibrate_options.side_effect = lambda opts, **kw: opts
    resolved = _attempt_interactive_mutex_resolution(
        g_path, s_path, post_eval, interactive_resolver=lambda _env: "person:alice", store=mock_store
    )
    assert resolved is True

    conn_g = sqlite3.connect(str(g_path))
    row_loser = conn_g.execute("SELECT currentness_state, canonical_status, retrieval_eligible FROM assertion WHERE assertion_id = 'a-2'").fetchone()
    conn_g.close()
    assert row_loser == ("historical", "superseded", 0)

    conn_s = sqlite3.connect(str(s_path))
    lane_loser = conn_s.execute("SELECT lane FROM retrieval_record WHERE record_id = 'a-2'").fetchone()[0]
    conn_s.close()
    assert lane_loser == "historical"


def test_record_attempt_artifacts_writes_metadata_and_enforces_binding_on_resume(tmp_path: Path) -> None:
    out_p = tmp_path / "out_attempt"
    syn = SynthesisResult(
        output_1="# Forensic\n**[00:01:00] Alice:** Hello world.",
        output_2="# Recap\nSummary",
        output_3='{"entities": []}',
    )
    ev = QAEvaluationResult(
        passed=True,
        qa_report={"overall_pass": True},
        judge_report={"verdict": "PASS"},
        final_decision={"status": "PASS", "authoritative_write_authorized": True},
        authoritative_write_authorized=True,
    )
    meta = {
        "input_hash": "hash-111",
        "binding_sha256": "bind-222",
        "collateral_digest": "collat-333",
        "filename_slug": "Test",
        "date_filename": "10-28-2026",
    }
    _record_attempt_artifacts(out_p, 1, ev, syn, meta)
    assert (out_p / "attempts" / "attempt_1" / "metadata.json").is_file()

    # Matching metadata resumes
    assert try_resume_passing_attempt(out_p, dict(meta), CanonicalModality.MEETING_TRANSCRIPT) is not None

    # Mismatched input_hash or binding_sha256 is rejected
    assert try_resume_passing_attempt(
        out_p, {**meta, "input_hash": "hash-999"}, CanonicalModality.MEETING_TRANSCRIPT
    ) is None
    assert try_resume_passing_attempt(
        out_p, {**meta, "binding_sha256": "bind-999"}, CanonicalModality.MEETING_TRANSCRIPT
    ) is None
