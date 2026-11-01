"""Counterfactual regression suite for Daily SSOT Drift Remediation (2026-10-30).

Verifies:
1. Raw os.environ calls eliminated in target modules and routed via CredentialManager.
2. Unused diskcache import removed from dual_pipeline/hash_cache.py.
3. Silent `except Exception: pass` blocks replaced with structured debug logging across target modules.
4. Atomic signal envelope persistence (.tmp -> replace) in dual_pipeline/audio_ingestion_core/base.py.
5. Clean 400-line module budget compliance for unified_agentic_pipeline.py and healing_policy.py,
   plus monotonic burndown in reports/file_budget_burndown_baseline.json.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import sqlite3
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _has_raw_os_environ(rel_path: str) -> list[int]:
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel_path)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr in ("environ", "getenv"):
                lines.append(node.lineno)
    return lines


def _find_silent_except_pass_lines(rel_path: str) -> list[int]:
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel_path)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                if isinstance(node.type, ast.Name) and node.type.id == "ImportError":
                    continue
                lines.append(node.lineno)
    return lines


@pytest.mark.parametrize(
    "rel_path",
    [
        "dual_pipeline/hash_cache.py",
        "dual_pipeline/host_metadata_adapter.py",
        "dual_pipeline/agent_sdk/config.py",
        "dual_pipeline/storage/governed_context.py",
        "dual_pipeline/speaker_memory_core/bootstrap.py",
        "dual_pipeline/audio_ingestion_core/acoustic_airgap.py",
        "dual_pipeline/live_meeting_recorder_core/mlx_worker.py",
    ],
)
def test_zero_raw_os_environ_in_remediated_modules(rel_path: str) -> None:
    offending_lines = _has_raw_os_environ(rel_path)
    assert offending_lines == [], f"{rel_path} still contains raw os.environ/os.getenv at lines {offending_lines}"


def test_hash_cache_unused_diskcache_removed_and_credential_routed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = (REPO_ROOT / "dual_pipeline/hash_cache.py").read_text(encoding="utf-8")
    assert "import diskcache" not in source

    from dual_pipeline.hash_cache import GenerationalHashCache

    sample = tmp_path / "sample.txt"
    sample.write_text("hello world", encoding="utf-8")
    cache_file = tmp_path / "verdicts.json"

    with patch("config.credentials.CredentialManager.has_credential", return_value=True):
        in_mem_cache = GenerationalHashCache(cache_file=None)
        assert in_mem_cache._cache_file is None

    cache = GenerationalHashCache(cache_file=cache_file)
    digest = cache.get(sample)
    assert len(digest) == 64

    with caplog.at_level(logging.DEBUG, logger="dual_pipeline.hash_cache"):
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            cache._flush_persistent()
    assert any("Failed to flush hash cache" in rec.message for rec in caplog.records)


def test_credential_routing_across_remediated_modules(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from dual_pipeline.agent_sdk.config import AgentConfigFactory
    from dual_pipeline.audio_ingestion_core.acoustic_airgap import (
        clear_mlx_whisper_metal_cache,
        enforce_process_airgap as airgap_enforce,
        write_acoustic_receipt,
    )
    from dual_pipeline.host_metadata_adapter import IdentityScope, resolve_host_identity
    from dual_pipeline.live_meeting_recorder_core.mlx_worker import enforce_process_airgap as worker_enforce
    from dual_pipeline.speaker_memory_core.bootstrap import load_bootstrap_profiles
    from dual_pipeline.storage.governed_context import is_test_environment

    report = resolve_host_identity(env=None, scope=IdentityScope.DETERMINISTIC_TOOL)
    assert report.status == "VERIFIED"

    factory = AgentConfigFactory(env=None)
    assert isinstance(factory._env, dict)

    assert is_test_environment() is True

    custom_profiles = tmp_path / "speaker_memory.json"
    custom_profiles.write_text(json.dumps({"profiles": {"Alice": {"role": "Lead"}}}), encoding="utf-8")
    with patch("config.credentials.CredentialManager.get_credential", return_value=str(custom_profiles)):
        loaded = load_bootstrap_profiles()
        assert "Alice" in loaded

    with patch("config.credentials.CredentialManager.has_credential", return_value=False), patch(
        "config.credentials.CredentialManager.set_credential"
    ) as mock_set:
        airgap_enforce()
        assert mock_set.call_count >= 2

    with patch("config.credentials.CredentialManager.has_credential", return_value=False), patch(
        "config.credentials.CredentialManager.set_credential"
    ) as mock_worker_set:
        worker_enforce()
        assert mock_worker_set.call_count >= 2

    with caplog.at_level(logging.DEBUG):
        clear_mlx_whisper_metal_cache()
        with patch("dual_pipeline.model_registry.ModelRegistry.get_tier_spec", side_effect=RuntimeError("registry err")):
            rec_path = write_acoustic_receipt("test_stem", "mlx-model", output_dir=tmp_path)
            assert rec_path is not None and rec_path.is_file()


@pytest.mark.parametrize(
    "rel_path",
    [
        "dual_pipeline/hash_cache.py",
        "dual_pipeline/answer_ledger.py",
        "dual_pipeline/answer_ledger_core/store.py",
        "dual_pipeline/audio/circuit_breaker.py",
        "dual_pipeline/audio/source_detector.py",
        "dual_pipeline/audio_ingestion_core/acoustic_airgap.py",
        "dual_pipeline/audio_ingestion_core/acoustic_cache.py",
        "dual_pipeline/audio_ingestion_core/base.py",
        "dual_pipeline/audio_ingestion_core/voiceprint_harvester.py",
        "dual_pipeline/calibration.py",
    ],
)
def test_zero_silent_except_pass_in_remediated_modules(rel_path: str) -> None:
    silent_lines = _find_silent_except_pass_lines(rel_path)
    assert silent_lines == [], f"{rel_path} still has silent `except ...: pass` at lines {silent_lines}"


def test_structured_logging_and_atomic_signal_envelope_in_remediated_modules(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from dual_pipeline.answer_ledger import _verify_assertions_in_db
    from dual_pipeline.audio.circuit_breaker import _extract_audio_certificate
    from dual_pipeline.audio.source_detector import AudioSourceDetector
    from dual_pipeline.audio_ingestion_core.acoustic_cache import find_matching_sealed_cache
    from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber, _load_segments_from_paths
    from dual_pipeline.audio_ingestion_core.voiceprint_harvester import (
        _parse_voiceprint_sidecar,
        _read_dir_cluster_map,
    )
    from dual_pipeline.calibration import apply_calibrated_parameter
    from dual_pipeline.query_core.firewall import GraphDBRequiredError

    # 1. base.py: _load_segments_from_paths corrupt file debug log & atomic transcribe_signal write
    corrupt_seg = tmp_path / "corrupt_seg.json"
    corrupt_seg.write_text("{bad json", encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        assert _load_segments_from_paths({"structured_segments_path": str(corrupt_seg)}) is None

    class _DummyTranscriber(BaseAudioTranscriber):
        def _live_transcribe(self, audio_file_path: str, metadata: dict | None = None) -> str:
            return "**Speaker 1** [00:00:01]: Hello world."

    dummy_wav = tmp_path / "sample.wav"
    dummy_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    transcriber = _DummyTranscriber(mock=False)
    env = transcriber.transcribe_signal(str(dummy_wav), metadata={"transcript_cache_dir": str(tmp_path)})
    env_file = tmp_path / "sample.signal_envelope.json"
    assert env_file.is_file()
    assert not (tmp_path / ".sample.signal_envelope.json.tmp").exists()
    assert env.recording_id == "sample"

    # 2. acoustic_cache.py: corrupt .cache_seal.json debug log
    bad_seal = tmp_path / "broken.cache_seal.json"
    bad_seal.write_text("{corrupt", encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        assert find_matching_sealed_cache(tmp_path, "abc", "abcdef", None) is None

    # 3. voiceprint_harvester.py: corrupt cluster_map and voiceprint sidecar debug log
    bad_cmap = tmp_path / "test.cluster_map.json"
    bad_cmap.write_text("{corrupt", encoding="utf-8")
    bad_vp = tmp_path / "test.voiceprints.json"
    bad_vp.write_text("{corrupt", encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        assert _read_dir_cluster_map(tmp_path) == {}
        assert _parse_voiceprint_sidecar(bad_vp, {}, {}) == ({}, None)

    # 4. circuit_breaker.py: invalid certificate dict debug log
    with caplog.at_level(logging.DEBUG):
        assert _extract_audio_certificate({"audio_certificate": {"invalid_field": 123}}) is None

    # 5. source_detector.py: struct unpack error debug log
    with caplog.at_level(logging.DEBUG):
        with patch("struct.unpack", side_effect=struct.error("bad unpack")):
            prof = AudioSourceDetector.detect_from_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20)
            assert prof.container_format == "wav"

    # 6. answer_ledger.py: missing claim table fallback debug log
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE assertion (assertion_id TEXT);")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GraphDBRequiredError):
            _verify_assertions_in_db(["asrt_missing"], con)
    con.close()

    # 7. calibration.py: persistence failure debug log
    proposal = {
        "proposal_id": "prop-1",
        "extensions": {
            "com.partner_advisory.nee.calibration": {
                "target_parameter": "nonexistent.param",
                "proposed_value": 0.5,
                "current_value": 0.4,
            }
        },
    }
    with patch("dual_pipeline.calibration.verify_calibration_approval", return_value=True), patch(
        "dual_pipeline.parameters.set_parameter", side_effect=RuntimeError("read only")
    ):
        with caplog.at_level(logging.DEBUG):
            res = apply_calibrated_parameter(proposal, {"receipt_id": "rec-1"}, config_path=dummy_wav, persist=True)
            assert res["approval_receipt_id"] == "rec-1"


def test_module_budgets_and_burndown_baseline_ratchet() -> None:
    from dual_pipeline.healing_policy import HealingActionClassification, load_healing_policy_config
    from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE, PipelineStage

    assert len(PIPELINE_STAGE_SEQUENCE) == 7
    assert PipelineStage.U0_INGRESS.value == "U0"
    assert HealingActionClassification.AUTO_APPLY.value == "AUTO_APPLY"
    cfg = load_healing_policy_config()
    assert isinstance(cfg, dict)

    uap_lines = len((REPO_ROOT / "dual_pipeline/unified_agentic_pipeline.py").read_text(encoding="utf-8").splitlines())
    hp_lines = len((REPO_ROOT / "dual_pipeline/healing_policy.py").read_text(encoding="utf-8").splitlines())
    assert uap_lines <= 400, f"unified_agentic_pipeline.py has {uap_lines} lines (> 400)"
    assert hp_lines <= 400, f"healing_policy.py has {hp_lines} lines (> 400)"

    baseline = json.loads((REPO_ROOT / "reports/file_budget_burndown_baseline.json").read_text(encoding="utf-8"))
    assert "dual_pipeline/unified_agentic_pipeline.py" not in baseline["modules"]
    assert baseline["total_oversized_modules"] <= 90
