"""Wave 6 E2E Tests: AST Reverse-Dependency Blast-Radius Cone & Post-Rebase Verification."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from tools.ast_dependency_cone import (
    check_import_cone_overlap,
    resolve_impacted_tests,
)
from tools.readiness_planner import (
    check_disjoint_base_advance_equivalence,
    filter_impacted_test_modules,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_env() -> dict[str, str]:
    banned = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    env = {k: v for k, v in os.environ.items() if k not in banned}
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


def test_e2e_copilot_module_edit_resolves_transitive_e2e_replay_test() -> None:
    """Editing copilot_intents.py or copilot_roster_bridge.py resolves test_copilot_ed_live_session_replay_e2e."""
    impacted = resolve_impacted_tests(
        ["dual_pipeline/live_meeting_recorder_core/copilot_intents.py"],
        repo_root=REPO_ROOT,
    )
    assert "tests.end_to_end.test_copilot_ed_live_session_replay_e2e" in impacted
    assert "tests.test_copilot_live_priority_and_speech_wave3" in impacted

    filtered = filter_impacted_test_modules(
        ["dual_pipeline/live_meeting_recorder_core/copilot_roster_bridge.py"],
        test_mode="impacted_only",
        base_modules=["tests.test_runtime_evidence_claims"],
        repo_root=REPO_ROOT,
    )
    assert "tests.end_to_end.test_copilot_ed_live_session_replay_e2e" in filtered
    assert "tests.test_copilot_live_transcript_identity_wave1" in filtered
    # Transitive mega-hubs (>50 callers) are capped at depth >= 1 so leaf edits do not explode to 150+ unrelated modules
    assert len(filtered) < 125




def test_e2e_cross_file_rebase_cone_overlap_blocks_fast_path_rebind(tmp_path: Path) -> None:
    """When main changes File A and branch changes File B (importing A), cone overlap blocks fast-path rebind."""
    repo = tmp_path / "cone_repo"
    (repo / "dual_pipeline").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    file_a = repo / "dual_pipeline" / "mod_a.py"
    file_b = repo / "dual_pipeline" / "mod_b.py"
    test_b = repo / "tests" / "test_mod_b.py"

    file_a.write_text("def compute(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    file_b.write_text(
        "from dual_pipeline.mod_a import compute\n\ndef run_b(val: int) -> int:\n    return compute(val)\n",
        encoding="utf-8",
    )
    test_b.write_text(
        "from dual_pipeline.mod_b import run_b\n\ndef test_run_b():\n    assert run_b(2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "base commit"], cwd=repo, env=env, check=True)
    old_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Main advances by changing signature in mod_a.py
    file_a.write_text("def compute(x: int, scale: int) -> int:\n    return (x + 1) * scale\n", encoding="utf-8")
    subprocess.run(["git", "add", "dual_pipeline/mod_a.py"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "main contract change in mod_a"], cwd=repo, env=env, check=True)
    new_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    has_overlap, overlap_files = check_import_cone_overlap(
        ["dual_pipeline/mod_b.py"],
        ["dual_pipeline/mod_a.py"],
        repo_root=repo,
    )
    assert has_overlap is True
    assert "dual_pipeline/mod_b.py" in overlap_files
    assert "tests/test_mod_b.py" in overlap_files

    equiv, reason = check_disjoint_base_advance_equivalence(
        old_base,
        new_base,
        ["dual_pipeline/mod_b.py"],
        repo_root=repo,
    )
    assert equiv is False
    assert "Overlapping AST import cone" in reason
