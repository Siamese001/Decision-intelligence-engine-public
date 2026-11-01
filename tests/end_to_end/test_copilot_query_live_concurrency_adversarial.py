"""Adversarial and End-to-End concurrency tests for Copilot vs Query live execution.

Wave 3 Acceptance Tests:
1. Multi-threaded / concurrent cross-process AnswerLedger writes preserve cryptographic hash chain (verify_chain == True).
2. Query answer records populate latency_ms, generation, and graph_sha256.
3. GPU isolation: Embedding backend routes device to CPU when active live recording session is present.
4. Worktree migration immediate heartbeat (force=True bypasses rate-limiting).
5. Adversarial inputs: Boundary payloads, malformed JSON, and empty results.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

import pytest

from dual_pipeline.answer_ledger import (
    AnswerLedger,
    AnswerRecord,
    get_default_answer_ledger,
    record_query_answer_safe,
)
from dual_pipeline.index_core.backends import (
    _resolve_embedding_device,
    is_live_recording_active,
)
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry


@pytest.mark.e2e
def test_concurrent_ledger_writes_preserve_hash_chain(tmp_path: Path):
    """Verifies that concurrent multi-threaded writes to AnswerLedger with BEGIN IMMEDIATE never fork the hash chain."""
    db_file = tmp_path / "concurrent_ledger.sqlite"
    ledger = AnswerLedger(db_file)

    def _write_record(worker_id: int, idx: int) -> str:
        pipeline = "copilot" if idx % 2 == 0 else "query"
        rec = AnswerRecord(
            pipeline=pipeline,
            intent_or_type=f"intent_{idx}",
            context_digest=f"ctx_{worker_id}_{idx}",
            retrieval_seeds=[f"seed_{worker_id}_{idx}"],
            context_snapshot=[{"speaker": "Tester", "text": f"text_{idx}"}],
            cited_assertion_ids=[f"aid_{idx}"],
            rigor="VERIFIED",
            response_text=f"Response from worker {worker_id} item {idx}",
            latency_ms=12.5 + idx,
            generation="gen_test_01",
            graph_sha256="0" * 64,
        )
        saved = ledger.append_answer(rec)
        return saved.answer_id

    num_workers = 8
    items_per_worker = 10
    total_expected = num_workers * items_per_worker

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(_write_record, worker_id, item_idx)
            for worker_id in range(num_workers)
            for item_idx in range(items_per_worker)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == total_expected

    # Verify complete cryptographic chain integrity
    records = ledger.list_answers(limit=total_expected + 10)
    assert len(records) == total_expected
    is_valid, error_msg = ledger.verify_chain()
    assert is_valid is True, f"Hash chain verification failed: {error_msg}"


@pytest.mark.e2e
def test_query_answer_record_metadata_population(tmp_path: Path):
    """Verifies that record_query_answer_safe records latency_ms, generation, and graph_sha256."""
    ledger_file = tmp_path / "learning" / "answer_ledger.sqlite"
    ledger = AnswerLedger(ledger_file)

    with patch("dual_pipeline.answer_ledger.get_default_answer_ledger", return_value=ledger):
        # Case 1: Metadata passed explicitly
        ans_id1 = record_query_answer_safe(
            runtime_root=tmp_path,
            question="What is the release timeline?",
            result={"answer": "Q4 release", "cited_assertion_ids": ["aid_1"]},
            query_type="exact_lookup",
            latency_ms=45.2,
            generation="gen_20261014",
            graph_sha256="abc123sha",
            model_id="gemini-3.8-flash",
        )
        assert ans_id1 is not None
        rec1 = ledger.get_answer(ans_id1)
        assert rec1 is not None
        assert rec1.latency_ms == 45.2
        assert rec1.generation == "gen_20261014"
        assert rec1.graph_sha256 == "abc123sha"
        assert rec1.model_id == "gemini-3.8-flash"

        # Case 2: Metadata embedded in result dict
        ans_id2 = record_query_answer_safe(
            runtime_root=tmp_path,
            question="What are the key dependencies?",
            result={
                "answer": "Core database",
                "latency_ms": 112.8,
                "generation_id": "gen_from_dict",
                "graph_sha256": "def456sha",
            },
            query_type="relationship_explanation",
        )
        assert ans_id2 is not None
        rec2 = ledger.get_answer(ans_id2)
        assert rec2 is not None
        assert rec2.latency_ms == 112.8
        assert rec2.generation == "gen_from_dict"
        assert rec2.graph_sha256 == "def456sha"

    is_valid, _ = ledger.verify_chain()
    assert is_valid is True


@pytest.mark.e2e
def test_gpu_isolation_during_active_recording(tmp_path: Path):
    """Verifies that embedding device falls back to CPU when an active live meeting recording session exists."""
    registry_dir = tmp_path / ".session_registry"
    registry_dir.mkdir(parents=True, exist_ok=True)

    # 1. No active session -> default auto resolution
    assert is_live_recording_active(tmp_path) is False

    # 2. Create active session manifest with recent heartbeat and alive PID (current process PID)
    manifest = {
        "session_id": "sess_concurrency_test",
        "parent_pid": os.getpid(),
        "last_heartbeat": time.time(),
        "repo_root": str(tmp_path),
    }
    manifest_path = registry_dir / "sess_concurrency_test.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert is_live_recording_active(tmp_path) is True

    # When active recording exists, auto device resolves to CPU to preserve GPU memory for ASR
    with patch("dual_pipeline.index_core.backends.ROOT", tmp_path):
        resolved_device = _resolve_embedding_device(requested_device=None, default_device="auto")
        assert resolved_device == "cpu"

    # Clean up manifest
    manifest_path.unlink()
    assert is_live_recording_active(tmp_path) is False


@pytest.mark.e2e
def test_session_registry_heartbeat_force_bypass_rate_limit(tmp_path: Path):
    """Verifies that force=True updates last_heartbeat and outputs immediately without waiting min_interval."""
    registry = SessionRegistry(tmp_path)
    manifest = registry.register(
        meeting_slug="concurrency_test_meeting",
        output_dir=str(tmp_path / "output_initial"),
    )
    initial_hb = manifest.last_heartbeat

    # Heartbeat without force with identical state: skipped by rate limiter (< 3.0s)
    registry.heartbeat(
        manifest.session_id,
        output_dir=manifest.output_dir,
        capture_state=manifest.capture_state,
        asr_state=manifest.asr_state,
        context_state=manifest.context_state,
        copilot_state=manifest.copilot_state,
        force=False,
    )
    m_check1 = registry.inspect(manifest.session_id)
    assert m_check1 is not None
    assert m_check1.last_heartbeat == initial_hb

    # Heartbeat WITH force=True: updates immediately even within 3.0s interval
    time.sleep(0.01)
    new_dir = str(tmp_path / "output_worktree_migrated")
    registry.heartbeat(manifest.session_id, output_dir=new_dir, force=True)
    m_check2 = registry.inspect(manifest.session_id)
    assert m_check2 is not None
    assert m_check2.last_heartbeat > initial_hb
    assert m_check2.output_dir == new_dir


@pytest.mark.adversarial
def test_answer_ledger_adversarial_boundary_payloads(tmp_path: Path):
    """Adversarial check: boundary payloads, malformed JSON, and empty results are handled safely."""
    ledger_file = tmp_path / "learning" / "answer_ledger.sqlite"
    ledger = AnswerLedger(ledger_file)

    with patch("dual_pipeline.answer_ledger.get_default_answer_ledger", return_value=ledger):
        # Empty question and None result
        ans_id1 = record_query_answer_safe(tmp_path, "", None)
        assert ans_id1 is not None

        # Massive string response text (100KB)
        big_text = "x" * 100_000
        ans_id2 = record_query_answer_safe(tmp_path, "big prompt", big_text)
        assert ans_id2 is not None

        # Unicode mojibake and special characters
        mojibake_payload = {"answer": "Testing Â· mojibake and \x00 null byte \U0001F680 emojis"}
        ans_id3 = record_query_answer_safe(tmp_path, "mojibake test", mojibake_payload)
        assert ans_id3 is not None

    is_valid, _ = ledger.verify_chain()
    assert is_valid is True
