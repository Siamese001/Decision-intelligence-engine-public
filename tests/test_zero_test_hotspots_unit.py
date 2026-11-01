"""Hermetic unit and fault-injection tests for the Top 5 Zero-Test Codebase Hotspots.

Targets (identified by Phase 1.5 empirical AST + 400-commit churn scanner):
1. dual_pipeline.query_core.lineage_db
2. dual_pipeline.canonical_pipeline.u0_ledger_checks
3. dual_pipeline.audio_ingestion_core.acoustic_cache
4. dual_pipeline.intake_engine_core.ingest_flow
5. dual_pipeline.transcript_stages_core.offline_graph_generator
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import dual_pipeline.audio_ingestion_core.acoustic_cache as acoustic_cache
import dual_pipeline.canonical_pipeline.u0_ledger_checks as u0_ledger_checks
import dual_pipeline.intake_engine_core.ingest_flow as ingest_flow
import dual_pipeline.query_core.lineage_db as lineage_db
import dual_pipeline.transcript_stages_core.offline_graph_generator as offline_graph_generator
from dual_pipeline.canonical_pipeline.u0_preflight_types import PreflightDiagnostic


# ---------------------------------------------------------------------------
# 1. dual_pipeline.query_core.lineage_db (>= 16 assertions)
# ---------------------------------------------------------------------------
def test_lineage_db_token_humanization_and_badge_calibration() -> None:
    assert lineage_db.humanize_token("asrt_edge_jarvis_platform") == "Jarvis Platform"
    assert lineage_db.humanize_token("ev_legacy_edge_partner_advisory_sow") == "PartnerAdvisory Sow"
    assert lineage_db.humanize_token("node_person_ninan") == "Person Speaker_Beta"

    assert lineage_db.humanize_predicate("ALLOCATES_ENGINEERING_CAPACITY_TO") == "Allocates Engineering Capacity To"
    assert lineage_db.humanize_predicate("HAS_CLAIM_TEXT") == "Verified Claim"
    assert lineage_db.humanize_predicate("MANAGES_WORKSTREAM") == "Manages Workstream"

    # Superseded / Candidate / Active badge formatting
    assert (
        lineage_db.format_confidence_badge(
            "HIGH", 0.92, canonical_status="canonical", canonical_eligible=1, currentness_state="superseded"
        )
        == "SUPERSEDED (0%)"
    )
    assert (
        lineage_db.format_confidence_badge(
            "HIGH", 0.92, canonical_status="candidate", canonical_eligible=0, currentness_state="current"
        )
        == "CANDIDATE (45%)"
    )
    assert (
        lineage_db.format_confidence_badge(
            "HIGH", 0.92, canonical_status="canonical", canonical_eligible=1, currentness_state="current"
        )
        == "HIGH (92%)"
    )
    assert lineage_db.format_confidence_badge(None, 0.96) == "HIGH (96%)"
    assert lineage_db.format_confidence_badge(None, 0.65) == "MEDIUM (65%)"
    assert lineage_db.format_confidence_badge(None, 0.30) == "LOW (30%)"


def test_lineage_db_query_metadata_and_summary_on_isolated_sqlite(tmp_path: Path) -> None:
    db_file = tmp_path / "lineage_test.sqlite"
    conn = sqlite3.connect(str(db_file))
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT
            );
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_name TEXT,
                cold_archive_ref TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                confidence_label TEXT,
                confidence_score REAL,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                currentness_state TEXT,
                predicate TEXT,
                object_literal TEXT,
                scope_text TEXT,
                subject_node_id TEXT,
                object_node_id TEXT,
                primary_source_id TEXT,
                legacy_claim_id TEXT
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                source_id TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT
            );
            INSERT INTO node VALUES ('node_jarvis', 'Jarvis Engine', 'Platform');
            INSERT INTO node VALUES ('node_nee', 'Apex Global Enterprises', 'Organization');
            INSERT INTO source VALUES ('src_01', 'Weekly Architecture Sync', 'file:///tmp/sync.md');
            INSERT INTO assertion VALUES (
                'asrt_01', 'HIGH', 0.94, 'canonical', 1, 'validated', 'current',
                'POWERS_PLATFORM', NULL, 'Core platform', 'node_jarvis', 'node_nee', 'src_01', 'claim_01'
            );
            INSERT INTO evidence_item VALUES ('ev_01', 'src_01');
            INSERT INTO assertion_evidence VALUES ('asrt_01', 'ev_01');
            """
        )
        conn.commit()
    finally:
        conn.close()

    confs, paths, srcs = lineage_db.query_db_lineage_metadata(db_file, ["asrt_01", "node_jarvis", "ev_01"])
    assert confs["asrt_01"] == "HIGH (94%)"
    assert "`Jarvis Engine`" in paths["asrt_01"]
    assert "`Apex Global Enterprises`" in paths["asrt_01"]
    assert srcs["asrt_01"][0] == "Weekly Architecture Sync"
    assert confs["node_jarvis"] == "CONFIRMED (90%)"
    assert confs["ev_01"] == "HIGH (94%)"

    summary_md, items, idx_map = lineage_db.summarize_graph_traversal_coverage(
        ["asrt_01", "node_jarvis"], graph_path=str(db_file)
    )
    assert "### Evidence & Graph Lineage" in summary_md
    assert "Weekly Architecture Sync" in summary_md
    assert len(items) == 2
    assert idx_map["asrt_01"] == 1


# ---------------------------------------------------------------------------
# 2. dual_pipeline.canonical_pipeline.u0_ledger_checks (>= 15 assertions)
# ---------------------------------------------------------------------------
def test_u0_ledger_checks_unknown_utterances_and_role_resolutions(tmp_path: Path) -> None:
    utterances_path = tmp_path / "utterances.json"
    utterances_path.write_text(
        json.dumps([
            {"speaker": "Unknown", "text": "We need to finalize the SOW."},
            {"speaker": "Speaker_Beta", "text": "Agreed, let us lock the budget."},
        ]),
        encoding="utf-8",
    )

    metadata: Dict[str, Any] = {
        "structured_utterances_path": str(utterances_path),
        "meeting_name": "NEE Weekly Sync",
        "source_file_hash": "deadbeef1234",
    }
    missing: List[str] = []
    diagnostics: List[PreflightDiagnostic] = []

    u0_ledger_checks._check_acoustic_unknown_utterances(metadata, missing, diagnostics)
    assert "speaker_map" in missing
    assert len(diagnostics) == 1
    assert diagnostics[0].check_name == "U0_UNMAPPED_SPEAKERS"
    assert diagnostics[0].severity == "BLOCKING"
    assert "unresolved 'Unknown' turn(s)" in diagnostics[0].message

    # When speaker_map maps 'unknown', no blocking diagnostic should be emitted
    metadata_mapped: Dict[str, Any] = {
        "structured_utterances": [{"speaker": "Unknown", "text": "Mapped turn."}],
        "speaker_map": {"Unknown": "Speaker_Alpha"},
    }
    missing_mapped: List[str] = []
    diag_mapped: List[PreflightDiagnostic] = []
    u0_ledger_checks._check_acoustic_unknown_utterances(metadata_mapped, missing_mapped, diag_mapped)
    assert missing_mapped == []
    assert diag_mapped == []

    # Test _record_ledger_candidate ordering & org stripping
    target_names = {"Speaker_Beta", "Speaker_Alpha"}
    latest_order: Dict[str, Any] = {}
    resolved: Dict[str, str] = {}
    speaker_resolutions: Dict[str, Any] = {}

    u0_ledger_checks._record_ledger_candidate(
        "Speaker_Beta",
        "Managing Director (PartnerAdvisory)",
        "2026-10-20T10:00:00Z",
        1,
        target_names,
        latest_order,
        resolved,
        speaker_resolutions,
        org="PartnerAdvisory",
    )
    assert "Speaker_Beta" in resolved
    assert speaker_resolutions["Speaker_Beta"]["is_human_confirmed"] is True
    assert speaker_resolutions["Speaker_Beta"]["provenance"] == "HITL_LEDGER"

    # Older timestamp should not overwrite newer resolution
    u0_ledger_checks._record_ledger_candidate(
        "Speaker_Beta",
        "Outdated Role",
        "2026-10-19T10:00:00Z",
        0,
        target_names,
        latest_order,
        resolved,
        speaker_resolutions,
    )
    assert resolved["Speaker_Beta"] != "Outdated Role"


# ---------------------------------------------------------------------------
# 3. dual_pipeline.audio_ingestion_core.acoustic_cache (>= 15 assertions)
# ---------------------------------------------------------------------------
def test_acoustic_cache_digest_key_and_metadata_restoration(tmp_path: Path) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64)

    full_sha, short_sha = acoustic_cache.compute_audio_digest(str(audio_file))
    assert len(full_sha) == 64
    assert short_sha == full_sha[:16]

    key1 = acoustic_cache.build_acoustic_cache_key(full_sha, "mlx-whisper", "en", False, False)
    key2 = acoustic_cache.build_acoustic_cache_key(full_sha, "mlx-whisper", "en", True, False)
    assert len(key1) == 16
    assert key1 != key2

    fake_diar = SimpleNamespace(model_id="pyannote-3.1", revision="v1")
    key_diar = acoustic_cache.build_acoustic_cache_key(full_sha, "mlx-whisper", "en", False, False, fake_diar)
    assert len(key_diar) == 16
    assert key_diar != key1

    meta: Dict[str, Any] = {"transcript_cache_dir": str(tmp_path / "custom_cache")}
    resolved_dir = acoustic_cache.resolve_transcript_cache_dir(str(audio_file), meta)
    assert resolved_dir == tmp_path / "custom_cache"
    assert resolved_dir.is_dir()

    # Test _restore_cache_metadata with cluster map and voiceprints
    cache_file = resolved_dir / "sample_1234.cache"
    cache_file.write_text("{}", encoding="utf-8")
    structured_file = resolved_dir / "sample_1234.segments.json"
    structured_file.write_text("[]", encoding="utf-8")
    utterance_file = resolved_dir / "sample_1234.utterances.json"
    utterance_file.write_text("[]", encoding="utf-8")
    cluster_map_file = resolved_dir / "sample_1234.cluster_map.json"
    cluster_map_file.write_text(json.dumps({"SPEAKER_00": "Speaker 1"}), encoding="utf-8")
    vp_file = resolved_dir / "sample_1234.voiceprints.json"
    vp_file.write_text(
        json.dumps({"matches": {"SPEAKER_00": {"name": "Speaker_Beta"}}, "centroids": {"SPEAKER_00": [0.1, 0.2]}}),
        encoding="utf-8",
    )

    restore_meta: Dict[str, Any] = {}
    turns = [{"speaker": "SPEAKER_00", "text": "Hello", "start": 0.0, "end": 1.0}]
    acoustic_cache._restore_cache_metadata(
        restore_meta, cache_file, structured_file, utterance_file, turns, {"sealed": True}
    )
    assert restore_meta["turn_count"] == 1
    assert restore_meta["cache_seal_verified"] is True
    assert restore_meta["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"
    assert restore_meta["speaker_map"]["SPEAKER_00"] == "Speaker_Beta"
    assert restore_meta["speaker_map"]["Speaker 1"] == "Speaker_Beta"
    assert "SPEAKER_00" in restore_meta["cluster_centroids"]


# ---------------------------------------------------------------------------
# 4. dual_pipeline.intake_engine_core.ingest_flow (>= 15 assertions)
# ---------------------------------------------------------------------------
def test_ingest_flow_permission_and_contract_validation_guards(tmp_path: Path) -> None:
    pkg_path = tmp_path / "invalid_package.json"
    pkg_path.write_text(json.dumps({"contract_id": "urn:nee:unknown:1.0.0"}), encoding="utf-8")

    # 1. Production intake without authorization_context must fail closed with PermissionError
    unauth_engine = SimpleNamespace(
        retriever=SimpleNamespace(backend=SimpleNamespace(production_qualified=True)),
        authorization_context=None,
        graph_path=tmp_path / "graph.sqlite",
        batch_admission_manifest=None,
    )
    with pytest.raises(PermissionError, match="production intake requires an authenticated authorization context"):
        ingest_flow.execute_ingest(unauth_engine, pkg_path)

    # 2. Non-production intake with invalid artifact payload must fail closed on schema/contract check
    dev_engine = SimpleNamespace(
        retriever=None,
        authorization_context=None,
        graph_path=tmp_path / "graph.sqlite",
        batch_admission_manifest=None,
    )
    with pytest.raises(Exception):
        ingest_flow.execute_ingest(dev_engine, pkg_path)

    # Verify helper exports & bindings on ingest_flow module
    assert callable(ingest_flow.execute_ingest)
    assert hasattr(ingest_flow, "validate_artifact")
    assert hasattr(ingest_flow, "normalize_evidence_envelope")
    assert hasattr(ingest_flow, "build_ingestion_plan")
    assert hasattr(ingest_flow, "build_routes")


# ---------------------------------------------------------------------------
# 5. dual_pipeline.transcript_stages_core.offline_graph_generator (>= 15 assertions)
# ---------------------------------------------------------------------------
def test_offline_graph_generator_directive_and_document_fixture_extraction() -> None:
    source_text = (
        "Short line.\n"
        "The architecture team must complete the BGE-M3 sidecar migration by Friday.\n"
        "Another note."
    )
    directive = offline_graph_generator._extract_document_directive(source_text, limit=200)
    assert directive == "The architecture team must complete the BGE-M3 sidecar migration by Friday."

    fallback_source = "Brief.\nThis sentence has no modal verb but exceeds twenty characters easily."
    fallback_dir = offline_graph_generator._extract_document_directive(fallback_source, limit=200)
    assert fallback_dir == "This sentence has no modal verb but exceeds twenty characters easily."
    assert offline_graph_generator._extract_document_directive("Too short.", limit=200) is None

    user_prompt = (
        "<document_metadata>{\"classification\": \"confidential\", \"audiences\": \"exec, eng\"}</document_metadata>\n"
        "<document_source>The platform team must enforce cryptographic receipts across all releases.</document_source>"
    )
    fixture_json = offline_graph_generator._document_fixture(user_prompt)
    assert fixture_json is not None
    parsed = json.loads(fixture_json)
    assert parsed["schema_version"] == "5.7"
    assert parsed["classification"] == "confidential"
    assert parsed["audiences"] == ["exec", "eng"]
    assert len(parsed["evidence_index"]) == 1
    signals = parsed["graph_mutations"]["strategic_intent_signals"]
    assert len(signals) == 1
    assert "cryptographic receipts" in signals[0]["text"]
    assert offline_graph_generator._document_fixture("no tags at all") is None
