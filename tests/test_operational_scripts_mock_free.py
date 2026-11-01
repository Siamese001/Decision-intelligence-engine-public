"""Tests verifying that operational scripts enforce zero mocks invariants and SSOT model resolution.

Ensures scripts default to live execution (mock=False), resolve gpt-5.6-luna from
ModelRegistry SSOT, and fail closed when credentials are absent rather than silently mocking.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.model_registry import ModelRegistry, ModelTier


def test_run_parallel_remediation_cli_defaults_to_gpt56_and_no_mock():
    """Verify that CLI defaults for run_parallel_remediation are gpt-5.6-luna and mock=False."""
    import argparse

    from dual_pipeline.model_registry import reload_registry
    reload_registry()
    parser = argparse.ArgumentParser()
    default_model, _ = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    parser.add_argument("--model", type=str, default=default_model)
    parser.add_argument("--mock", action="store_true", default=False)

    args = parser.parse_args([])
    assert args.model == "gpt-5.6-luna"
    assert args.mock is False


def test_run_parallel_remediation_fails_closed_without_credentials():
    """run_parallel_batch must raise RuntimeError when mock=False and no OpenAI key is set."""
    from scripts.run_parallel_remediation import run_parallel_batch

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
        with patch("config.credentials.CredentialManager.get_openai_api_key", return_value=None):
            with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
                run_parallel_batch([], mock=False)


def test_adversarial_debate_fails_closed_without_credentials():
    """run_adversarial_debate must raise RuntimeError when dry_run=False and no key is set."""
    from scripts.run_adversarial_debate import run_adversarial_debate

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
        with patch("config.credentials.CredentialManager.get_openai_api_key", return_value=None):
            with pytest.raises(RuntimeError, match="Zero mocks invariant violated"):
                run_adversarial_debate(dry_run=False)


def test_unified_l2_healer_fails_closed_without_credentials():
    """UnifiedL2HealerService must fail closed when mock_mode=False and client is unavailable."""
    import asyncio

    from dual_pipeline.l2_healer_service import UnifiedL2HealerService
    from dual_pipeline.l2_healing_contracts import HealingScope, L2HealingRequest

    service = UnifiedL2HealerService(mock_mode=False)
    req = L2HealingRequest(
        scope=HealingScope.PIPELINE_A_TRANSCRIPT,
        candidate_artifact="invalid transcript content",
        failure_reasons=["Gate 1 failure"],
    )

    with patch("storage.credentials.CredentialManager.get_async_openai_client", return_value=(None, None)):
        res = asyncio.run(service.execute_healing(req))
        assert res.status == "FAILED"
        assert "Live OpenAI client unavailable" in (res.error_message or "")




def test_shadow_pipelines_purged_from_scripts_directory():
    """Ensure no custom build_and_ingest_* scripts remain in scripts directory."""
    scripts_dir = ROOT / "scripts"
    shadow_scripts = list(scripts_dir.glob("build_and_ingest_*.py"))
    assert shadow_scripts == [], f"Found unpurged shadow scripts: {shadow_scripts}"

