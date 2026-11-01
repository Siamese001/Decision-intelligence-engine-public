"""
tests/test_deterministic_post_asr_replay_engine_adversarial.py

Wave 4 Adversarial and Edge-Case Tests for Deterministic Post-ASR Replay Engine
and Checkpoint Invalidation.

Covers:
1. is_deliberate_reset prioritizes force_rerun over resume/regenerate.
2. try_resume_passing_attempt rejects attempts with mismatched collateral_digest or binding_sha256.
3. _read_attempt_checkpoint fails closed when decision/seals are empty or missing.
4. prepare_synthesis_attempts prunes stale attempt_2..N directories on fresh run.
5. check_deliverable_01_deploy_action deploys updated content when content changes or force_rerun=True.
6. compute_stage_manifest incorporates metadata attachments and collateral_drop_log into collateral_digest.
7. persist_stage_checkpoint_seal updates seal when validated content changes.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from dual_pipeline.canonical_pipeline.attachments import (
    is_deliberate_reset,
    try_resume_passing_attempt,
)
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _read_attempt_checkpoint,
    _find_passing_checkpoint_file,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    prepare_synthesis_attempts,
    check_deliverable_01_deploy_action,
)
from dual_pipeline.remediation_core.stage_sealer import (
    persist_stage_checkpoint_seal,
    verify_stage_checkpoint_seal,
)
from dual_pipeline.runtime.pipeline_context import (
    compute_stage_manifest,
    is_checkpoint_valid,
)


def test_is_deliberate_reset_prioritizes_force_rerun():
    """Verifies that force_rerun=True always resets stale state even with resume/regenerate."""
    # When force_rerun is True, should always return True regardless of other flags
    assert is_deliberate_reset({"force_rerun": True}) is True
    assert is_deliberate_reset({"force_rerun": True, "resume": True}) is True
    assert is_deliberate_reset({"force_rerun": True, "regenerate": "output_2"}) is True
    assert is_deliberate_reset({"force_rerun": True, "recovery": True}) is True

    # When force_rerun is False or absent, should return False
    assert is_deliberate_reset({}) is False
    assert is_deliberate_reset({"resume": True}) is False
    assert is_deliberate_reset({"regenerate": "output_2"}) is False


def test_try_resume_passing_attempt_collateral_and_binding_invalidation():
    """Verifies that attempts with mismatched collateral_digest or binding_sha256 are rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        att_dir = out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True)

        # Create passing decision and checkpoints
        decision = {
            "status": "PASS",
            "authoritative_write_authorized": True,
            "input_hash": "hash_abc123",
            "binding_sha256": "bind_old_111",
            "collateral_digest": "collat_old_222",
        }
        (att_dir / "final_release_decision.json").write_text(json.dumps(decision), encoding="utf-8")
        (att_dir / "output_1.ckpt.md").write_text("# Output 1\nSome dialogue turn: text", encoding="utf-8")
        (att_dir / "output_2.ckpt.md").write_text("# Output 2\nExecutive recap", encoding="utf-8")
        (att_dir / "output_3.ckpt.json").write_text('{"entities": []}', encoding="utf-8")
        (att_dir / "metadata.json").write_text(json.dumps({
            "input_hash": "hash_abc123",
            "binding_sha256": "bind_old_111",
            "collateral_digest": "collat_old_222",
        }), encoding="utf-8")

        # 1. Matching metadata -> should resume
        matching_meta = {
            "input_hash": "hash_abc123",
            "binding_sha256": "bind_old_111",
            "collateral_digest": "collat_old_222",
        }
        res = try_resume_passing_attempt(out_p, matching_meta, CanonicalModality.MEETING_TRANSCRIPT)
        assert res is not None
        attempt_num, s_res, e_res = res
        assert attempt_num == 1

        # 2. Changed collateral_digest (e.g. newly added screenshots) -> must reject
        new_collat_meta = {
            "input_hash": "hash_abc123",
            "binding_sha256": "bind_old_111",
            "collateral_digest": "collat_NEW_444",
        }
        res_collat = try_resume_passing_attempt(out_p, new_collat_meta, CanonicalModality.MEETING_TRANSCRIPT)
        assert res_collat is None

        # 3. Changed binding_sha256 -> must reject
        new_bind_meta = {
            "input_hash": "hash_abc123",
            "binding_sha256": "bind_NEW_555",
            "collateral_digest": "collat_old_222",
        }
        res_bind = try_resume_passing_attempt(out_p, new_bind_meta, CanonicalModality.MEETING_TRANSCRIPT)
        assert res_bind is None


def test_read_attempt_checkpoint_fails_closed_on_empty_decision():
    """Verifies that _read_attempt_checkpoint returns None when dec is empty or unsealed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        att_dir = Path(tmp_dir) / "attempt_1"
        att_dir.mkdir(parents=True)
        ckpt_file = att_dir / "output_1.ckpt.md"
        ckpt_file.write_text("A" * 300, encoding="utf-8")

        # No final_release_decision.json or stage_seals.json -> dec == {} -> MUST return None
        res = _read_attempt_checkpoint(att_dir, "output_1.ckpt.md", "output_1_pass")
        assert res is None, "Must fail closed when no decision or seal exists"

        # Corrupt / empty decision file -> MUST return None
        (att_dir / "final_release_decision.json").write_text("{}", encoding="utf-8")
        res_empty = _read_attempt_checkpoint(att_dir, "output_1.ckpt.md", "output_1_pass")
        assert res_empty is None, "Must fail closed on empty decision JSON"


def test_prepare_synthesis_attempts_prunes_stale_higher_attempts():
    """Verifies that starting attempt_1 prunes stale attempt_2..N directories."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        attempts_dir = out_p / "attempts"
        (attempts_dir / "attempt_1").mkdir(parents=True)
        (attempts_dir / "attempt_2").mkdir(parents=True)
        (attempts_dir / "attempt_3").mkdir(parents=True)
        (attempts_dir / "attempt_1" / "file.txt").write_text("keep", encoding="utf-8")
        (attempts_dir / "attempt_2" / "file.txt").write_text("stale2", encoding="utf-8")
        (attempts_dir / "attempt_3" / "file.txt").write_text("stale3", encoding="utf-8")

        meta = {"force_rerun": False, "attempt": 1}
        prepare_synthesis_attempts(CanonicalModality.MEETING_TRANSCRIPT, "dummy source", meta, out_p)

        assert (attempts_dir / "attempt_1").is_dir()
        assert not (attempts_dir / "attempt_2").exists(), "attempt_2 must be pruned"
        assert not (attempts_dir / "attempt_3").exists(), "attempt_3 must be pruned"


def test_check_deliverable_01_deploy_action_allows_updated_content():
    """Verifies that check_deliverable_01_deploy_action deploys when content changed or force_rerun=True."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        f1_path = out_p / "01_transcript.md"
        old_content = "# Old Transcript Content\nDialogue line"
        f1_path.write_text(old_content, encoding="utf-8")

        import hashlib
        old_sha = hashlib.sha256(old_content.encode("utf-8")).hexdigest()
        (out_p / "stage_seals.json").write_text(json.dumps({
            "output_1": {"status": "PASS", "sha256": old_sha}
        }), encoding="utf-8")

        class MockSynthesisResult:
            def __init__(self, out1):
                self.output_1 = out1

        # 1. Same content -> should not overwrite
        same_res = MockSynthesisResult(old_content)
        should_write, text = check_deliverable_01_deploy_action(f1_path, out_p, same_res, {})
        assert should_write is False

        # 2. Updated content (e.g. healed prototype walkthrough turns) -> MUST overwrite
        new_content = "# Updated Transcript Content\nWith newly incorporated prototype screenshots"
        updated_res = MockSynthesisResult(new_content)
        should_write, text = check_deliverable_01_deploy_action(f1_path, out_p, updated_res, {})
        assert should_write is True
        assert text == new_content

        # 3. force_rerun=True -> MUST overwrite even if same
        should_write_force, text_force = check_deliverable_01_deploy_action(f1_path, out_p, same_res, {"force_rerun": True})
        assert should_write_force is True


def test_compute_stage_manifest_collateral_digest_derivation():
    """Verifies that attachments and collateral_drop_log derive deterministic collateral_digest."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        drop_log = [
            {"filename": "screenshot1.png", "elapsed_secs": 0.0, "sha256": "sha_111"},
            {"filename": "screenshot2.png", "elapsed_secs": 120.0, "sha256": "sha_222"},
        ]
        (out_dir / "collateral_drop_log.json").write_text(json.dumps(drop_log), encoding="utf-8")

        meta = {
            "output_dir": str(out_dir),
            "attachments": [{"path": "/path/to/screenshot1.png", "filename": "screenshot1.png"}],
        }
        manifest = compute_stage_manifest("output_1", "Source dialogue", meta)
        assert manifest.collateral_digest is not None
        assert len(manifest.collateral_digest) == 64
        # Stored in metadata
        assert meta.get("collateral_digest") == manifest.collateral_digest

        # Changing drop log changes collateral_digest
        drop_log_updated = drop_log + [{"filename": "screenshot3.png", "elapsed_secs": 240.0}]
        (out_dir / "collateral_drop_log.json").write_text(json.dumps(drop_log_updated), encoding="utf-8")
        meta2 = {
            "output_dir": str(out_dir),
            "attachments": [{"path": "/path/to/screenshot1.png", "filename": "screenshot1.png"}],
        }
        manifest2 = compute_stage_manifest("output_1", "Source dialogue", meta2)
        assert manifest2.collateral_digest != manifest.collateral_digest


def test_persist_stage_checkpoint_seal_updates_validated_content():
    """Verifies that persist_stage_checkpoint_seal updates seal when validated content changes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ckpt_dir = Path(tmp_dir) / "attempt_1"
        initial_content = "# Initial Output 1 content\n" + ("Text " * 20)
        persist_stage_checkpoint_seal(ckpt_dir, "output_1", initial_content)

        ok, err = verify_stage_checkpoint_seal(ckpt_dir, "output_1")
        assert ok is True

        # Now update with newly validated content
        updated_content = "# Updated Output 1 content with all screenshots\n" + ("Text " * 20)
        persist_stage_checkpoint_seal(ckpt_dir, "output_1", updated_content)

        ok, err = verify_stage_checkpoint_seal(ckpt_dir, "output_1")
        assert ok is True
        ckpt_saved = (ckpt_dir / "output_1.ckpt.md").read_text(encoding="utf-8")
        assert ckpt_saved == updated_content
