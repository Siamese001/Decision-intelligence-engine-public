from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "documentation" / "NEE_Ingestion_GraphRAG_Prompt_v1_3_HARDENED.txt"


class HardenedIngestionPromptTests(unittest.TestCase):
    def test_prompt_declares_all_checked_in_u0_contract_families(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        for required in (
            "urn:nee:dual-pipeline:copilot-intake:1.0.0",
            "urn:nee:dual-pipeline:google-meet-gemini-intake:1.0.0",
            "urn:nee:dual-pipeline:architecture-diagram-intake:1.0.0",
            "urn:nee:dual-pipeline:markdown-source-intake:1.0.0",
            "MISSING_CLASSIFICATION",
            "MISSING_AUDIENCES",
            "UNSUPPORTED_SOURCE_ADAPTER",
            "U0 L1 L0 C0.1 C0.2 C0.3 X1 X2 X3 L4",
            "intake-completion-noop",
        ):
            self.assertIn(required, text)

    def test_machine_json_examples_are_parseable(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        blocks = re.findall(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL)
        self.assertGreaterEqual(len(blocks), 3)
        for block in blocks:
            json.loads(block)

    def test_copilot_and_non_copilot_source_rule_is_explicit(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("The Copilot path does not require a separate raw transcript", text)
        self.assertIn("Preserve the exact raw transcript or minutes bytes", text)
        self.assertIn("Do not substitute another source", text)

    def test_pre_intake_classifier_is_advisory_and_fail_closed(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        for required in (
            "deterministic,",
            "provider-free pre-intake classifier",
            '"initial_classification"',
            "approve or override",
            "never proposes",
            "`public` without an explicit declaration",
            "The proposal is not approval",
            "Unknown audience mappings remain explicit operator input",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
