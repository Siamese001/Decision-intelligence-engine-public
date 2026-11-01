"""
Unit tests for PR governance guards, worktree isolation invariants, and commit linters.
"""
from __future__ import annotations

from pathlib import Path

from tools.lint_commit_message import validate_commit_message
from tools.lint_model_neutral_naming import scan_path_for_model_tokens
from tools.pr_governance_core.common import CONVENTIONAL_BRANCH_RE
from tools.validate_output_hierarchy import validate_output_hierarchy


class TestCommitMessageLinter:
    """Tests for tools/lint_commit_message.py."""

    def test_valid_conventional_commits(self):
        valid_messages = [
            "feat(governance): add worktree start and finish commands",
            "fix(qa): resolve type annotation gaps in test suite",
            "chore(deps): update runtime dependency pins",
            "docs(architecture): clarify worktree isolation invariants",
            "refactor(pipeline): extract prompt builders into core module",
            "test(governance): add regression suite for PR guards",
        ]
        for msg in valid_messages:
            ok, explanation = validate_commit_message(msg)
            assert ok, f"Expected '{msg}' to be valid, got: {explanation}"

    def test_valid_merge_commits(self):
        valid_merges = [
            "Merge branch 'feat/audio-vad-priming' into main (Audio VAD Priming)",
            "Merge remote-tracking branch 'origin/main'",
            "Merge pull request #42 from org/branch",
            "merge: integrate security audit fixes",
        ]
        for msg in valid_merges:
            ok, explanation = validate_commit_message(msg)
            assert ok, f"Expected '{msg}' to be valid, got: {explanation}"

    def test_reject_dirty_dump_keywords(self):
        dirty_messages = [
            "chore: commit dirty files",
            "feat: wip work on engine",
            "chore: temp dump of artifacts",
            "fix: save files before checkout",
            "chore: save changes from yesterday",
            "refactor: checkpoint dump",
        ]
        for msg in dirty_messages:
            ok, explanation = validate_commit_message(msg)
            assert not ok, f"Expected '{msg}' to be rejected for dirty dump"
            assert "dirty-dump keyword" in explanation

    def test_reject_prohibited_model_names(self):
        model_messages = [
            "feat(eval): update gpt-6-luna benchmark parameters",
            "fix: correct sol-max reasoning effort timeout",
            "chore: update luna model prompts",
            "refactor: switch to claude-3-opus provider",
            "docs: explain gemini-1.5 multimodal handling",
        ]
        for msg in model_messages:
            ok, explanation = validate_commit_message(msg)
            assert not ok, f"Expected '{msg}' to be rejected for model naming"
            assert "prohibited model identifier" in explanation.lower()

    def test_reject_empty_and_short_messages(self):
        ok, exp = validate_commit_message("")
        assert not ok
        ok, exp = validate_commit_message("# Only a comment line\n# another")
        assert not ok
        ok, exp = validate_commit_message("feat(scope): ab")
        assert not ok
        assert "too short" in exp


class TestModelNeutralArtifactNaming:
    """Tests for tools/lint_model_neutral_naming.py."""

    def test_detects_prohibited_tokens(self):
        bad_paths = [
            "tests/test_astra_benchmark.py",
            "reports/gpt6_performance_eval.md",
            "docs/luna_migration_guide.md",
            "output/project_lens/sol_medium/01_output.md",
        ]
        for p in bad_paths:
            violations = scan_path_for_model_tokens(p)
            assert len(violations) > 0, f"Expected violations for '{p}', got none"

    def test_allows_neutral_paths(self):
        good_paths = [
            "tests/test_frontier_unshackled_config.py",
            "reports/performance_eval.md",
            "docs/migration_guide.md",
            "output/project_lens/09-18-2026/Jake_Amit_Miles_PartnerAdvisory/01_hitl_resolved_transcript.md",
        ]
        for p in good_paths:
            violations = scan_path_for_model_tokens(p)
            assert len(violations) == 0, f"Expected no violations for '{p}', got: {violations}"


class TestWorktreeBranchValidation:
    """Tests for branch convention enforcement in worktrees."""

    def test_conventional_branch_regex(self):
        valid_branches = [
            "feat/worktree-isolation",
            "feature/pre-commit-hardening",
            "fix/sqlite-timeout-wal",
            "chore/pr-governance-and-worktree-hooks",
            "refactor/transcript-pipeline-core",
            "test/pr-governance-guards",
            "ci/harden-gatekeeper",
            "docs/update-agents-rule",
            "audio/standup-9-30-mitch-partner_advisory",
            "enhance/judge-calibration",
            "ingest/meeting-infrastructure-orals",
            "transcript/working-session-lens-testing-validation",
            "ingest/doc-nea-org-chart",
            "ingest/sheet-partner_advisory-rate-card",
        ]
        for b in valid_branches:
            assert CONVENTIONAL_BRANCH_RE.match(b), f"Branch '{b}' should match conventional regex"

    def test_rejects_nonconventional_branches(self):
        invalid_branches = [
            "my-feature",
            "wip-branch",
            "test",
            "random_fixes",
            "feat_without_slash",
        ]
        for b in invalid_branches:
            assert not CONVENTIONAL_BRANCH_RE.match(b), f"Branch '{b}' should not match"


class TestOutputHierarchyDraftRejection:
    """Tests that validate_output_hierarchy rejects draft and temp directories."""

    def test_rejects_draft_at_root(self, tmp_path: Path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "project_lens_draft").mkdir()
        violations = validate_output_hierarchy(output_dir)
        assert any("PROHIBITED_DRAFT_DIRECTORY" in v or "INVALID_PROJECT_NAME" in v for v in violations)

    def test_rejects_draft_inside_project(self, tmp_path: Path):
        output_dir = tmp_path / "output"
        project_dir = output_dir / "project_lens"
        project_dir.mkdir(parents=True)
        (project_dir / "09-18-2026 draft").mkdir()
        violations = validate_output_hierarchy(output_dir)
        assert any("PROHIBITED_DRAFT_DIRECTORY" in v for v in violations)
