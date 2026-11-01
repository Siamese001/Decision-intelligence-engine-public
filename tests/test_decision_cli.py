from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli import build_parser, execute


class DecisionCliTests(unittest.TestCase):
    def test_general_route_does_not_initialize_embedding_backend(self) -> None:
        args = build_parser().parse_args(
            ["ask", "--question", "Write an email summarizing these notes"]
        )
        with patch("dual_pipeline.cli._backend", side_effect=AssertionError("backend called")):
            result = execute(args)
        self.assertEqual("HANDOFF_READY", result["terminal_state"])
        self.assertEqual(0, result["retrieval_call_count"])
        self.assertEqual(0, result["provider_call_count"])

    def test_ambiguous_route_does_not_initialize_embedding_backend(self) -> None:
        args = build_parser().parse_args(["ask", "--question", "What should we do?"])
        with patch("dual_pipeline.cli._backend", side_effect=AssertionError("backend called")):
            result = execute(args)
        self.assertEqual("CLARIFICATION_REQUIRED", result["terminal_state"])

    def test_decision_arguments_are_repeatable(self) -> None:
        args = build_parser().parse_args(
            [
                "ask",
                "--question",
                "Compare NEE evidence and recommend an option",
                "--option",
                "Alpha",
                "--option",
                "Beta",
                "--criterion",
                "impact",
                "--criterion",
                "cost",
            ]
        )
        self.assertEqual(["Alpha", "Beta"], args.option)
        self.assertEqual(["impact", "cost"], args.criterion)

    def test_decision_server_defers_graph_runtime_initialization(self) -> None:
        args = build_parser().parse_args(
            ["decision-serve", "--host", "127.0.0.1", "--port", "0"]
        )
        with (
            patch("dual_pipeline.cli._backend", side_effect=AssertionError("backend called")),
            patch("dual_pipeline.cli.engine.serve_decision_api") as serve,
        ):
            result = execute(args)
        self.assertEqual({"status": "STOPPED"}, result)
        serve.assert_called_once()

    def test_plain_windows_python_emits_utf8_without_hidden_flag(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-E",
                str(root / "tools" / "dev_decision_e2e.py"),
                "--question",
                "Rewrite this phrase: résumé → decision",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("résumé → decision", completed.stdout)


if __name__ == "__main__":
    unittest.main()
