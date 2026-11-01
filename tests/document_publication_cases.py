"""Isolated publication repeat tests mixed into Wave2AdversarialTests only."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dual_pipeline.canonical_pipeline import publication_journal as journal
from dual_pipeline.canonical_pipeline.publish_helper import (
    check_idempotent_or_continue, publish_graph_if_requested,
)
from dual_pipeline.core import digest, sha256_file


class PublicationCases:
    @contextmanager
    def _publication_case(self):
        with TemporaryDirectory(prefix="nee-w2-publication-") as temp, ExitStack() as stack:
            root = Path(temp)
            runtime = root / ".local" / "dual_pipeline"
            runtime.mkdir(parents=True)
            (runtime / "active_pair.json").write_text('{"generation_id":"fixture-before"}')
            out = root / "stage-one"
            out.mkdir()
            package = out / "intake_package.json"
            package.write_text(json.dumps({"meeting": {
                "meeting_id": "fixture-source", "source_hash": "a" * 64,
                "classification": "restricted", "audiences": ["fixture-team"],
                "source_location": "fixture-source.txt",
            }}))
            (out / "final_release_decision.json").write_text('{"status":"PASS"}')
            (out / "llm_judge_evaluation.json").write_text('{"overall_verdict":"PASS"}')
            writer = stack.enter_context(patch(
                "dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph",
                return_value={"status": "COMPLETE", "fixture": True},
            ))
            stack.enter_context(patch("dual_pipeline.transcript_pipeline_core.release_runner.ROOT", root))
            stack.enter_context(patch(
                "dual_pipeline.transcript_pipeline_core.release_runner._resolve_model_asset",
                return_value=str(root / "fixture-model"),
            ))
            stack.enter_context(patch("dual_pipeline.canonical_pipeline.publish_helper.check_directory_compliance"))
            gate = stack.enter_context(patch("dual_pipeline.release_policy.verify_authoritative_release_gate"))
            args = {
                "mutate_graph": True, "intake_pkg_path": package,
                "metadata": {"is_document": True, "title": "Fixture", "raw_source_sha256": "b" * 64},
                "written": {}, "directive_id": "fixture-publication",
                "reviewer": "fixture-human", "rationale": "Fixture authorization request",
                "embedding_backend": "fixture-backend",
            }
            yield root, runtime, args, writer, gate

    def test_publication_intent_precedes_writer_and_binds_exact_inputs(self):
        with self._publication_case() as (_, runtime, args, writer, _):
            def observe(**_kwargs):
                intent = runtime / "publication_journal" / "fixture-publication.intent.json"
                data = json.loads(intent.read_text())
                checksum = data.pop("record_sha256")
                self.assertEqual(checksum, digest(data))
                self.assertEqual(data["state"], "PENDING")
                bound = data["bindings"]
                self.assertEqual(bound["package"]["sha256"], sha256_file(args["intake_pkg_path"]))
                self.assertEqual(bound["source"]["raw_source_sha256"], "b" * 64)
                self.assertEqual(bound["authority_request"]["reviewer"], "fixture-human")
                self.assertEqual(bound["access"]["classification"], "restricted")
                self.assertEqual(bound["runtime"]["runtime_root"], str(runtime.resolve()))
                self.assertEqual(bound["backend"]["requested_backend"], "fixture-backend")
                return {"status": "COMPLETE", "fixture": True}
            writer.side_effect = observe
            self.assertEqual(publish_graph_if_requested(**args)["status"], "COMPLETE")
            recorded = runtime / "publication_journal" / "fixture-publication.result.json"
            self.assertEqual(json.loads(recorded.read_text())["state"], "RESULT_RECORDED")
            self.assertEqual(writer.call_count, 1)

    def test_publication_activation_then_exception_never_repeats(self):
        with self._publication_case() as (root, runtime, args, writer, gate):
            def activate_then_raise(**_kwargs):
                (runtime / "active_pair.json").write_text('{"generation_id":"fixture-activated"}')
                raise RuntimeError("fixture crash after activation")
            writer.side_effect = activate_then_raise
            with self.assertRaises(journal.PublicationReconciliationRequired):
                publish_graph_if_requested(**args)
            relocated = root / "different-stage" / "intake_package.json"
            relocated.parent.mkdir()
            relocated.write_text('{"changed":"source and package"}')
            attempts = [args, {**args, "metadata": {**args["metadata"], "force_rerun": True}}, {
                **args, "intake_pkg_path": relocated, "reviewer": "changed-human",
                "embedding_backend": "changed-model",
                "metadata": {**args["metadata"], "raw_source_sha256": "c" * 64, "force_rerun": True},
            }]
            for retry in attempts:
                with self.subTest(retry=retry["embedding_backend"]):
                    with self.assertRaises(journal.PublicationReconciliationRequired) as raised:
                        publish_graph_if_requested(**retry)
                    self.assertEqual(raised.exception.details["prior_state"], "PENDING")
            self.assertEqual(writer.call_count, 1)
            self.assertEqual(gate.call_count, 1)
            self.assertEqual(json.loads((runtime / "active_pair.json").read_text())["generation_id"], "fixture-activated")

    def test_publication_recorded_result_is_not_replayed_after_generation_drift(self):
        with self._publication_case() as (_, runtime, args, writer, _):
            publish_graph_if_requested(**args)
            (runtime / "active_pair.json").write_text('{"generation_id":"fixture-later"}')
            with self.assertRaises(journal.PublicationReconciliationRequired) as raised:
                publish_graph_if_requested(**{**args, "metadata": {"force_rerun": True}})
            self.assertEqual(raised.exception.details["prior_state"], "RESULT_RECORDED")
            self.assertFalse(raised.exception.details["retry_safe"])
            self.assertEqual(writer.call_count, 1)

    def test_publication_result_write_failure_preserves_pending_guard(self):
        with self._publication_case() as (_, runtime, args, writer, _):
            store = journal._store_record
            def fail_result(path, value, runtime_root):
                if path.name.endswith(".result.json"):
                    raise OSError("fixture result disk failure")
                return store(path, value, runtime_root)
            with patch.object(journal, "_store_record", side_effect=fail_result):
                with self.assertRaises(journal.PublicationReconciliationRequired):
                    publish_graph_if_requested(**args)
            with self.assertRaises(journal.PublicationReconciliationRequired):
                publish_graph_if_requested(**args)
            self.assertEqual(writer.call_count, 1)
            self.assertTrue((runtime / "publication_journal" / "fixture-publication.intent.json").is_file())
            self.assertFalse((runtime / "publication_journal" / "fixture-publication.result.json").exists())

    def test_publication_preflight_failure_has_no_intent_or_writer_call(self):
        with self._publication_case() as (_, runtime, args, writer, gate):
            gate.side_effect = RuntimeError("fixture invalid seal")
            with self.assertRaisesRegex(RuntimeError, "fixture invalid seal"):
                publish_graph_if_requested(**args)
            writer.assert_not_called()
            self.assertFalse((runtime / "publication_journal" / "fixture-publication.intent.json").exists())
            gate.side_effect = None
            publish_graph_if_requested(**args)
            self.assertEqual(writer.call_count, 1)

    def test_publication_legacy_directive_requires_reconciliation(self):
        with self._publication_case() as (_, runtime, args, writer, gate):
            legacy = runtime / "ingestion_directives" / "fixture-publication.json"
            legacy.parent.mkdir()
            legacy.write_text('{"status":"COMPLETE","fixture":true}')
            with self.assertRaises(journal.PublicationReconciliationRequired) as raised:
                publish_graph_if_requested(**args)
            self.assertEqual(raised.exception.details["prior_state"], "LEGACY_DIRECTIVE")
            writer.assert_not_called()
            gate.assert_not_called()

    def test_publication_cross_runtime_request_fails_before_writer(self):
        with self._publication_case() as (root, runtime, args, writer, _):
            args["metadata"]["runtime_root"] = str(root / "different-runtime")
            with self.assertRaisesRegex(RuntimeError, "PUBLICATION_RUNTIME_MISMATCH"):
                publish_graph_if_requested(**args)
            writer.assert_not_called()
            self.assertFalse((runtime / "publication_journal").exists())

    def test_publication_corrupt_pending_journal_never_calls_writer(self):
        with self._publication_case() as (_, runtime, args, writer, _):
            path = runtime / "publication_journal" / "fixture-publication.intent.json"
            path.parent.mkdir()
            path.write_text("incomplete JSON")
            with self.assertRaises(journal.PublicationReconciliationRequired) as raised:
                publish_graph_if_requested(**args)
            self.assertEqual(raised.exception.details["prior_state"], "CORRUPT_JOURNAL")
            writer.assert_not_called()

    def test_publication_concurrent_attempts_hold_lock_through_result(self):
        with self._publication_case() as (_, _runtime, args, writer, _):
            entered, release, second_started, second_done = (threading.Event() for _ in range(4))
            def blocking_writer(**_kwargs):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("fixture writer deadline")
                return {"status": "COMPLETE", "fixture": True}
            def second_attempt():
                second_started.set()
                try:
                    return publish_graph_if_requested(**args)
                finally:
                    second_done.set()
            writer.side_effect = blocking_writer
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(publish_graph_if_requested, **args)
                try:
                    self.assertTrue(entered.wait(3))
                    second = executor.submit(second_attempt)
                    self.assertTrue(second_started.wait(3))
                    self.assertFalse(second_done.wait(0.05))
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=3)["status"], "COMPLETE")
                with self.assertRaises(journal.PublicationReconciliationRequired):
                    second.result(timeout=3)
            self.assertEqual(writer.call_count, 1)

    def test_publication_mutation_false_does_not_create_journal(self):
        with self._publication_case() as (_, runtime, args, writer, _):
            self.assertIsNone(publish_graph_if_requested(**{**args, "mutate_graph": False}))
            writer.assert_not_called()
            self.assertFalse((runtime / "publication_journal").exists())

    def test_document_request_bypasses_legacy_output_hash_shortcut(self):
        with TemporaryDirectory(prefix="nee-w2-shortcut-") as temp:
            with patch("dual_pipeline.transcript_pipeline_core.caching.check_idempotent_meeting_run") as cached:
                cached.return_value = {"status": "old-hash-only-result"}
                tracker = Mock()
                for metadata in ({"is_document": True}, {"output3_only": True}, {"source_type": "document"}):
                    self.assertIsNone(check_idempotent_or_continue(tracker, temp, metadata, "source-hash", False))
                cached.assert_not_called()
                tracker.complete_stage.assert_not_called()
                result = check_idempotent_or_continue(tracker, temp, {}, "source-hash", False)
                self.assertEqual(result, cached.return_value)
                self.assertEqual(cached.call_count, 1)
                self.assertEqual(tracker.complete_stage.call_count, 1)
