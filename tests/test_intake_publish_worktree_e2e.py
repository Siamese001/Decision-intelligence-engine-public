"""End-to-end and adversarial edge-case tests for intake-publish worktree isolation."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import (
    assemble_intake_publish_payload,
    ensure_intake_publish_worktree_isolation,
)
from dual_pipeline.cli.handlers import _intake_publish
from dual_pipeline.cli.parser import build_parser


def _init_temp_git_repo(repo_root: Path) -> None:
    """Create a hermetic temporary git repository checked out on main."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, env=env, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, env=env, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Operator"], cwd=repo_root, env=env, check=True, capture_output=True)
    (repo_root / ".githooks").mkdir(parents=True, exist_ok=True)
    (repo_root / ".venv").mkdir(parents=True, exist_ok=True)
    (repo_root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, env=env, check=True, capture_output=True)


def test_parser_intake_publish_enforces_worktree_and_blocks_bypass() -> None:
    """Adversarial invariant bypass attempt: --no-enforce-worktree is blocked fail-closed."""
    parser = build_parser()
    parsed = parser.parse_args(["intake-publish", "--package", "output/project_nexus/09-14-2026/Draft_Charters/intake_package.json"])
    assert parsed.enforce_worktree is True
    assert parsed.cleanup_worktree is False
    assert parsed.worktree_dir is None

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([
            "intake-publish",
            "--package",
            "output/project_nexus/09-14-2026/Draft_Charters/intake_package.json",
            "--no-enforce-worktree",
        ])
    assert exc_info.value.code == 1


def test_ensure_intake_publish_worktree_isolation_provisions_worktree_from_root_output(tmp_path: Path) -> None:
    """Exact production regression: root output/project_nexus/09-14-2026/Draft_Charters auto-provisions worktree."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_temp_git_repo(repo_root)

    src_dir = repo_root / "output" / "project_nexus" / "09-14-2026" / "Draft_Charters"
    src_dir.mkdir(parents=True)
    pkg_file = src_dir / "intake_package.json"
    pkg_payload = {"package_id": "pkg-draft-charters", "entities": [{"name": "FORGE"}]}
    pkg_file.write_text(json.dumps(pkg_payload), encoding="utf-8")
    decision_file = src_dir / "final_release_decision.json"
    decision_file.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    isolated_pkgs, wt_dir, wt_branch = ensure_intake_publish_worktree_isolation([pkg_file], enforce_worktree=True)

    assert len(isolated_pkgs) == 1
    isolated_pkg = isolated_pkgs[0]
    assert wt_dir is not None
    assert wt_branch is not None
    assert "/.worktrees/" in str(isolated_pkg)
    assert wt_branch.startswith("transcript/")
    assert isolated_pkg.is_file()
    assert json.loads(isolated_pkg.read_text(encoding="utf-8")) == pkg_payload
    assert (isolated_pkg.parent / "final_release_decision.json").is_file()


def test_ensure_intake_publish_worktree_isolation_boundary_and_idempotency(tmp_path: Path) -> None:
    """Boundary payload & idempotency: empty package list, external sandbox, and already-isolated worktree."""
    # 1. Empty package list
    pkgs, wt_dir, wt_branch = ensure_intake_publish_worktree_isolation([], enforce_worktree=True)
    assert pkgs == []
    assert wt_dir is None
    assert wt_branch is None

    # 2. External sandbox path (not inside <repo_root>/output/)
    ext_pkg = tmp_path / "sandbox" / "intake_package.json"
    ext_pkg.parent.mkdir(parents=True)
    ext_pkg.write_text("{}", encoding="utf-8")
    pkgs, wt_dir, wt_branch = ensure_intake_publish_worktree_isolation([ext_pkg], enforce_worktree=True)
    assert pkgs == [ext_pkg.resolve()]
    assert wt_dir is None

    # 3. Already inside .worktrees/<slug>
    wt_existing = tmp_path / ".worktrees" / "transcript_existing_slug" / "output" / "project_nexus" / "09-14-2026" / "Draft_Charters"
    wt_existing.mkdir(parents=True)
    wt_pkg = wt_existing / "intake_package.json"
    wt_pkg.write_text("{}", encoding="utf-8")
    pkgs, wt_dir, _ = ensure_intake_publish_worktree_isolation([wt_pkg], enforce_worktree=True)
    assert pkgs == [wt_pkg.resolve()]
    assert wt_dir == str((tmp_path / ".worktrees" / "transcript_existing_slug").resolve())


def test_intake_publish_handler_e2e_worktree_routing(tmp_path: Path) -> None:
    """E2E lifecycle test: _intake_publish routes root output package into isolated worktree and records metadata."""
    repo_root = tmp_path / "repo_e2e"
    repo_root.mkdir()
    _init_temp_git_repo(repo_root)

    src_dir = repo_root / "output" / "project_nexus" / "09-14-2026" / "Draft_Charters"
    src_dir.mkdir(parents=True)
    pkg_file = src_dir / "intake_package.json"
    pkg_file.write_text(json.dumps({"package_id": "pkg-e2e"}), encoding="utf-8")

    args = argparse.Namespace(
        package=[pkg_file],
        enforce_worktree=True,
        worktree_dir=None,
        allow_unjudged_package=True,
        reviewer="Amit Ayer",
        rationale="E2E verification of worktree routing",
        a2a_finalization_receipt=None,
        directive_id="dir-e2e-test",
        scope="Test scope",
        runtime_root=tmp_path / "runtime",
        graph=tmp_path / "runtime" / "graph.sqlite",
        require_bge_m3=False,
        batch_manifest=None,
    )

    mock_guard = MagicMock(unsafe=True)
    mock_guard.status.side_effect = [
        FileNotFoundError(),
        {"approval": {"decision": "APPROVE"}, "required_embedding_target": "bge-m3"},
    ]
    mock_guard.open.return_value = {
        "classification": {
            "embedding_recommendation": "PROCEED",
            "classification_digest": "a" * 64,
        },
        "required_embedding_target": "bge-m3",
    }
    mock_guard.complete_noop.return_value = {"status": "COMPLETE", "mutation_summary": {}}

    mock_engine = MagicMock()
    mock_engine.ingest.return_value = {"run_id": "run-1", "graph_operation_count": 0}

    mock_manager = MagicMock()
    mock_manager.active_path.is_file.return_value = True
    mock_manager.active_pair.return_value = {"generation_id": "gen-123"}

    with (
        patch("dual_pipeline.release_policy.verify_authoritative_release_gate"),
        patch("dual_pipeline.cli.handlers._backend") as mock_backend,
        patch("dual_pipeline.cli.handlers._authorization"),
        patch("dual_pipeline.cli.handlers._bind_model_authorization"),
        patch("dual_pipeline.cli.handlers.ReleaseManager", return_value=mock_manager),
        patch("dual_pipeline.cli.handlers._assert_backend_matches_pair"),
        patch("dual_pipeline.cli.handlers._completion_guard", return_value=mock_guard),
        patch("dual_pipeline.cli.intake_approval_helper.resolve_intake_publish_approval", return_value=(None, {"approval": {"decision": "APPROVE"}})),
        patch("dual_pipeline.cli.handlers._intake_engine", return_value=mock_engine),
    ):
        mock_backend.return_value.production_qualified = False
        result = _intake_publish(args)

    assert result["status"] == "COMPLETE"
    assert result.get("worktree_dir") is not None
    assert "/.worktrees/" in result["worktree_dir"]
    assert result.get("worktree_branch", "").startswith("transcript/")
    ingested_pkg_arg = mock_engine.ingest.call_args[0][0]
    assert "/.worktrees/" in str(ingested_pkg_arg)
