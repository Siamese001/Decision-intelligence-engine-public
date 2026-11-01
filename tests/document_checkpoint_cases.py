"""Focused W2 foundation cases; integrate into the governed W2 acceptance class."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.checkpoint_storage import CheckpointIntegrityError


class CheckpointFoundationCases:
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "checkpoint.json"
        self.binding = {"source_sha256": "a" * 64, "configuration": {"schema": "v2", "model": "test"}}

    def manager(self, *, strict=True):
        return PipelineCheckpointManager(self.path, require_binding=strict)

    def test_exact_binding_and_snapshot_copy(self):
        manager = self.manager()
        self.assertEqual(manager.bind(self.binding)["reason"], "NEW_CHECKPOINT")
        payload = {"candidate": {"entities": ["one"]}}
        manager.save_stage("L2", payload)
        payload["candidate"]["entities"].append("caller mutation")
        loaded = manager.get_stage("L2")
        loaded["candidate"]["entities"].append("reader mutation")
        second = self.manager()
        result = second.bind({"configuration": {"model": "test", "schema": "v2"}, "source_sha256": "a" * 64})
        self.assertTrue(result["reused"])
        self.assertEqual(result["reason"], "EXACT_BINDING")
        self.assertEqual(second.get_stage("L2"), {"candidate": {"entities": ["one"]}})

    def test_strict_mode_cannot_access_before_binding_and_legacy_is_invalidated(self):
        self.path.write_text(json.dumps({"version": "1.0.0", "stages": {"U0": {"text": "legacy"}}}))
        manager = self.manager()
        for action in (lambda: manager.has_stage("U0"), lambda: manager.get_stage("U0"), lambda: manager.save_stage("L2", {})):
            with self.assertRaises(CheckpointIntegrityError) as caught:
                action()
            self.assertEqual(caught.exception.reason, "BINDING_REQUIRED")
        decision = manager.bind(self.binding)
        self.assertEqual(decision["reason"], "LEGACY_UNBOUND")
        self.assertEqual(decision["invalidated_stages"], ["U0"])
        self.assertFalse(decision["reused"])
        self.assertFalse(manager.has_stage("U0"))

    def test_force_and_binding_changes_preserve_audit_and_reject_stale_managers(self):
        first = self.manager()
        first.bind(self.binding)
        first.save_stage("L2", {"candidate": "old"})
        second = self.manager()
        second.bind(self.binding)
        forced = second.bind(self.binding, force=True)
        self.assertEqual(forced["reason"], "FORCE_INVALIDATION")
        self.assertEqual(forced["invalidated_stages"], ["L2"])
        for action in (lambda: first.get_stage("L2"), lambda: first.save_stage("RELEASE", {})):
            with self.assertRaises(CheckpointIntegrityError) as caught:
                action()
            self.assertEqual(caught.exception.reason, "STALE_BINDING")
        altered = {**self.binding, "configuration": {"schema": "v3", "model": "test"}}
        result = second.bind(altered)
        self.assertEqual(result["reason"], "BINDING_CHANGED")
        self.assertEqual([event["reason"] for event in result["audit"]],
                         ["NEW_CHECKPOINT", "FORCE_INVALIDATION", "BINDING_CHANGED"])

    def test_checksum_corruption_rejects_read_write_and_force(self):
        manager = self.manager()
        manager.bind(self.binding)
        manager.save_stage("L2", {"candidate": "accepted"})
        data = json.loads(self.path.read_text())
        data["stages"]["L2"]["candidate"] = "tampered"
        self.path.write_text(json.dumps(data))
        for action in (lambda: self.manager(), lambda: manager.get_stage("L2"),
                       lambda: manager.save_stage("RELEASE", {}), lambda: manager.bind(self.binding, force=True)):
            with self.assertRaises(CheckpointIntegrityError) as caught:
                action()
            self.assertEqual(caught.exception.reason, "CORRUPT_CHECKPOINT")

    def test_independent_same_binding_writers_do_not_lose_stages(self):
        self.manager().bind(self.binding)
        def write(ordinal):
            manager = self.manager()
            manager.bind(self.binding)
            manager.save_stage(f"stage-{ordinal}", {"ordinal": ordinal})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(24)))
        manager = self.manager()
        manager.bind(self.binding)
        for ordinal in range(24):
            self.assertEqual(manager.get_stage(f"stage-{ordinal}"), {"ordinal": ordinal})
        self.assertEqual(len(list(self.path.parent.glob(".checkpoint.json.*.tmp"))), 0)

    def test_replace_failure_preserves_prior_checkpoint_and_cleans_unique_temporary(self):
        manager = self.manager()
        manager.bind(self.binding)
        manager.save_stage("L2", {"candidate": "accepted"})
        before = self.path.read_bytes()
        with patch("dual_pipeline.checkpoint_storage.os.replace", side_effect=OSError("injected interruption")):
            with self.assertRaises(OSError):
                manager.save_stage("RELEASE", {"ready": True})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(manager.has_stage("RELEASE"))
        self.assertEqual(list(self.path.parent.glob(".checkpoint.json.*.tmp")), [])

    def test_subset_invalidation_and_default_legacy_serialization(self):
        @dataclass
        class Value:
            location: Path
        legacy = self.manager(strict=False)
        legacy.save_stage("U0", {"value": Value(Path("input.md")), "sequence": (1, 2)})
        reloaded = self.manager(strict=False)
        self.assertEqual(reloaded.get_stage("U0"), {"value": {"location": "input.md"}, "sequence": [1, 2]})
        self.assertEqual(json.loads(self.path.read_text())["version"], "2.0.0")
        reloaded.bind(self.binding)
        reloaded.save_stage("SOURCE", {"valid": True})
        reloaded.save_stage("L2", {"valid": True})
        event = reloaded.invalidate(["L2", "ABSENT"], reason="UPSTREAM_CHANGED")
        self.assertEqual(event["invalidated_stages"], ["L2"])
        self.assertTrue(reloaded.has_stage("SOURCE"))
        self.assertFalse(reloaded.has_stage("L2"))

    def test_stale_same_stage_writer_cannot_overwrite_a_new_candidate(self):
        first, second = self.manager(), self.manager()
        first.bind(self.binding)
        second.bind(self.binding)
        first.save_stage("L2", {"candidate": "first"})
        with self.assertRaises(CheckpointIntegrityError) as caught:
            second.save_stage("L2", {"candidate": "stale overwrite"})
        self.assertEqual(caught.exception.reason, "CONCURRENT_STAGE_CHANGE")
        self.assertEqual(first.get_stage("L2"), {"candidate": "first"})
