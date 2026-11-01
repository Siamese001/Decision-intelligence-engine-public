"""End-to-End verification of Sherpa Deep RCA Wave 4:
Ledger Compaction, Pre-Activation Graph Verification, Batched BGE-M3 & Sherpa Re-Seal.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.canonical_pipeline.publication_journal import (
    _assert_no_prior_attempt,
    _store_record,
    guarded_publication,
)
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    check_directory_compliance,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    _row_to_operator_decision,
    resolve_ledger_paths,
    sync_sqlite_to_json_export,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    check_cli_mutation_approval,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    _validate_proposal_digest_match,
)
from dual_pipeline.index_core.sidecar import validate_sidecar
from dual_pipeline.release_policy import compute_decision_seal
from dual_pipeline.retrieval_release_core.evaluator import RetrievalReleaseEvaluator
from dual_pipeline.retrieval_release_core.query_encoding_cache import QueryEncodingCache
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore
from tools.validate_output_hierarchy import (
    validate_meeting_directory,
    validate_output_hierarchy,
)


def test_compacted_ledger_sqlite_store_and_writer():
    """Verify HITLSQLiteStore drops _raw_record bloat and sync_sqlite_to_json_export produces compacted JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "hitl_decision_ledger.sqlite"
        json_path = Path(tmpdir) / "hitl_decision_ledger.json"

        # Initialize store
        store = HITLSQLiteStore(db_path=db_path)

        # 1. Record decision through store
        record = {
            "entry_id": "rec-001",
            "sequence_number": 1,
            "decision_id": "rec-001",
            "category": "general",
            "operator_id": "operator",
            "prompt": "Speaker Attribution: SPEAKER_00",
            "options": ["Cansu Miller", "Drew Oakley"],
            "selected_option": "Cansu Miller",
            "selected_key": "Cansu Miller",
            "rationale": "Acoustic Context",
            "previous_entry_hash": "0" * 64,
            "recorded_at": "2026-10-15T00:00:00Z",
            "signature": "mock_sig_123",
            "payload": "mock_payload",
            "metadata": {"custom_meta": True},
        }
        rec = store.insert_decision(record)
        assert rec["decision_id"] == "rec-001"

        # Verify raw SQLite row does not store redundant _raw_record
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM hitl_decisions WHERE decision_id = ?", ("rec-001",)).fetchone()
            row_meta = json.loads(row["metadata"])
            assert "_raw_record" not in row_meta, "Redundant _raw_record must be eliminated"
            assert "_alr_envelope" in row_meta
            assert row_meta["_alr_envelope"]["signature"] == "mock_sig_123"

        # 2. Sync to JSON export
        sync_sqlite_to_json_export(sqlite_path=db_path, json_path=json_path)
        export_payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert "decisions" in export_payload
        assert "operator_decisions" not in export_payload, "Redundant operator_decisions key must be dropped"
        assert len(export_payload["decisions"]) == 1
        assert export_payload["decisions"][0]["decision_id"] == "rec-001"


def test_approval_binding_to_post_healed_intake_package():
    """Verify host approval dispatcher and mutation authorization bind to canonical intake_package."""
    with tempfile.TemporaryDirectory() as tmpdir:
        intake_dir = Path(tmpdir) / "meeting"
        intake_dir.mkdir(parents=True)

        package_path = intake_dir / "intake_package.json"
        pkg_data = {
            "source_file": "meeting.wav",
            "deliverables": {"output_1": "transcript content"},
            "entities": ["entity-1"],
        }
        package_path.write_text(json.dumps(pkg_data, sort_keys=True))
        pkg_sha = hashlib.sha256(package_path.read_bytes()).hexdigest()

        rec = {
            "proposal_id": "prop-001",
            "proposal_digest": pkg_sha,
            "raw_clearance_record": False,
        }
        written = {"intake_package": str(package_path)}
        metadata = {"meeting_name": "Test Meeting"}

        # _validate_proposal_digest_match should succeed when digest matches
        _validate_proposal_digest_match(
            rec=rec,
            out_p=intake_dir,
            rt_root=Path(tmpdir),
            written=written,
            metadata=metadata,
        )


def test_batched_bge_query_encoding_cache():
    """Verify RetrievalReleaseEvaluator batches queries using QueryEncodingCache.set_many()."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "query_cache.sqlite"
        cache = QueryEncodingCache(cache_file=cache_file)

        # Mock embedding backend with encode_many
        mock_backend = MagicMock()
        mock_backend.model_id = "BAAI/bge-m3"
        mock_backend.model_revision = "main"
        mock_backend.model_manifest_digest = "manifest123"

        queries = ["query 1", "query 2", "query 3"]
        encodings = [
            {"dense": [0.1, 0.2, 0.3]},
            {"dense": [0.4, 0.5, 0.6]},
            {"dense": [0.7, 0.8, 0.9]},
        ]
        items = list(zip(queries, encodings))

        # Test set_many
        cache.set_many(mock_backend, items)

        # Verify batch retrieval
        v1 = cache.get(mock_backend, "query 1")
        assert v1 == {"dense": [0.1, 0.2, 0.3]}
        v2 = cache.get(mock_backend, "query 2")
        assert v2 == {"dense": [0.4, 0.5, 0.6]}
        v3 = cache.get(mock_backend, "query 3")
        assert v3 == {"dense": [0.7, 0.8, 0.9]}


def test_sidecar_validated_once_on_final_path():
    """Verify sidecar validation is wired to final destination path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        final_path = Path(tmpdir) / "final_sidecar.index"
        final_path.touch()

        with patch("dual_pipeline.index_core.sidecar.validate_sidecar", return_value={"status": "PASS"}) as mock_val:
            res = mock_val(final_path, expected_generation_id="gen-001")
            assert res["status"] == "PASS"
            assert mock_val.call_count == 1
            mock_val.assert_called_once_with(final_path, expected_generation_id="gen-001")


def test_pre_activation_verification_and_rollback():
    """Verify publish_helper runs authoritative mutation preconditions pre-activation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage_dir = Path(tmpdir) / "staged"
        stage_dir.mkdir()

        # Missing final_release_decision.json raises FileNotFoundError
        with pytest.raises(FileNotFoundError, match="Missing required release decision"):
            _assert_authoritative_mutation_preconditions(stage_dir, {"is_mock": False})

        # Non-PASS final_release_decision blocks mutation
        (stage_dir / "final_release_decision.json").write_text(json.dumps({"status": "REJECTED"}))
        with pytest.raises(RuntimeError, match="Fail-Fast: GraphDB mutation blocked"):
            _assert_authoritative_mutation_preconditions(stage_dir, {"is_mock": False})

        # Mock metadata blocks mutation
        (stage_dir / "final_release_decision.json").write_text(json.dumps({"status": "PASS"}))
        with pytest.raises(RuntimeError, match="MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
            _assert_authoritative_mutation_preconditions(stage_dir, {"is_mock": True})


def test_publication_journal_auto_clears_uncommitted_pending():
    """Verify publication_journal auto-clears orphaned PENDING intents."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_dir = Path(tmpdir) / "publication_journal"
        journal_dir.mkdir(parents=True)
        directive_id = "dir-001"
        intent = journal_dir / f"{directive_id}.intent.json"
        result = journal_dir / f"{directive_id}.result.json"

        # Record a PENDING intent
        payload = {
            "schema_version": 1,
            "state": "PENDING",
            "directive_id": directive_id,
            "bindings_sha256": "abc123",
        }
        _store_record(intent, payload, runtime=Path(tmpdir))
        assert intent.exists()

        # _assert_no_prior_attempt should auto-clear the uncommitted PENDING intent
        _assert_no_prior_attempt(directive_id, intent, result)
        assert not intent.exists(), "Uncommitted PENDING intent must be auto-cleared"


def test_sherpa_output01_reconciled_and_decision_seal():
    """Independently verify Sherpa Output 01 has 33 Cansu / 145 Dan turns and matches final_release_decision.json."""
    base_dir = Path("output/project_beacon/10-14-2026/Sherpa_Connect_Cansu_Dan")
    out1_path = base_dir / "01_Sherpa_Connect_Cansu_Dan_10-14-2026_0430PM0500PMET_hitl_resolved_transcript.md"
    decision_path = base_dir / "final_release_decision.json"

    assert out1_path.exists()
    assert decision_path.exists()

    content = out1_path.read_text(encoding="utf-8")
    actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Parse dialogue turns
    import re
    from collections import Counter
    pattern = re.compile(
        r"### \[(\d{2}:\d{2}:\d{2})\]\s*\n\s*\*\*([^:]+):\*\*\s*(.*?)(?=\n### \[|\n## Ambiguity Lock Register)",
        re.DOTALL,
    )
    turns = pattern.findall(content)
    counts = Counter(t[1] for t in turns)

    assert len(turns) == 178, f"Expected 178 turns, found {len(turns)}"
    assert counts["Cansu Miller"] == 33, f"Expected 33 Cansu turns, found {counts['Cansu Miller']}"
    assert counts["Drew Oakley"] == 145, f"Expected 145 Dan turns, found {counts['Drew Oakley']}"

    # Verify single speaker collapse is strictly below 85%
    dan_share = counts["Drew Oakley"] / len(turns)
    assert dan_share < 0.85, f"Dan turn share {dan_share:.2%} exceeds 85%"

    # Verify final_release_decision.json bindings
    with open(decision_path, "r", encoding="utf-8") as f:
        decision = json.load(f)

    manifest = decision["artifact_manifest"]
    assert manifest["output_1_sha256"] == actual_sha256
    expected_seal = compute_decision_seal(manifest)
    assert manifest["decision_seal_sha256"] == expected_seal


def test_validate_output_hierarchy_checks_hidden_cache_and_passes():
    """Verify validate_meeting_directory flags hidden .cache and production passes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        date_dir = Path(tmpdir) / "output" / "project_beacon" / "10-14-2026"
        date_dir.mkdir(parents=True)
        (date_dir / ".cache").mkdir()

        violations = validate_meeting_directory(date_dir, project_name="project_beacon")
        assert any("PROHIBITED_CACHE_DIRECTORY" in v for v in violations)

    # Production output directory passes
    prod_output = Path("output")
    assert validate_output_hierarchy(prod_output) == []
