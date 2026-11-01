#!/usr/bin/env python3
"""End-to-end test verifying HTTP concurrency, queue scaling, and probe performance (Wave 9).

Verifies:
1. Full HTTP service concurrency across live route and query endpoints.
2. Deep health check and metrics visibility into queue utilization.
3. Fast cached readiness probe (< 15ms including network loopback) on live runtime.
"""
from __future__ import annotations

import concurrent.futures
import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import read_json  # noqa: E402
from dual_pipeline.decision_api import DecisionHTTPServer, runtime_readiness  # noqa: E402
from dual_pipeline.decision_service_core.service import DecisionService  # noqa: E402


class StubQueryEngine:
    def ask(self, question: str, **kwargs: object) -> dict[str, object]:
        return {
            "question": question,
            "terminal_state": "DECISION_PRODUCED",
            "message": f"Processed: {question}",
            "route": "exact_lookup",
        }


@pytest.mark.e2e
class TestHttpConcurrencyE2E(unittest.TestCase):
    """E2E suite verifying HTTP concurrency scaling and probe speed on live runtime."""

    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return

        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)

    def setUp(self) -> None:
        if not self.has_active_pair:
            self.skipTest("Active runtime pair not available for E2E HTTP concurrency test")

        self.service = DecisionService(
            query_engine=StubQueryEngine(),
            runtime_root=self.runtime_root,
        )
        self.server = DecisionHTTPServer(
            ("127.0.0.1", 0),
            self.service,
            max_concurrency=4,
            max_queue_size=8,
            readiness_check=lambda: runtime_readiness(self.runtime_root),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown_gracefully(timeout=2.0)
        self.thread.join(timeout=2.0)

    def _http_request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        body = json.dumps(payload).encode("utf-8") if payload else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return status, data

    def test_e2e_live_service_concurrency_and_health_probes(self) -> None:
        """Verify concurrent requests across live HTTP server and deep health report."""
        # 1. Probe readiness and health
        ready_status, ready_data = self._http_request("GET", "/ready")
        self.assertEqual(ready_status, 200)
        self.assertEqual(ready_data["status"], "READY")

        health_status, health_data = self._http_request("GET", "/health")
        self.assertEqual(health_status, 200)
        self.assertEqual(health_data["status"], "HEALTHY")
        self.assertIn("queue", health_data["checks"])
        self.assertEqual(health_data["checks"]["queue"]["max_workers"], 4)
        self.assertEqual(health_data["checks"]["queue"]["max_queue_size"], 8)

        # 2. Dispatch 8 concurrent requests across route and query
        num_requests = 8

        def worker(idx: int) -> int:
            if idx % 2 == 0:
                status, res = self._http_request(
                    "POST", "/v1/route", {"question": f"Route test {idx}"}
                )
            else:
                status, res = self._http_request(
                    "POST", "/v1/query", {"question": f"Query test {idx}"}
                )
            return status

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as pool:
            futures = [pool.submit(worker, i) for i in range(num_requests)]
            statuses = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(statuses, [200] * num_requests)

        # 3. Post-run stats verify completion
        final_health_status, final_health_data = self._http_request("GET", "/health")
        self.assertEqual(final_health_status, 200)
        q_stats = final_health_data["checks"]["queue"]
        self.assertGreaterEqual(q_stats["completed_total"], num_requests)
        self.assertEqual(q_stats["rejected_total"], 0)

    def test_e2e_readiness_probe_http_latency_under_15ms(self) -> None:
        """Verify repeated HTTP /ready probe calls complete in < 15ms over loopback."""
        # Warmup probe
        s0, _ = self._http_request("GET", "/ready")
        self.assertEqual(s0, 200)

        iterations = 20
        start = time.monotonic()
        for _ in range(iterations):
            status, data = self._http_request("GET", "/ready")
            self.assertEqual(status, 200)
            self.assertEqual(data["status"], "READY")
        elapsed_total = time.monotonic() - start
        avg_ms = (elapsed_total / iterations) * 1000

        self.assertLess(
            avg_ms,
            15.0,
            f"Average loopback readiness probe latency was {avg_ms:.2f}ms >= 15.0ms",
        )


if __name__ == "__main__":
    unittest.main()
