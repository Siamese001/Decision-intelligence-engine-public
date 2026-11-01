"""Wave 1 Counterfactual Regression Suite: CredentialManager SSOT Routing & Silent Exception Hardening.

Verifies:
1. All 10 target modules resolve environment variables via `CredentialManager` with zero raw `os.environ` accesses.
2. All 7 target attachment, audio ingestion, and canonical publication modules emit structured `logger.debug` records on caught cleanup/fallback exceptions rather than using bare `except Exception: pass`.
3. Functional behavior of each modified helper under hermetic environment overrides and simulated fallback exceptions.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

WAVE1_ENV_MODULES = [
    "dual_pipeline/spark/client.py",
    "dual_pipeline/spark/session.py",
    "pipeline_b_extraction/agents/spark_extraction_coordinator.py",
    "dual_pipeline/token_optimization/prompt_prefix_aligner.py",
    "dual_pipeline/integrations/ci_cd.py",
    "dual_pipeline/canonical_pipeline/c0_pa_runner.py",
    "dual_pipeline/canonical_pipeline/concurrency.py",
    "dual_pipeline/transcript_qa_core/eval_cache.py",
    "dual_pipeline/llm_judge_core/sampling.py",
    "dual_pipeline/cli/model_resolution.py",
]

WAVE1_EXCEPTION_MODULES = [
    "dual_pipeline/attachment_core/detector.py",
    "dual_pipeline/audio_ingestion_core/diarization_engine.py",
    "dual_pipeline/audio_ingestion_core/mlx_transcriber.py",
    "dual_pipeline/canonical_pipeline/attachments.py",
    "dual_pipeline/canonical_pipeline/checkpoint_memoizer.py",
    "dual_pipeline/canonical_pipeline/publication_journal.py",
    "dual_pipeline/canonical_pipeline/publication_verifier.py",
]


def _find_os_environ_lines(file_path: Path) -> List[int]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    lines: List[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                lines.append(getattr(node, "lineno", 0))
    return lines


def _find_silent_except_pass_lines(file_path: Path) -> List[int]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    lines: List[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                lines.append(getattr(node, "lineno", 0))
    return lines


@pytest.mark.parametrize("rel_path", WAVE1_ENV_MODULES)
def test_wave1_zero_raw_os_environ_reads(rel_path: str) -> None:
    """Target modules must route environment reads through CredentialManager, not raw os.environ."""
    path = REPO_ROOT / rel_path
    offending_lines = _find_os_environ_lines(path)
    assert offending_lines == [], f"{rel_path} still contains raw os.environ accesses at lines {offending_lines}"


@pytest.mark.parametrize("rel_path", WAVE1_EXCEPTION_MODULES)
def test_wave1_zero_silent_except_pass_blocks(rel_path: str) -> None:
    """Target modules must log caught fallback/cleanup exceptions via logger.debug instead of bare pass."""
    path = REPO_ROOT / rel_path
    offending_lines = _find_silent_except_pass_lines(path)
    assert offending_lines == [], f"{rel_path} still contains silent `except ...: pass` blocks at lines {offending_lines}"


def test_wave1_credential_manager_env_routing_behavior(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify functional behavior of CredentialManager routing across modified modules."""
    from storage.credentials import CredentialManager
    from dual_pipeline.spark.client import DataprocBatchControllerClient
    from dual_pipeline.spark.session import ManagedSparkSessionFactory
    from pipeline_b_extraction.agents.spark_extraction_coordinator import SparkExtractionCoordinator
    from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner
    from dual_pipeline.integrations.ci_cd import write_to_github_summary
    from dual_pipeline.canonical_pipeline.concurrency import MeetingIngestionLock
    from dual_pipeline.transcript_qa_core.eval_cache import _get_cache_db_path
    from dual_pipeline.llm_judge_core.sampling import check_self_evaluation

    with CredentialManager.temporary_env({
        "GOOGLE_CLOUD_PROJECT": "test-gcp-proj",
        "SPARK_REGION": "us-east1",
        "REASONING_EFFORT": "high",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_summary.md"),
        "TEST_SANDBOX_DIR": str(tmp_path / "sandbox"),
        "NEE_CACHE_DIR": str(tmp_path / "cache"),
        "PRODUCTION_MODE": "1",
    }):
        client = DataprocBatchControllerClient(location="")
        assert client.project_id == "test-gcp-proj"
        assert client.location == "us-east1"

        session_factory = ManagedSparkSessionFactory()
        assert session_factory.config.project_id == "test-gcp-proj"
        assert session_factory.config.location == "us-east1"

        coord = SparkExtractionCoordinator(mock_mode=True)
        assert coord.project_id == "test-gcp-proj"

        budget = PromptPrefixAligner._calculate_stage03_budget(2000, {}, 1.0)
        assert budget["reasoning_effort"] == "high"
        assert budget["budget_tokens"] == 8192

        assert write_to_github_summary("### Gate Summary") is True
        assert "### Gate Summary" in (tmp_path / "step_summary.md").read_text(encoding="utf-8")

        lock = MeetingIngestionLock("testkey123")
        assert str(tmp_path / "sandbox" / "locks") in str(lock.lock_dir)

        cache_db = _get_cache_db_path()
        assert cache_db == (tmp_path / "cache" / "judge_evaluation_cache.sqlite")

        err = check_self_evaluation("gpt-5.6-sol", {})
        assert err is not None and "FAIL CLOSED" in err


def test_wave1_exception_logging_behavior(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify fallback exception paths emit debug telemetry without crashing."""
    from dual_pipeline.attachment_core.detector import _sync_drop_log_to_out_dir
    from dual_pipeline.canonical_pipeline.attachments import _load_passing_decision
    from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _freeze_deliverable
    from dual_pipeline.canonical_pipeline.publication_verifier import _resolve_from_active_pair

    caplog.set_level(logging.DEBUG)

    # 1. _sync_drop_log_to_out_dir with non-existent source file logs debug instead of crashing
    _sync_drop_log_to_out_dir(tmp_path / "missing_drop_log.json", tmp_path / "out")

    # 2. _load_passing_decision with corrupt JSON logs debug and returns None
    corrupt_dir = tmp_path / "attempt_1"
    corrupt_dir.mkdir(parents=True, exist_ok=True)
    (corrupt_dir / "final_release_decision.json").write_text("{corrupt json", encoding="utf-8")
    assert _load_passing_decision(corrupt_dir) is None

    # 3. _freeze_deliverable with failing state_machine logs debug and completes
    dummy_sm = MagicMock()
    dummy_sm.get_frozen_content.side_effect = RuntimeError("simulated healer lookup error")
    meta = {}
    _freeze_deliverable(dummy_sm, "output_2", "# Recap", meta, out_p= None)
    assert meta.get("output_2_memoized") is True

    # 4. _resolve_from_active_pair with corrupt active_pair.json returns None and logs debug
    rt_dir = tmp_path / "rt"
    rt_dir.mkdir(parents=True, exist_ok=True)
    (rt_dir / "active_pair.json").write_text("{bad json", encoding="utf-8")
    assert _resolve_from_active_pair(rt_dir) is None

    assert any(rec.levelno == logging.DEBUG for rec in caplog.records)
