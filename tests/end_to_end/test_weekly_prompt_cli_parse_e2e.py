"""E2E: the weekly prompt's executable command parses against the real `nee ingest-document` parser.

Proves the scheduled command is a valid governed CLI invocation and resolves to a
dry-run (no --mutate-graph) with real BGE-M3 required. No pipeline execution occurs.
"""

from __future__ import annotations

import shlex
import unittest

from dual_pipeline.cli.parser import build_parser
from tests.test_weekly_prompt_governance_edge_cases import PROMPT, command_block, interactive_block


def _argv() -> list[str]:
    text = command_block(PROMPT.read_text(encoding="utf-8"))
    tokens = shlex.split(text.replace("\\\n", " "))
    assert tokens[:2] == ["nee", "ingest-document"], tokens[:2]
    return tokens[1:]


class WeeklyPromptCliParseE2E(unittest.TestCase):
    def test_command_parses_as_active_intent_with_real_bge_m3(self) -> None:
        args = build_parser().parse_args(_argv())
        self.assertEqual(args.command, "ingest-document")
        self.assertTrue(args.require_bge_m3)
        self.assertFalse(args.force_rerun)
        self.assertIsNone(args.reviewer)
        self.assertFalse(args.allow_dev_bge_m3)
        self.assertEqual(args.embedding_backend, "bge-m3")
        self.assertEqual(args.project, "project_nexus")
        self.assertEqual(args.classification, "internal")
        self.assertEqual(args.provider, "llm")

    def test_interactive_command_parses_as_mutating_with_production_backend(self) -> None:
        text = interactive_block(PROMPT.read_text(encoding="utf-8"))
        tokens = shlex.split(text.replace("\\\n", " "))
        self.assertEqual(tokens[:2], ["nee", "ingest-document"])
        args = build_parser().parse_args(tokens[1:])
        self.assertEqual(args.reviewer, "<name from your answer>")
        self.assertEqual(args.directive_id, "dir-weekly-trends-MM-DD-YYYY")
        self.assertTrue(args.require_bge_m3)
        self.assertFalse(args.allow_dev_bge_m3)
        self.assertEqual(args.embedding_backend, "bge-m3")
        self.assertFalse(args.force_rerun)

    def test_unknown_flag_would_fail_parsing(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(_argv() + ["--not-a-real-flag"])


if __name__ == "__main__":
    unittest.main()
