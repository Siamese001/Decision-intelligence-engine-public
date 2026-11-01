from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "documentation" / "INGESTION_GRAPHRAG_PROMPT_GOOGLE_MEET_GEMINI.md"


class ExecutableMeetingPromptTests(unittest.TestCase):
    def test_drives_source_preprocessing_intake_and_release_lifecycle(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        for required in (
            "Copilot/Teams transcript or recap",
            "Google Meet transcript, Gemini notes export, or raw meeting minutes",
            "intake-directive-open",
            "ingest-google-meet-gemini",
            "U0 L1 L0 C0.1 C0.2 C0.3 X1 X2 X3 L4",
            "graph_delta_summary.json",
            "intake-directive-approve",
            "intake-release-prepare",
            "intake-release-activate",
            "intake-completion-noop",
        ):
            self.assertIn(required, text)

    def test_keeps_mutation_approval_exact_and_nonstanding(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("one exact candidate-lane release", text)
        self.assertIn('canonical_promotion": "excluded', text)
        self.assertIn('standing_authority": "none', text)
        self.assertIn("hold_open_directive", text)

    def test_requires_advisory_classification_before_hitl_approval(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "initial_classification",
            "deterministic, provider-free classifier",
            "approve or override",
            "unknown audience scope remains an explicit decision",
            "does not authorize graph mutation or standing authority",
        ):
            self.assertIn(required, normalized)
