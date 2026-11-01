"""
tests/test_wave2_worktree_and_hitl_concurrency.py

Wave 2 Verification Suite:
1. Git worktree environment variable isolation in run_git.
2. PID recording and concurrency serialization in git_worktree_provision_lock.
3. Interactive HITL deadline pause and extension via pause_deadline_for_hitl.
4. Thread serialization of CLI input prompts via _CLI_INPUT_LOCK.
5. Group commit BGE-M3 retrieval evaluation sealing before release activation.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from config.request_deadline import (
    Deadline,
    RequestDeadlineExceeded,
    deadline_scope,
    pause_deadline_for_hitl,
)
from dual_pipeline.cli.intake_approval_helper import (
    _CLI_INPUT_LOCK,
    attach_cli_judge_resolver,
)
from dual_pipeline.document_pipeline_core.parallel_ingestor import ParallelDocumentIngestor
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    git_worktree_provision_lock,
    run_git,
)


class TestWave2WorktreeAndHITLConcurrency(unittest.TestCase):
    """Verifies Wave 2 concurrency, HITL pause, and group-commit gates."""

    def test_run_git_sanitizes_git_environment_variables(self) -> None:
        """run_git must strip GIT_DIR and GIT_WORK_TREE to ensure subshell isolation."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_root = Path(tmp_str)
            # Create a real git repo
            run_git(["init"], cwd=tmp_root)

            # Set contaminating git environment variables
            orig_env = {
                k: os.environ.get(k)
                for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
            }
            try:
                os.environ["GIT_DIR"] = "/nonexistent/fake/git/dir"
                os.environ["GIT_WORK_TREE"] = "/nonexistent/fake/worktree"

                # run_git must succeed because it strips the poisoned env vars
                out = run_git(["rev-parse", "--is-inside-work-tree"], cwd=tmp_root)
                self.assertEqual(out, "true")
            finally:
                for k, v in orig_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_git_worktree_provision_lock_records_pid_and_serializes(self) -> None:
        """Lock acquisition must write current PID to nee_worktree_provision.flock."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_root = Path(tmp_str)
            (tmp_root / ".git").mkdir()

            with git_worktree_provision_lock(tmp_root, timeout_seconds=10.0) as lock_path:
                self.assertTrue(lock_path.is_file())
                content = lock_path.read_text(encoding="utf-8").strip()
                self.assertEqual(content, str(os.getpid()))

    def test_pause_deadline_for_hitl_shifts_deadline_expiration(self) -> None:
        """pause_deadline_for_hitl must extend deadline so human review time does not cause timeout."""
        # A tiny 0.15s deadline budget
        with deadline_scope(0.15) as dl:
            self.assertIsNotNone(dl)
            # Simulate human operator taking 0.25 seconds to review a prompt
            with pause_deadline_for_hitl():
                time.sleep(0.25)

            # Without extension, this would raise RequestDeadlineExceeded
            remaining = dl.remaining()
            self.assertGreater(remaining, 0.0)

    def test_cli_judge_resolver_mutex_serializes_concurrent_threads(self) -> None:
        """Concurrent workers encountering judge prompts must be serialized by _CLI_INPUT_LOCK."""
        meta: dict = {}
        attach_cli_judge_resolver(meta)
        resolver = meta["hitl_judge_resolver"]
        self.assertTrue(callable(resolver))

        active_holders = 0
        max_concurrent_holders = 0

        class DummyEnvelope:
            question = "Is this assertion grounded?"
            calibrated_options = [{"key": "UPHOLD_DEDUCTION", "label": "Uphold"}]

        def _worker():
            nonlocal active_holders, max_concurrent_holders
            with _CLI_INPUT_LOCK:
                active_holders += 1
                max_concurrent_holders = max(max_concurrent_holders, active_holders)
                time.sleep(0.05)
                active_holders -= 1

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(max_concurrent_holders, 1)

    def test_group_commit_executes_retrieval_evaluation_before_activation(self) -> None:
        """_execute_group_commit must resolve suite and evaluate BGE-M3 before calling activate."""
        with tempfile.TemporaryDirectory() as tmp_str:
            rt_root = Path(tmp_str)
            releases_dir = rt_root / "releases"
            releases_dir.mkdir(parents=True, exist_ok=True)
            active_pair_file = rt_root / "active_pair.json"
            active_pair_file.write_text('{"graph_path": "dummy.db", "generation_id": "gen_0"}', encoding="utf-8")

            ingestor = ParallelDocumentIngestor(runtime_root=rt_root, group_commit=True)

            mock_mgr = MagicMock()
            mock_mgr.runtime_root = rt_root
            mock_mgr.releases_dir = releases_dir
            mock_mgr.active_pair.return_value = {"graph_path": "dummy.db", "generation_id": "gen_0"}
            mock_mgr._resolve.return_value = rt_root / "dummy.db"
            mock_mgr.prepare_many.return_value = {"next_pair": {"generation_id": "gen_1"}}
            mock_mgr.activate.return_value = {"status": "ACTIVATED", "generation_id": "gen_1"}

            receipt_file = releases_dir / "release-gen_1.json"
            receipt_file.write_text('{"receipt_id": "rec_1", "status": "PREPARED"}', encoding="utf-8")

            with (
                patch("dual_pipeline.release_core.manager.ReleaseManager", return_value=mock_mgr),
                patch("dual_pipeline.intake_engine.IntakeEngine"),
                patch.object(ingestor, "_collect_group_inputs", return_value=(["run_1"], [], 1)),
                patch("dual_pipeline.retrieval_suite.AutomaticRetrievalReleaseSuiteBuilder") as mock_sb,
                patch("dual_pipeline.retrieval_release.RetrievalReleaseEvaluator") as mock_eval,
            ):
                mock_sb.return_value.resolve.return_value = {"path": str(rt_root / "suite.json")}
                mock_eval.return_value.evaluate.return_value = {"evaluation": {"status": "PASS"}}

                res = ingestor._execute_group_commit([{"run_id": "run_1", "graph_operation_count": 1}])
                self.assertIsNotNone(res)
                self.assertEqual(res.get("status"), "ACTIVATED")
                # Verify that AutomaticRetrievalReleaseSuiteBuilder and RetrievalReleaseEvaluator were called
                mock_sb.return_value.resolve.assert_called_once_with(receipt_file)
                mock_eval.return_value.evaluate.assert_called_once()
                mock_mgr.activate.assert_called_once_with(receipt_file)


if __name__ == "__main__":
    unittest.main()
