from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "dev_retrieval_smoke.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("dev_retrieval_smoke", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - test-environment failure
        raise RuntimeError("could not load dev_retrieval_smoke")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DevRetrievalSmokeTests(unittest.TestCase):
    def test_routed_out_requests_need_no_retrieval_arguments_or_backend(self) -> None:
        module = _load_script()
        cases = (
            ("Summarize these meeting notes into actions.", "GENERAL_ASSISTANT_HANDOFF"),
            (
                "Rebuild the staffing workbook with formulas and allocations.",
                "OPERATIONAL_WORKFLOW_HANDOFF",
            ),
            ("Tell me about Chakra.", "ROUTE_AMBIGUOUS"),
        )
        for question, destination in cases:
            argv = [str(SCRIPT_PATH), "--question", question]
            with (
                self.subTest(destination=destination),
                patch.object(
                    module, "get_backend", side_effect=AssertionError("BGE must not initialize")
                ),
                patch.object(sys, "argv", argv),
                io.StringIO() as stdout,
                redirect_stdout(stdout),
            ):
                self.assertEqual(0, module.main())
                payload = json.loads(stdout.getvalue())
            self.assertEqual("ROUTED", payload["status"])
            self.assertEqual(destination, payload["route"]["destination"])

    def test_smoke_runtime_uses_reusable_worker_with_requested_deadline(self) -> None:
        module = _load_script()
        question = "Recommend and prioritize options across graph evidence for a decision."
        argv = [
            str(SCRIPT_PATH),
            "--sidecar",
            "sidecar.sqlite",
            "--model-path",
            "model",
            "--model-manifest",
            "manifest.json",
            "--model-revision",
            "revision",
            "--question",
            question,
            "--timeout-seconds",
            "7",
        ]
        with (
            patch.object(
                module,
                "_submit_to_worker",
                return_value={
                    "status": module.RETRIEVAL_SMOKE_STATUS,
                    "tool_scope": module.RETRIEVAL_SMOKE_SCOPE,
                },
            ) as submit,
            patch.object(module.time, "monotonic", return_value=100.0),
            patch.object(sys, "argv", argv),
            io.StringIO() as stdout,
            redirect_stdout(stdout),
        ):
            self.assertEqual(0, module.main())
            payload = json.loads(stdout.getvalue())
        self.assertEqual(module.RETRIEVAL_SMOKE_STATUS, payload["status"])
        self.assertEqual(module.RETRIEVAL_SMOKE_SCOPE, payload["tool_scope"])
        self.assertEqual(107.0, submit.call_args.kwargs["deadline"])

    def test_di_smoke_never_accepts_a_completed_decision_response(self) -> None:
        module = _load_script()
        question = "Recommend and prioritize options across graph evidence for a decision."
        argv = [
            str(SCRIPT_PATH),
            "--sidecar",
            "sidecar.sqlite",
            "--model-path",
            "model",
            "--model-manifest",
            "manifest.json",
            "--model-revision",
            "revision",
            "--question",
            question,
        ]
        with (
            patch.object(
                module,
                "_submit_to_worker",
                return_value={
                    "status": "COMPLETED",
                    "decision": {"status": "COMPLETED"},
                },
            ),
            patch.object(sys, "argv", argv),
            io.StringIO() as stdout,
            redirect_stdout(stdout),
        ):
            self.assertEqual(1, module.main())
            payload = json.loads(stdout.getvalue())
        self.assertEqual("ERROR", payload["status"])
        self.assertEqual("RETRIEVAL_SMOKE_CONTRACT_VIOLATION", payload["reason_code"])

    def test_di_smoke_execution_never_emits_a_decision_or_di_completion(self) -> None:
        module = _load_script()

        class Backend:
            backend_id = "fixture-backend"

        class Retriever:
            def retrieve(self, *args, **kwargs):
                return {
                    "lanes": {
                        "approved": {
                            "results": [
                                {
                                    "record_id": "record-1",
                                    "lane": "approved",
                                    "fused_rank": 1,
                                    "fused_score": 1.0,
                                    "record": {"retrieval_text": "Fixture evidence."},
                                }
                            ]
                        }
                    }
                }

        result = module._execute_retrieval(
            question="Recommend and prioritize options across graph evidence for a decision.",
            audience="internal_consulting",
            classification="internal",
            top_k=1,
            backend=Backend(),
            retriever=Retriever(),
        )
        self.assertEqual(module.RETRIEVAL_SMOKE_STATUS, result["status"])
        self.assertEqual(module.RETRIEVAL_SMOKE_SCOPE, result["tool_scope"])
        self.assertNotIn("decision", result)

    def test_smoke_runtime_timeout_is_structured_and_nonzero(self) -> None:
        module = _load_script()
        question = "Recommend and prioritize options across graph evidence for a decision."
        argv = [
            str(SCRIPT_PATH),
            "--sidecar",
            "sidecar.sqlite",
            "--model-path",
            "model",
            "--model-manifest",
            "manifest.json",
            "--model-revision",
            "revision",
            "--question",
            question,
            "--timeout-seconds",
            "7",
        ]
        with (
            patch.object(
                module.subprocess,
                "Popen",
                side_effect=AssertionError("the client must not start a worker after a timeout"),
            ),
            patch.object(
                module,
                "_submit_to_worker",
                side_effect=module.WorkerTimeout("request"),
            ),
            patch.object(sys, "argv", argv),
            io.StringIO() as stdout,
            redirect_stdout(stdout),
        ):
            self.assertEqual(module.TIMEOUT_EXIT_CODE, module.main())
            payload = json.loads(stdout.getvalue())
        self.assertEqual("TIMEOUT", payload["status"])
        self.assertEqual("QUERY_EXECUTION_TIMEOUT", payload["reason_code"])
        self.assertEqual(7, payload["timeout_seconds"])
        self.assertEqual("request", payload["timeout_phase"])
        self.assertEqual("NEE_DECISION_INTELLIGENCE", payload["route"]["destination"])

    def test_stop_worker_targets_only_the_exact_worker_identity(self) -> None:
        module = _load_script()
        argv = [
            str(SCRIPT_PATH),
            "--sidecar",
            "sidecar.sqlite",
            "--model-path",
            "model",
            "--model-manifest",
            "manifest.json",
            "--model-revision",
            "revision",
            "--stop-worker",
        ]
        state = {"identity": "expected", "pid": 12345}
        with (
            patch.object(module, "_worker_identity", return_value="expected"),
            patch.object(module, "_read_worker_state", return_value=state),
            patch.object(module, "_terminate_worker") as terminate,
            patch.object(sys, "argv", argv),
            io.StringIO() as stdout,
            redirect_stdout(stdout),
        ):
            self.assertEqual(0, module.main())
            payload = json.loads(stdout.getvalue())
        terminate.assert_called_once_with(state, "expected")
        self.assertEqual("STOPPED", payload["status"])


if __name__ == "__main__":
    unittest.main()
