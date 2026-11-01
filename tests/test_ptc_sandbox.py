"""
tests/test_ptc_sandbox.py

Unit tests for Programmatic Tool Calling (PTC) sandbox, AST validator, and spooler.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCSecurityViolationError,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.sandbox import PTCAstValidator, PTCSandbox
from dual_pipeline.ptc.spooler import PTCOutputSpooler


class TestPTCSandbox(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name)
        self.spooler = PTCOutputSpooler(spool_dir=self.runtime_root / "spool")
        self.sandbox = PTCSandbox(spooler=self.spooler)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ast_validator_blocks_forbidden_imports(self) -> None:
        PTCAstValidator()
        forbidden_scripts = [
            "import os\nprint(os.getcwd())",
            "import subprocess\nsubprocess.run(['ls'])",
            "import sqlite3\nconn = sqlite3.connect(':memory:')",
            "from sys import exit\nexit(0)",
            "import requests\nr = requests.get('https://example.com')",
        ]
        for script in forbidden_scripts:
            with self.subTest(script=script):
                with self.assertRaises(PTCSecurityViolationError):
                    self.sandbox.validate_script(script)

    def test_ast_validator_blocks_forbidden_calls(self) -> None:
        forbidden_scripts = [
            "x = eval('1 + 1')",
            "exec('x = 2')",
            "c = compile('x = 1', '', 'exec')",
            "mod = __import__('os')",
        ]
        for script in forbidden_scripts:
            with self.subTest(script=script):
                with self.assertRaises(PTCSecurityViolationError):
                    self.sandbox.validate_script(script)

    def test_ast_validator_allows_safe_code(self) -> None:
        safe_script = """
import math
import json
import re

def run():
    nums = [1, 2, 3, 4, 5]
    total = sum(nums)
    squared = [x**2 for x in nums]
    return {"total": total, "squared": squared, "sqrt": math.sqrt(16)}
"""
        # Should not raise
        self.sandbox.validate_script(safe_script)

    def test_sandbox_execution_success(self) -> None:
        script = """
def run(data_proxy):
    items = data_proxy.get_items()
    filtered = [i for i in items if i["score"] > 50]
    return {"matched_count": len(filtered), "top": filtered[0]["id"]}
"""
        class MockDataProxy:
            def get_items(self):
                return [{"id": "a", "score": 80}, {"id": "b", "score": 20}]

        ctx = PTCExecutionContext(
            task_id="task_success_01",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=4096),
            proxies={"data_proxy": MockDataProxy()},
        )

        res = self.sandbox.execute(script, ctx)
        self.assertEqual(res.status, PTCStatus.SUCCESS)
        self.assertEqual(res.data["matched_count"], 1)
        self.assertEqual(res.data["top"], "a")
        self.assertGreater(res.execution_time_ms, 0.0)

    def test_sandbox_timeout_enforcement(self) -> None:
        script = """
import time
def run():
    # Long busy loop
    end = time.time() + 2.0
    while time.time() < end:
        pass
    return "done"
"""
        ctx = PTCExecutionContext(
            task_id="task_timeout_01",
            runtime_root=self.runtime_root,
            timeout_seconds=0.1,  # Strict timeout
            allowed_modules=["time"],
        )

        res = self.sandbox.execute(script, ctx)
        self.assertEqual(res.status, PTCStatus.TIMEOUT)
        self.assertIn("EXECUTION_TIMEOUT", res.error_message)

    def test_output_spooler_within_budget(self) -> None:
        budget = TokenBudget(max_payload_bytes=1000)
        data = {"status": "OK", "records": [1, 2, 3]}

        summary = self.spooler.marshal(
            task_id="task_budget_ok",
            data=data,
            token_budget=budget,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data, data)
        self.assertIsNone(summary.artifact_ref)

    def test_output_spooler_exceeds_budget(self) -> None:
        budget = TokenBudget(max_payload_bytes=100)  # Very small budget
        large_data = [{"id": f"rec_{i}", "payload": "x" * 50} for i in range(20)]

        summary = self.spooler.marshal(
            task_id="task_spool_large",
            data=large_data,
            token_budget=budget,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(summary.status, PTCStatus.SPOOLED_POINTER)
        self.assertIsNotNone(summary.artifact_ref)
        self.assertIsNotNone(summary.sha256_digest)

        # Verify spooled file on disk
        spool_path = Path(summary.artifact_ref)
        self.assertTrue(spool_path.is_file())
        with open(spool_path, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)
        self.assertEqual(len(loaded_data), 20)
        self.assertEqual(summary.data["total_items"], 20)


if __name__ == "__main__":
    unittest.main()
