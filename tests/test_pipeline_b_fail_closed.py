"""Unit and integration tests for Wave 2: Pipeline B Fail-Closed Extraction & Mock Fallback Eradication.

Verifies:
1. ClientSafeExtractor and InternalAdvisoryExtractor raise ExtractionInferenceError in non-mock mode on failure.
2. Zero synthetic mock nodes are generated or committed to CANONICAL_GRAPH.db when models fail.
3. json_repair utility successfully repairs markdown fences, trailing commas, and truncated completions.
4. DeadLetterQueueManager routes failures to retry or DEAD_LETTER quarantine after max retries.
5. SlowPathWorker fails closed to DLQ when ComputeForkCoordinator encounters extraction errors.
6. All Pipeline B extraction modules strictly conform to the 400-line budget.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pipeline_b_extraction.contracts import (
    ExtractionInferenceError,
)
from pipeline_b_extraction.dlq import DeadLetterQueueManager
from pipeline_b_extraction.extractors import (
    ClientSafeExtractor,
    InternalAdvisoryExtractor,
)
from pipeline_b_extraction.json_repair import (
    repair_and_parse_json,
)
from pipeline_b_extraction.worker import SlowPathWorker, WorkerConfig
from storage.database import (
    DatabaseManager,
    EvidenceRecord,
    WorkItemState,
)


class TestJSONRepair(unittest.TestCase):
    """Tests robust parsing and repair of LLM completions."""

    def test_strip_markdown_fences(self) -> None:
        raw = "```json\n{\"facts\": [{\"name\": \"System A\"}]}\n```"
        parsed = repair_and_parse_json(raw)
        self.assertIn("facts", parsed)
        self.assertEqual(parsed["facts"][0]["name"], "System A")

    def test_extract_outer_json_structure(self) -> None:
        raw = "Here is the advisory response:\n[{\"category\": \"BLOCKER\", \"name\": \"Security Signoff\"}]\nPlease verify."
        parsed = repair_and_parse_json(raw)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["category"], "BLOCKER")

    def test_trailing_commas_repair(self) -> None:
        raw = '{"name": "Architecture Review", "items": [1, 2, 3, ], "properties": {"env": "prod", }, }'
        parsed = repair_and_parse_json(raw)
        self.assertEqual(parsed["name"], "Architecture Review")
        self.assertEqual(parsed["items"], [1, 2, 3])
        self.assertEqual(parsed["properties"]["env"], "prod")

    def test_truncated_json_repair(self) -> None:
        # Cut off mid-string or mid-object
        raw = '[{"category": "ADOPTION_RISK", "name": "Stakeholder friction due to chang'
        parsed = repair_and_parse_json(raw)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["category"], "ADOPTION_RISK")
        self.assertTrue(parsed[0]["name"].startswith("Stakeholder friction"))

    def test_empty_or_invalid_json_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            repair_and_parse_json("")
        with self.assertRaises(ValueError):
            repair_and_parse_json("This is pure text with no braces or brackets.")


class TestFailClosedExtractors(unittest.TestCase):
    """Verifies that non-mock extractors fail closed with ExtractionInferenceError."""

    def setUp(self) -> None:
        self.evidence = EvidenceRecord(
            evidence_id="ev_test_fail_closed",
            meeting_id="meet_fail_closed",
            speaker="Alice",
            text="We must finalize the dual-artery WAL persistence architecture before Friday.",
            start_timestamp=0.0,
            end_timestamp=10.0,
            is_final=True,
        )

    def test_client_safe_extractor_raises_on_failure_in_non_mock_mode(self) -> None:
        extractor = ClientSafeExtractor(mock_mode=False, api_key="sk-test-mock-key")

        # Mock OpenAI client to simulate network / API failure
        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("OpenAI API unreachable"))
            mock_openai_cls.return_value = mock_client

            with self.assertRaises(ExtractionInferenceError) as ctx:
                asyncio.run(extractor.extract(self.evidence))

            self.assertIn("ClientSafeExtractor failed", str(ctx.exception))
            self.assertEqual(ctx.exception.evidence_id, "ev_test_fail_closed")

    def test_internal_advisory_extractor_raises_on_failure_in_non_mock_mode(self) -> None:
        extractor = InternalAdvisoryExtractor(mock_mode=False)

        # Mock mlx_lm to simulate inference crash / OOM
        with patch("pipeline_b_extraction.extractors.MLX_LM_AVAILABLE", True):
            extractor._model = MagicMock()
            extractor._tokenizer = MagicMock()
            with patch("mlx_lm.generate", side_effect=RuntimeError("Metal GPU memory allocation failure")):
                with self.assertRaises(ExtractionInferenceError) as ctx:
                    asyncio.run(extractor.extract(self.evidence))

                self.assertIn("InternalAdvisoryExtractor failed", str(ctx.exception))
                self.assertEqual(ctx.exception.evidence_id, "ev_test_fail_closed")

    def test_mock_mode_returns_mock_when_explicitly_configured(self) -> None:
        client_mock = ClientSafeExtractor(mock_mode=True)
        nodes = asyncio.run(client_mock.extract(self.evidence))
        self.assertGreater(len(nodes), 0)
        self.assertEqual(nodes[0].namespace, "CLIENT_SAFE")

        advisory_mock = InternalAdvisoryExtractor(mock_mode=True)
        adv_nodes = asyncio.run(advisory_mock.extract(self.evidence))
        self.assertGreater(len(adv_nodes), 0)
        self.assertEqual(adv_nodes[0].namespace, "INTERNAL_ADVISORY")


class TestDeadLetterQueueManager(unittest.TestCase):
    """Verifies DLQ failure routing, retry tracking, and quarantine transition."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir.name,
            allow_unencrypted_dev_fallback=True,
        )
        self.dlq = DeadLetterQueueManager(db_manager=self.db_manager, default_max_retries=3)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dlq_increments_retries_and_quarantines_after_max_retries(self) -> None:
        rec = EvidenceRecord(
            evidence_id="ev_01",
            meeting_id="meet_dlq",
            speaker="Bob",
            text="Test speech",
            start_timestamp=0.0,
            is_final=True,
        )
        event_id = self.db_manager.append_evidence(rec, enqueue=True)

        # Failure 1 -> PENDING (attempt 1/3)
        state1 = self.dlq.route_failure(event_id, "ev_01", "Timeout 1", retry_count=1, max_retries=3)
        self.assertEqual(state1, WorkItemState.PENDING)

        # Failure 2 -> PENDING (attempt 2/3)
        state2 = self.dlq.route_failure(event_id, "ev_01", "Timeout 2", retry_count=2, max_retries=3)
        self.assertEqual(state2, WorkItemState.PENDING)

        # Failure 3 -> DEAD_LETTER (attempt 3/3 exhausted)
        state3 = self.dlq.route_failure(event_id, "ev_01", "Fatal error", retry_count=3, max_retries=3)
        self.assertEqual(state3, WorkItemState.DEAD_LETTER)

        # Inspect dead letters
        dead_letters = self.dlq.list_dead_letters()
        self.assertEqual(len(dead_letters), 1)
        self.assertEqual(dead_letters[0]["event_id"], event_id)
        self.assertEqual(dead_letters[0]["retry_count"], 3)

        # Redrive item back to PENDING
        ok = self.dlq.redrive_item(event_id)
        self.assertTrue(ok)
        self.assertEqual(len(self.dlq.list_dead_letters()), 0)


class TestSlowPathWorkerFailClosed(unittest.TestCase):
    """Verifies that SlowPathWorker routes extraction errors to DLQ without committing mock nodes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir.name,
            allow_unencrypted_dev_fallback=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_worker_fails_closed_on_coordinator_error(self) -> None:
        mock_coord = MagicMock()
        mock_coord.execute_fork = AsyncMock(side_effect=ExtractionInferenceError("API Gateway Timeout", evidence_id="ev_err"))

        worker = SlowPathWorker(
            db_manager=self.db_manager,
            fork_coordinator=mock_coord,
            config=WorkerConfig(batch_size=1, max_retries=2),
        )

        rec = EvidenceRecord(
            evidence_id="ev_err",
            meeting_id="meet_worker",
            speaker="Charlie",
            text="Discourse chunk",
            start_timestamp=0.0,
            is_final=True,
        )
        event_id = self.db_manager.append_evidence(rec, enqueue=True)

        # Run one worker cycle
        processed = asyncio.run(worker.run_once())
        self.assertEqual(processed, 1)

        # Verify zero canonical nodes were written to DB
        nodes = self.db_manager.query_canonical_nodes(0.0, True, 100)
        self.assertEqual(len(nodes), 0, "No mock nodes should be committed on error")

        # Run second cycle -> should exhaust max_retries=2 and move to DEAD_LETTER
        processed2 = asyncio.run(worker.run_once())
        self.assertEqual(processed2, 1)

        dead_letters = worker.dlq_manager.list_dead_letters()
        self.assertEqual(len(dead_letters), 1)
        self.assertEqual(dead_letters[0]["event_id"], event_id)


class TestPipelineBModuleLineBudgets(unittest.TestCase):
    """Verifies that all Pipeline B extraction modules strictly conform to the 400-line budget."""

    def test_pipeline_b_modules_under_400_lines(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        target_files = [
            repo_root / "pipeline_b_extraction" / "extractors.py",
            repo_root / "pipeline_b_extraction" / "worker.py",
            repo_root / "pipeline_b_extraction" / "json_repair.py",
            repo_root / "pipeline_b_extraction" / "dlq.py",
            repo_root / "pipeline_b_extraction" / "judges.py",
            repo_root / "pipeline_b_extraction" / "pre_egress_sanitizer.py",
            repo_root / "pipeline_b_extraction" / "contracts.py",
            repo_root / "pipeline_b_extraction" / "extractors_core" / "relations.py",
            repo_root / "pipeline_b_extraction" / "extractors_core" / "mock_extractors.py",
        ]
        for fpath in target_files:
            self.assertTrue(fpath.exists(), f"File missing: {fpath}")
            line_count = len(fpath.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Module {fpath.name} exceeds 400 lines: {line_count} lines found.",
            )


if __name__ == "__main__":
    unittest.main()
