from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import sha256_file, write_json
from dual_pipeline.decision_api import DecisionHTTPServer, runtime_readiness
from dual_pipeline.decision_service import DecisionService
from tests.test_decision_service import StubQueryEngine


class StubDecisionService:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def route(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return {"question": question, "selected_handler": "GeneralHandler", "kwargs": kwargs}

    def query(self, question: str, **kwargs: Any) -> dict[str, Any]:
        if question == "block":
            self.started.set()
            self.release.wait(timeout=2)
        if question == "timeout":
            time.sleep(0.2)
        return {
            "question": question,
            "terminal_state": "HANDOFF_READY",
            "message": "NEE → résumé",
            "kwargs": kwargs,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return {"run_id": run_id, "terminal_state": "DECISION_PRODUCED"}


class DecisionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StubDecisionService()
        self.server = DecisionHTTPServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.service.release.set()
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(
        self, method: str, path: str, payload: dict[str, Any] | bytes | None = None
    ) -> tuple[int, dict[str, Any], bytes]:
        body = None
        headers: dict[str, str] = {}
        if isinstance(payload, dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif isinstance(payload, bytes):
            body = payload
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(
            self.server.server_address[0], self.server.server_address[1], timeout=2
        )
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        connection.close()
        return status, json.loads(raw.decode("utf-8")), raw

    def test_health_readiness_route_query_and_run_lookup(self) -> None:
        self.assertEqual(200, self.request("GET", "/health")[0])
        self.assertEqual(200, self.request("GET", "/ready")[0])

        route_status, route, _ = self.request(
            "POST", "/v1/route", {"question": "Draft a short note."}
        )
        self.assertEqual(200, route_status)
        self.assertEqual("GeneralHandler", route["selected_handler"])

        query_status, query, raw = self.request(
            "POST",
            "/v1/query",
            {"question": "Explain the résumé.", "criteria": ["clarity"]},
        )
        self.assertEqual(200, query_status)
        self.assertEqual("NEE → résumé", query["message"])
        self.assertIn("NEE → résumé".encode(), raw)

        run_status, run, _ = self.request("GET", "/v1/runs/run-123")
        self.assertEqual(200, run_status)
        self.assertEqual("run-123", run["run_id"])

    def test_invalid_json_unknown_fields_and_run_traversal_are_rejected(self) -> None:
        self.assertEqual(400, self.request("POST", "/v1/query", b"{")[0])
        self.assertEqual(
            400,
            self.request(
                "POST", "/v1/query", {"question": "hello", "output_dir": "C:/tmp"}
            )[0],
        )
        self.assertEqual(400, self.request("GET", "/v1/runs/..%2Fsecret")[0])

    def test_max_concurrency_returns_429_without_starting_extra_work(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.server = DecisionHTTPServer(
            ("127.0.0.1", 0), self.service, max_concurrency=1, max_queue_size=0
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        first_result: list[tuple[int, dict[str, Any], bytes]] = []
        first = threading.Thread(
            target=lambda: first_result.append(
                self.request("POST", "/v1/query", {"question": "block"})
            )
        )
        first.start()
        self.assertTrue(self.service.started.wait(timeout=1))
        second_status, second, _ = self.request(
            "POST", "/v1/query", {"question": "second"}
        )
        self.assertEqual(429, second_status)
        self.assertEqual("BUSY", second["status"])
        self.service.release.set()
        first.join(timeout=2)
        self.assertEqual(200, first_result[0][0])

    def test_bounded_queue_absorbs_burst_and_rejects_on_saturation(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        # 1 worker, 1 queue slot -> total capacity 2
        self.server = DecisionHTTPServer(
            ("127.0.0.1", 0), self.service, max_concurrency=1, max_queue_size=1
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        # Request 1 blocks worker
        t1 = threading.Thread(
            target=lambda: self.request("POST", "/v1/query", {"question": "block"})
        )
        t1.start()
        self.assertTrue(self.service.started.wait(timeout=1))

        # Request 2 is queued (in-flight = 2 == capacity).
        q2_result: list[tuple[int, dict[str, Any], bytes]] = []
        t2 = threading.Thread(
            target=lambda: q2_result.append(
                self.request("POST", "/v1/query", {"question": "queued"})
            )
        )
        t2.start()
        time.sleep(0.05)

        # Request 3 arrives when capacity is full -> 429 BUSY!
        status_3, res_3, _ = self.request("POST", "/v1/query", {"question": "overflow"})
        self.assertEqual(429, status_3)
        self.assertEqual("BUSY", res_3["status"])

        # Unblock Request 1; Request 2 then executes from queue and succeeds
        self.service.release.set()
        t1.join(timeout=2)
        t2.join(timeout=2)
        self.assertEqual(200, q2_result[0][0])

    def test_four_concurrent_requests_do_not_leak_state(self) -> None:
        results: list[tuple[int, dict[str, Any], bytes]] = []
        threads = [
            threading.Thread(
                target=lambda value=value: results.append(
                    self.request("POST", "/v1/query", {"question": value})
                )
            )
            for value in ("request-one", "request-two", "request-three", "request-four")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(4, len(results))
        self.assertEqual({200}, {status for status, _payload, _raw in results})
        self.assertEqual(
            {"request-one", "request-two", "request-three", "request-four"},
            {payload["question"] for _status, payload, _raw in results},
        )

    def test_provider_free_route_soak_handles_100_requests(self) -> None:
        for index in range(100):
            status, payload, _ = self.request(
                "POST", "/v1/route", {"question": f"route request {index}"}
            )
            self.assertEqual(200, status)
            self.assertEqual(f"route request {index}", payload["question"])

    def test_deadline_returns_504(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.server = DecisionHTTPServer(
            ("127.0.0.1", 0),
            self.service,
            max_concurrency=1,
            request_timeout_seconds=0.03,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, payload, _ = self.request(
            "POST", "/v1/query", {"question": "timeout"}
        )
        self.assertEqual(504, status)
        self.assertEqual("TIMEOUT", payload["status"])

    def test_runtime_readiness_reports_exact_pair_without_loading_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            graph = root / "generations" / "g1" / "graph.sqlite"
            sidecar = root / "generations" / "g1" / "sidecar.sqlite"
            graph.parent.mkdir(parents=True)
            graph.write_bytes(b"graph")
            sidecar.write_bytes(b"sidecar")
            write_json(
                root / "active_pair.json",
                {
                    "generation_id": "g1",
                    "graph_path": "generations/g1/graph.sqlite",
                    "graph_sha256": sha256_file(graph),
                    "sidecar_path": "generations/g1/sidecar.sqlite",
                    "sidecar_sha256": sha256_file(sidecar),
                    "embedding_backend": "flagembedding-bge-m3-dev",
                    "model_id": "BAAI/bge-m3",
                    "model_revision": "revision-1",
                    "production_qualified": False,
                },
            )
            result = runtime_readiness(root)
        self.assertEqual("READY", result["status"])
        self.assertEqual("g1", result["runtime_pair"]["generation_id"])
        self.assertEqual("revision-1", result["embedding"]["model_revision"])

    def test_http_and_direct_service_return_same_decision_digest(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            service = DecisionService(
                query_engine=StubQueryEngine(runtime), runtime_root=runtime
            )
            arguments = {
                "options": ["Alpha", "Beta"],
                "criteria": ["impact", "cost"],
            }
            direct = service.query(
                "Compare NEE evidence across options and recommend which to choose",
                **arguments,
            )
            self.server = DecisionHTTPServer(("127.0.0.1", 0), service)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            status, via_http, _ = self.request(
                "POST",
                "/v1/query",
                {
                    "question": (
                        "Compare NEE evidence across options and recommend which to choose"
                    ),
                    **arguments,
                },
            )
        self.assertEqual(200, status)
        self.assertEqual(
            direct["result"]["decision_result_digest"],
            via_http["result"]["decision_result_digest"],
        )


if __name__ == "__main__":
    unittest.main()
