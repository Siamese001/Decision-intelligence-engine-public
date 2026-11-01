"""End-to-end lifecycle tests for dual-route M5 parallel orchestration (Cached-WAV & Document)."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Dict

from dual_pipeline.canonical_pipeline.parallel_orchestrator import (
    MODE_1_CACHED_AUDIO_FULL_SYNTHESIS,
    MODE_2_DOCUMENT_OUTPUT3_INGESTION,
    get_m5_hardware_profile,
    run_named_tasks_parallel,
    should_enable_parallel_orchestration,
)
from dual_pipeline.transcript_pipeline_core.caching import (
    cache_audio_transcript,
    get_cached_audio_transcript,
)


def test_e2e_dual_route_gating_and_sandbox_isolation() -> None:
    """Verify Route 1 (Cached WAV) and Route 2 (Document Output 03) end-to-end gating in sandbox."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_e2e_") as sandbox_str:
        sandbox = Path(sandbox_str)
        cache_dir = sandbox / "transcript_cache"

        # 1. Provision cached WAV source
        wav_file = sandbox / "architecture_sync.wav"
        wav_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x02" * 256)
        transcript_text = (
            "Speaker 1 (00:00): We confirmed the M5 48GB unified memory orchestration plan.\n"
            "Speaker 2 (00:14): Agreed, parallel mode applies only to cached audio and document ingestion."
        )
        cache_audio_transcript(wav_file, transcript_text, cache_dir=cache_dir)
        assert get_cached_audio_transcript(wav_file, cache_dir=cache_dir) == transcript_text

        wav_meta: Dict[str, Any] = {
            "project": "project_m5",
            "meeting": "Architecture Sync",
            "date": "10/17/2026",
            "transcript_cache_dir": str(cache_dir),
            "outputs": ["01", "02", "03"],
            "mutate_graph": True,
        }
        route1_ok, route1_mode = should_enable_parallel_orchestration(
            str(wav_file), wav_meta, mutate_graph=True
        )
        assert route1_ok is True
        assert route1_mode == MODE_1_CACHED_AUDIO_FULL_SYNTHESIS

        # 2. Provision Document source (Output 03 only + graph mutation)
        doc_file = sandbox / "decision_memo.md"
        doc_file.write_text(
            "# Decision Memo\n\n**Date:** 10/17/2026\n\nApproved M5 48GB parallel scheduler.",
            encoding="utf-8",
        )
        doc_meta: Dict[str, Any] = {
            "project": "project_m5",
            "is_document": True,
            "canonical_modality": "DOCUMENT",
            "output3_only": True,
            "outputs": ["03"],
            "mutate_graph": True,
        }
        route2_ok, route2_mode = should_enable_parallel_orchestration(
            str(doc_file), doc_meta, mutate_graph=True
        )
        assert route2_ok is True
        assert route2_mode == MODE_2_DOCUMENT_OUTPUT3_INGESTION

        # 3. Verify concurrent task coordination in sandbox
        profile = get_m5_hardware_profile()
        results = run_named_tasks_parallel(
            {
                "route1": lambda: should_enable_parallel_orchestration(str(wav_file), wav_meta, True),
                "route2": lambda: should_enable_parallel_orchestration(str(doc_file), doc_meta, True),
            },
            max_workers=profile.cpu_workers,
        )
        assert results["route1"] == (True, MODE_1_CACHED_AUDIO_FULL_SYNTHESIS)
        assert results["route2"] == (True, MODE_2_DOCUMENT_OUTPUT3_INGESTION)


def test_e2e_wave2_cached_wav_parallel_l2_fork_and_checkpoints() -> None:
    """Verify Wave 2 in-fork Stage 02/03 checkpoints and hash pinning for Cached-WAV."""
    from contextlib import nullcontext
    from dual_pipeline.canonical_pipeline.child_dispatch import run_parallel_l2_fork
    from dual_pipeline.canonical_pipeline.parallel_orchestrator import (
        bind_verified_stage_checkpoint_pins,
    )

    class _DummyStage:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def execute(self, *args: Any, **kwargs: Any) -> str:
            return self._payload

    class _DummyPipeline:
        def __init__(self) -> None:
            self.l2_recap = _DummyStage("## Executive Recap\nSummary of M5 orchestration.")
            self.l2_graph = _DummyStage('{"entity_upserts": [{"entity_id": "system:m5_orchestrator"}]}')

    class _DummyRecorder:
        def span(self, _name: str) -> Any:
            return nullcontext()

    meta: Dict[str, Any] = {
        "source_text": "Speaker_Alpha: We approved the M5 48GB parallel orchestrator.",
    }
    pa_context: Dict[str, Any] = {"metadata": dict(meta)}
    artifacts: Dict[str, Any] = {}
    out_1_frozen = "**Date:** 10/17/2026\n\nSpeaker_Alpha: We approved the M5 48GB parallel orchestrator."

    recap, graph = run_parallel_l2_fork(
        pipeline=_DummyPipeline(),
        input_s2_s3=out_1_frozen,
        pa_context=pa_context,
        meta=meta,
        rerun_s2=True,
        rerun_s3=True,
        recorder=_DummyRecorder(),
        notify_step=lambda _step, _msg: None,
        attempt=1,
        current_recap="",
        current_graph="",
        artifacts=artifacts,
        checkpoint_s2_fn=lambda s2: s2 + "\n<!-- s2_healed -->",
        checkpoint_s3_fn=lambda s3: s3.replace("system:m5_orchestrator", "system:m5_orchestrator_healed"),
    )
    assert "<!-- s2_healed -->" in recap
    assert "system:m5_orchestrator_healed" in graph
    assert meta.get("_s2_checkpoint_completed_in_fork") is True
    assert meta.get("_s3_checkpoint_completed_in_fork") is True

    pins = bind_verified_stage_checkpoint_pins(meta, out_1_frozen, recap, graph)
    assert set(pins.keys()) == {"output_1", "output_2", "output_3"}


def test_e2e_wave3_document_output3_and_parallel_locator_determinism() -> None:
    """Verify Wave 3 DocumentSynthesisStrategy Output-03-only synthesis and locator determinism."""
    import copy
    import hashlib
    import json
    from dual_pipeline.canonical_pipeline.strategies.synthesis import DocumentSynthesisStrategy
    from tools.prepare_evidence_envelope_intake import (
        _read_source_text_cached,
        _validate_locators,
    )

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave3_") as tmp_str:
        sandbox = Path(tmp_str)
        doc_source = sandbox / "architecture_spec.md"
        doc_source.write_text(
            "# Architecture Specification\n\n## Section 1: Memory Budget\nM5 has 48 GB RAM.\n\n"
            "## Section 2: Parallel Gate\nOnly cached audio and documents run in parallel.\n",
            encoding="utf-8",
        )

        # Verify in-memory cache returns exact source text
        t1 = _read_source_text_cached(doc_source)
        t2 = _read_source_text_cached(doc_source)
        assert t1 == t2

        # Verify parallel _validate_locators produces identical package state as single-item validation
        base_pkg = {
            "statements": [
                {"text": "M5 has 48 GB RAM.", "locator": {"kind": "section", "value": "## Section 1: Memory Budget"}},
                {"text": "Only cached audio and documents run in parallel.", "locator": {"kind": "section", "value": "## Section 2: Parallel Gate"}},
            ],
            "actions": [],
        }
        pkg_parallel = copy.deepcopy(base_pkg)
        _validate_locators(pkg_parallel, doc_source)
        digest_parallel = hashlib.sha256(json.dumps(pkg_parallel, sort_keys=True).encode("utf-8")).hexdigest()
        digest_base = hashlib.sha256(json.dumps(base_pkg, sort_keys=True).encode("utf-8")).hexdigest()
        assert digest_parallel == digest_base

        # Verify DocumentSynthesisStrategy emits Output 03 only (output_1=None, output_2=None)
        strategy = DocumentSynthesisStrategy(provider=None)
        strategy.document_extractor.execute = lambda **_kw: '{"entity_upserts": [], "decision_updates": []}'  # type: ignore[method-assign]
        doc_meta: Dict[str, Any] = {"is_document": True, "output3_only": True}
        res = strategy.synthesize(
            source_text=t1,
            pa_context={"output_dir": str(sandbox / "out_doc"), "metadata": doc_meta},
            metadata=doc_meta,
            attempt=1,
        )
        assert res.output_1 is None
        assert res.output_2 is None
        assert res.output_3 == '{"entity_upserts": [], "decision_updates": []}'
        assert "output_3" in doc_meta.get("verified_stage_checkpoint_hashes", {})


def test_e2e_wave4_concurrent_stage7_publication_and_physical_proof() -> None:
    """Verify Wave 4 concurrent Route 1 + Route 2 Stage 7 serialization and 5-check physical proof."""
    import sqlite3
    from dual_pipeline.canonical_pipeline.parallel_orchestrator import (
        run_named_tasks_parallel,
        stage7_publication_mutex,
    )
    from dual_pipeline.graph_mutation_proof import MutationGuard

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave4_") as tmp_str:
        sandbox = Path(tmp_str)
        db_path = sandbox / "sandbox_graph.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE audit_event (event_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, payload TEXT)"
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        active_writers = 0
        max_concurrent_writers = 0

        def _publish_route(route_name: str, record_id: str) -> Dict[str, Any]:
            nonlocal active_writers, max_concurrent_writers
            with stage7_publication_mutex():
                active_writers += 1
                max_concurrent_writers = max(max_concurrent_writers, active_writers)
                try:
                    guard = MutationGuard(db_path, label=f"wave4-{route_name}")
                    c = sqlite3.connect(str(db_path))
                    c.execute(
                        "INSERT INTO audit_event (event_id, record_id, payload) VALUES (?, ?, ?)",
                        (f"evt_{route_name}", record_id, f"payload_{route_name}"),
                    )
                    c.commit()
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    c.close()
                    return guard.finalize(record_ids=[record_id], audit_events=1)
                finally:
                    active_writers -= 1

        outcomes = run_named_tasks_parallel(
            {
                "route1_wav": lambda: _publish_route("route1_wav", "rec:m5_wav_001"),
                "route2_doc": lambda: _publish_route("route2_doc", "rec:m5_doc_002"),
            },
            max_workers=2,
        )
        assert max_concurrent_writers == 1
        for route_key in ("route1_wav", "route2_doc"):
            proof = outcomes[route_key]
            assert proof["overall"] == "PASS"
            assert all(status == "PASS" for status in proof["checks"].values())
            assert len(proof["checks"]) == 5
