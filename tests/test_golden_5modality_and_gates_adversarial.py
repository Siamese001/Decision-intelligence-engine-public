"""Wave 7 Adversarial Tests: Mock-Heavy E2E Rejection, Core Stubbed Mock & Fixture Env Scoping."""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

from tools.audit_testing_gaps import TestFileInfo, TestingGapAuditor, is_true_runtime_e2e
from tools.lint_e2e_runtime_authenticity import E2ERuntimeAuthenticityAuditor
from tools.verify_tier1_cached_replay import has_tier1_replay_scope, verify_tier1_cached_replay

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_adversarial_mock_heavy_e2e_disqualified_from_true_runtime_e2e() -> None:
    """Proves mock-heavy E2E files get is_true_runtime_e2e == False and ERR_E2E_CORE_STUBBED_MOCK."""
    fake_e2e_src = (
        "from unittest.mock import patch\n"
        "from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request\n"
        "def test_fake_e2e():\n"
        "    with patch('dual_pipeline.live_meeting_recorder_core.copilot_engine.execute_copilot_request'):\n"
        "        assert True\n"
    )
    tree = ast.parse(fake_e2e_src)
    info = TestFileInfo(path="tests/end_to_end/test_fake_e2e.py", name="test_fake_e2e")
    auditor = TestingGapAuditor(REPO_ROOT)
    auditor._analyze_ast(tree, fake_e2e_src, info)

    assert info.is_mock_heavy is True
    assert info.is_true_runtime_e2e is False
    assert is_true_runtime_e2e(info) is False

    auth_auditor = E2ERuntimeAuthenticityAuditor("tests/end_to_end/test_fake_e2e.py", fake_e2e_src, tree)
    violations = auth_auditor.audit()
    codes = {v[1] for v in violations}
    assert "ERR_E2E_CORE_STUBBED_MOCK" in codes


def test_adversarial_tier1_replay_boundary_and_missing_fixtures(tmp_path: Path) -> None:
    """Handles empty target scopes and missing fixture directories fail-closed."""
    assert has_tier1_replay_scope([]) is False
    assert has_tier1_replay_scope(["docs/README.md"]) is False
    assert has_tier1_replay_scope(["dual_pipeline/live_meeting_recorder_core/copilot_intents.py"]) is True

    with pytest.raises(FileNotFoundError):
        verify_tier1_cached_replay(fixture_dir=tmp_path / "nonexistent_fixtures", force=True)


def test_adversarial_run_stage_scopes_allow_test_fixtures_env(monkeypatch) -> None:
    """Proves NEE_ALLOW_TEST_FIXTURES is not leaked to non-test linter stages."""
    import sys
    from tools import readiness_core

    monkeypatch.setenv("NEE_ALLOW_TEST_FIXTURES", "1")
    ok1, out1, _ = readiness_core.run_stage(
        "Purity",
        [sys.executable, "-c", "import os; print(os.environ.get('NEE_ALLOW_TEST_FIXTURES', 'ABSENT'))"],
    )
    assert ok1 is True
    assert out1.strip() == "ABSENT"

    monkeypatch.delenv("NEE_ALLOW_TEST_FIXTURES", raising=False)
    ok2, out2, _ = readiness_core.run_stage(
        "Tests",
        [sys.executable, "-c", "import os; print(os.environ.get('NEE_ALLOW_TEST_FIXTURES', 'ABSENT'))", "pytest"],
    )
    assert ok2 is True
    assert out2.strip() == "1"
