"""Wave 7 E2E Tests: Anti-Mock E2E Gate, Golden Corpus Replay & Contract/Sidecar Pinning."""
from __future__ import annotations

from pathlib import Path

from tools.readiness_core import _build_stages
from tools.readiness_planner import plan_readiness
from tools.verify_tier1_cached_replay import verify_tier1_cached_replay

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_e2e_tier1_cached_replay_executes_canonical_pipeline_and_copilot_ae00fe03(tmp_path: Path) -> None:
    """Proves verify_tier1_cached_replay runs CanonicalAgenticPipeline and Live Copilot ae00fe03 in sandbox."""
    ok, receipt = verify_tier1_cached_replay(
        output_sandbox_dir=tmp_path / "sandbox",
        force=True,
    )
    assert ok is True, receipt
    assert receipt["verdict"] == "PASS"
    assert receipt["canonical_pipeline_verified"] is True
    assert receipt["copilot_session_ae00fe03_verified"] is True
    assert (tmp_path / "sandbox" / "01_transcript.md").is_file()
    assert (tmp_path / "sandbox" / "project_lens" / "10-28-2026" / "Ed_Skyler_Amit" / "copilot_answers.jsonl").is_file()


def test_e2e_lane1_enforces_stages_10_11_25_and_read_only_prompt_check() -> None:
    """Proves Lane 1 prompt/contract changes include Stages 10, 11, and 25 (--check)."""
    plan, indices = plan_readiness(
        ["dual_pipeline/live_meeting_recorder_core/copilot_prompt_builder.py"],
        repo_root=REPO_ROOT,
    )
    assert plan.risk_lane == 1
    assert {10, 11, 25}.issubset(set(indices))

    stages = _build_stages(
        py="python",
        target_files=["dual_pipeline/live_meeting_recorder_core/copilot_intents.py"],
        test_modules=["tests.test_runtime_evidence_claims"],
        repo_root=REPO_ROOT,
    )
    stage_25_name, stage_25_cmd = stages[24]
    assert "Prompt SSOT Integrity Linter" in stage_25_name
    assert "--check" in stage_25_cmd
    assert "--sync" not in stage_25_cmd
