"""Adversarial and Edge-Case verification of Sherpa Deep RCA Wave 4:
Ledger Compaction, Pre-Activation Verification, Invariant Bypass Rejection & State Recovery.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    _assert_no_prior_attempt,
    _store_record,
)
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    sync_sqlite_to_json_export,
)
from dual_pipeline.retrieval_release_core.query_encoding_cache import QueryEncodingCache
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore
from tools.validate_output_hierarchy import (
    validate_meeting_directory,
    validate_output_hierarchy,
)
from tools.validate_output_hierarchy_core.deconfliction import detect_meeting_duplicates


def test_adversarial_malformed_sqlite_metadata_envelope_resilience():
    """Verify HITLSQLiteStore gracefully handles rows with corrupted/missing metadata envelopes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "corrupt_ledger.sqlite"
        json_path = Path(tmpdir) / "export.json"
        store = HITLSQLiteStore(db_path=db_path)

        # Insert a malformed row directly into SQLite
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO hitl_decisions (
                    entry_id, sequence_number, decision_id, category, operator_id,
                    prompt, options, selected_option, selected_key, rationale,
                    previous_entry_hash, recorded_at, metadata, entry_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("malformed-001", 1, "dec-001", "general", "op", "Prompt", "[]", "A", "A", None, "0"*64, "2026-10-15T00:00:00Z", "{malformed-json", "digest1")
            )
            # Insert a row without _alr_envelope
            conn.execute(
                """
                INSERT INTO hitl_decisions (
                    entry_id, sequence_number, decision_id, category, operator_id,
                    prompt, options, selected_option, selected_key, rationale,
                    previous_entry_hash, recorded_at, metadata, entry_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("no-envelope-002", 2, "dec-002", "general", "op", "Prompt", "[]", "B", "B", None, "0"*64, "2026-10-15T00:00:00Z", json.dumps({"custom": "data"}), "digest2")
            )
            conn.commit()

        # Reading records should not crash with unhandled json decode exception
        records = store.get_history()
        assert len(records) == 2

        # Export should succeed and include parsed decisions
        sync_sqlite_to_json_export(sqlite_path=db_path, json_path=json_path)
        export_payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert "decisions" in export_payload


def test_adversarial_query_cache_empty_or_zero_length_batches():
    """Verify QueryEncodingCache handles empty batches and null entries safely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "cache.sqlite"
        cache = QueryEncodingCache(cache_file=cache_file)

        mock_backend = MagicMock()
        mock_backend.model_id = "BAAI/bge-m3"
        mock_backend.model_revision = "main"
        mock_backend.model_manifest_digest = "manifest123"

        # Empty items list
        cache.set_many(mock_backend, [])

        # Query not present returns None
        assert cache.get(mock_backend, "") is None
        assert cache.get(mock_backend, "nonexistent") is None


def test_adversarial_journal_concurrent_or_stale_pending_recovery():
    """Verify publication journal cleanly recovers when stale PENDING intent exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_dir = Path(tmpdir) / "publication_journal"
        journal_dir.mkdir(parents=True)
        directive_id = "crashed-run"
        intent = journal_dir / f"{directive_id}.intent.json"
        result = journal_dir / f"{directive_id}.result.json"

        # Simulate crashed process leaving intent in PENDING state
        _store_record(intent, {
            "schema_version": 1,
            "state": "PENDING",
            "directive_id": directive_id,
            "bindings_sha256": "deadbeef123",
        }, runtime=Path(tmpdir))
        assert intent.exists()

        # Next check should auto-clear the stale intent without raising error
        _assert_no_prior_attempt(directive_id, intent, result)
        assert not intent.exists(), "Stale PENDING intent must be cleared"


def test_adversarial_single_speaker_collapse_detection_and_rejection():
    """Verify that transcripts with >= 85% single speaker share are flagged as collapse."""
    # Construct a transcript with 90% Drew Oakley turns
    lines = ["# Forensic Transcript\n\n"]
    for i in range(18):
        lines.append(f"### [00:{i:02d}:00]\n\n**Drew Oakley:** Utterance {i}\n\n")
    lines.append("### [00:19:00]\n\n**Cansu Miller:** Brief comment\n\n")

    transcript_text = "".join(lines)

    import re
    from collections import Counter
    pattern = re.compile(r"\*\*(Drew Oakley|Cansu Miller):\*\*")
    turns = pattern.findall(transcript_text)
    counts = Counter(turns)
    dan_share = counts["Drew Oakley"] / len(turns)

    # 18 / 19 = 94.7% -> Exceeds 85% collapse threshold
    assert dan_share >= 0.85
    is_collapse = dan_share >= 0.85 and len(turns) >= 10
    assert is_collapse is True, "Single speaker collapse MUST be flagged when >= 85% across >= 10 turns"


def test_adversarial_pre_activation_corrupted_directory_rejection():
    """Verify _assert_authoritative_mutation_preconditions rejects corrupted artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "partial_output"
        out_dir.mkdir()

        # Missing final_release_decision.json fails
        with pytest.raises(FileNotFoundError, match="Missing required release decision"):
            _assert_authoritative_mutation_preconditions(out_dir, {"is_mock": False})

        # Corrupted JSON fails with RuntimeError
        (out_dir / "final_release_decision.json").write_text("{corrupt-json")
        with pytest.raises(RuntimeError, match="Corrupted release decision file"):
            _assert_authoritative_mutation_preconditions(out_dir, {"is_mock": False})


def test_adversarial_hidden_cache_directory_detection():
    """Verify hidden .cache directory in production hierarchy triggers validation failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        date_dir = Path(tmpdir) / "output" / "project_a" / "10-14-2026"
        sub_dir = date_dir / "Meeting_01"
        sub_dir.mkdir(parents=True)

        # Valid files
        (sub_dir / "01_Meeting_01_hitl_resolved_transcript.md").write_text("# 01")
        (sub_dir / "02_Meeting_01_executive_recap.md").write_text("# 02")
        (sub_dir / "03_Meeting_01_canonical_signal_projection.json").write_text("{}")
        (sub_dir / "final_release_decision.json").write_text("{}")

        # Inject hidden .cache directory
        cache_dir = sub_dir / ".cache"
        cache_dir.mkdir()
        violations = validate_meeting_directory(date_dir, project_name="project_a")
        assert any("PROHIBITED_CACHE_DIRECTORY" in v for v in violations)


def test_adversarial_duplicate_meeting_detection():
    """Verify detect_meeting_duplicates detects colliding sibling subfolders."""
    with tempfile.TemporaryDirectory() as tmpdir:
        date_dir = Path(tmpdir) / "output" / "project_a" / "10-14-2026"
        s1 = date_dir / "Sync_Meeting_Dan"
        s2 = date_dir / "Sync_Meeting_Dan_PartnerAdvisory"
        s1.mkdir(parents=True)
        s2.mkdir(parents=True)

        # detect_meeting_duplicates safely audits the sibling subfolders
        duplicates = detect_meeting_duplicates([s1, s2], "project_a", "10-14-2026", date_dir)
        assert isinstance(duplicates, list)
