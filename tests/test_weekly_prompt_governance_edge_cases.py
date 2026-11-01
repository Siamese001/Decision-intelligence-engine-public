"""Weekly market-intel scheduled prompt: governance invariants (adversarial / edge cases).

Pins the dry-run-only posture of automations/prompts/weekly_market_intelligence_agent.md:
- the executable Stage 3 command never mutates the graph or uses dev embeddings,
- pre-flight / post-verification stages and hard BLOCKED reason codes are present,
- mutation, self-approval and weakening attempts are detected by the checker itself.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1] / "automations" / "prompts" / "weekly_market_intelligence_agent.md"

FORBIDDEN_EXECUTABLE_FLAGS = (
    "--dry-run-graph",
    "--no-mutate-graph",
    "--reviewer",
    "--rationale",
    "--allow-dev-bge-m3",
    "bge-m3-dev",
    "--no-require-bge-m3",
    "--force-rerun",
)
REQUIRED_SECTIONS = ("STAGE 0: PRE-FLIGHT", "STAGE 4: POST-VERIFICATION", "STAGE 5: COMPLETION CARD")
REQUIRED_REASON_CODES = (
    "PRE_BASELINE_INVALID",
    "GRAPH_HASH_CHANGED_UNEXPECTEDLY",
    "POST_DELTA_MISSING",
    "POST_DELTA_MISMATCH",
    "OPERATOR_INPUT_REQUIRED",
    "ENV_UNAVAILABLE",
    "UNSAFE_ENVIRONMENT",
    "DIRECTIVE_EXISTS",
)


def command_blocks(text: str) -> list[str]:
    """Return every fenced bash block, in order."""
    return re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)


def command_block(text: str) -> str:
    """Return the scheduled-mode executable block (the first fenced bash block)."""
    blocks = command_blocks(text)
    return blocks[0] if blocks else ""


def interactive_block(text: str) -> str:
    """Return the Stage 6 interactive-mode block (the second fenced bash block), if any."""
    blocks = command_blocks(text)
    return blocks[1] if len(blocks) > 1 else ""


INTERACTIVE_FORBIDDEN = (
    "bge-m3-dev",
    "--allow-dev-bge-m3",
    "--force-rerun",
    "--no-require-bge-m3",
    "--auto-mutate-on-pass",
    "--reviewer",
    "--rationale",
    "Antigravity Scheduler",
)


def _scheduled_violations(text: str) -> list[str]:
    found: list[str] = []
    block = command_block(text)
    if not block:
        found.append("NO_COMMAND_BLOCK")
    found += [f"FORBIDDEN_FLAG:{flag}" for flag in FORBIDDEN_EXECUTABLE_FLAGS if flag in block]
    found += [f"MISSING_SECTION:{s}" for s in REQUIRED_SECTIONS if s not in text]
    found += [f"MISSING_REASON:{c}" for c in REQUIRED_REASON_CODES if c not in text]
    if "not reported" in text.lower():
        found.append("NOT_REPORTED_ESCAPE")
    if re.search(r"--reviewer\s+\"Antigravity Scheduler\"", text):
        found.append("SELF_APPROVAL_IDENTITY")
    return found


def _interactive_block_violations(iblock: str) -> list[str]:
    if not iblock:
        return ["NO_INTERACTIVE_BLOCK"]
    found: list[str] = []
    found += [f"INTERACTIVE_FORBIDDEN:{flag}" for flag in INTERACTIVE_FORBIDDEN if flag in iblock]
    if "--dry-run-graph" in iblock:
        found.append("INTERACTIVE_STILL_DRY_RUN")
    return found


STAGE6_REQUIRED = ("ask_user_question", "ONCE, atomically", "OPERATOR_INPUT_REQUIRED", "NON_PRODUCTION_PAIR", "NO_ROLLBACK_COPY", "POST_VERIFICATION_FAILED")


def _mode_violations(text: str) -> list[str]:
    found: list[str] = []
    if re.search(r"^MODE:\s*", text, re.MULTILINE):
        found.append("PROMPT_MODE_MARKER")
    if "AuthorityContext" not in text:
        found.append("MISSING_TRUSTED_LAUNCH")
    if "guided by `ask_user_question`" not in text:
        found.append("MISSING_UNIFIED_INTERACTIVE_PIPELINE")
    if "STAGE 6: INTERACTIVE PROMOTION" not in text:
        found.append("MISSING_SECTION:STAGE 6: INTERACTIVE PROMOTION")
    found += _interactive_block_violations(interactive_block(text))
    if len(command_blocks(text)) != 2:
        found.append("UNEXPECTED_COMMAND_BLOCK_COUNT")
    stage6 = text.split("STAGE 6: INTERACTIVE PROMOTION", 1)[-1]
    found += [f"STAGE6_MISSING:{n}" for n in STAGE6_REQUIRED if n not in stage6]
    return found


def violations(text: str) -> list[str]:
    """Return governance violations found in a prompt text."""
    return _scheduled_violations(text) + _mode_violations(text)


class WeeklyPromptGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = PROMPT.read_text(encoding="utf-8")

    def test_committed_prompt_has_no_violations(self) -> None:
        self.assertEqual(violations(self.text), [])

    def test_ask_user_question_guides_pipeline_promotion(self) -> None:
        self.assertIn("guided by `ask_user_question`", self.text)
        self.assertIn("Present human approval decisions atomically via `ask_user_question` in Stage 6", self.text)

    def test_exactly_one_executable_ingest_command(self) -> None:
        self.assertEqual(command_block(self.text).count("nee ingest-document"), 1)

    # --- adversarial: the checker must catch tampering ---------------------------------

    def test_dry_run_flag_injection_is_detected(self) -> None:
        tampered = self.text.replace("--enforce-worktree\n```", "--enforce-worktree \\\n  --dry-run-graph\n```", 1)
        self.assertIn("FORBIDDEN_FLAG:--dry-run-graph", violations(tampered))

    def test_dev_embedding_injection_is_detected(self) -> None:
        tampered = self.text.replace("  --enforce-worktree\n```", "  --embedding-backend bge-m3-dev --allow-dev-bge-m3 \\\n  --enforce-worktree\n```", 1)
        found = violations(tampered)
        self.assertIn("FORBIDDEN_FLAG:bge-m3-dev", found)
        self.assertIn("FORBIDDEN_FLAG:--allow-dev-bge-m3", found)

    def test_self_approval_identity_is_detected(self) -> None:
        tampered = self.text.replace("  --enforce-worktree\n```", "  --reviewer \"Antigravity Scheduler\" \\\n  --enforce-worktree\n```", 1)
        found = violations(tampered)
        self.assertIn("SELF_APPROVAL_IDENTITY", found)
        self.assertIn("FORBIDDEN_FLAG:--reviewer", found)

    def test_no_mutate_flag_injection_is_detected(self) -> None:
        tampered = self.text.replace("--enforce-worktree\n```", "--enforce-worktree \\\n  --no-mutate-graph\n```", 1)
        self.assertIn("FORBIDDEN_FLAG:--no-mutate-graph", violations(tampered))

    def test_removed_postverification_is_detected(self) -> None:
        tampered = self.text.replace("STAGE 4: POST-VERIFICATION", "STAGE 4: SUMMARY")
        self.assertIn("MISSING_SECTION:STAGE 4: POST-VERIFICATION", violations(tampered))

    def test_not_reported_escape_is_detected(self) -> None:
        self.assertIn("NOT_REPORTED_ESCAPE", violations(self.text + "\nUse `not reported` when absent.\n"))

    def test_missing_reason_code_is_detected(self) -> None:
        tampered = self.text.replace("GRAPH_HASH_CHANGED_UNEXPECTEDLY", "X")
        self.assertIn("MISSING_REASON:GRAPH_HASH_CHANGED_UNEXPECTEDLY", violations(tampered))

    # --- unified interactive pipeline --------------------------------------------------

    def test_header_and_authority_context_present(self) -> None:
        self.assertTrue(self.text.startswith("# Weekly Market Intelligence Agent Specification\n"))
        self.assertIn("AuthorityContext", self.text)
        self.assertNotIn("PROMPT_MODE_MARKER", violations(self.text))

    def test_prompt_mode_marker_is_detected(self) -> None:
        tampered = "MODE: interactive\n" + self.text
        self.assertIn("PROMPT_MODE_MARKER", violations(tampered))

    def test_unified_interactive_pipeline_removal_is_detected(self) -> None:
        tampered = self.text.replace("guided by `ask_user_question`", "in background mode")
        self.assertIn("MISSING_UNIFIED_INTERACTIVE_PIPELINE", violations(tampered))

    def test_scheduled_block_runs_with_active_intent_while_interactive_block_mutates(self) -> None:
        self.assertNotIn("--dry-run-graph", command_block(self.text))
        self.assertNotIn("--no-mutate-graph", command_block(self.text))
        self.assertNotIn("--reviewer", interactive_block(self.text))
        self.assertNotIn("--rationale", interactive_block(self.text))
        self.assertNotIn("--dry-run-graph", interactive_block(self.text))

    def test_interactive_reviewer_flag_injection_is_detected(self) -> None:
        iblock = interactive_block(self.text)
        tampered_iblock = iblock.replace("  --decision approve", '  --decision approve \\\n  --reviewer "Antigravity Scheduler"')
        tampered = self.text.replace(iblock, tampered_iblock)
        found = violations(tampered)
        self.assertIn("INTERACTIVE_FORBIDDEN:--reviewer", found)
        self.assertIn("INTERACTIVE_FORBIDDEN:Antigravity Scheduler", found)

    def test_interactive_dev_embedding_is_detected(self) -> None:
        iblock = interactive_block(self.text)
        tampered_iblock = iblock.replace("  --decision approve", "  --decision approve \\\n  --embedding-backend bge-m3-dev --allow-dev-bge-m3")
        tampered = self.text.replace(iblock, tampered_iblock)
        found = violations(tampered)
        self.assertIn("INTERACTIVE_FORBIDDEN:bge-m3-dev", found)
        self.assertIn("INTERACTIVE_FORBIDDEN:--allow-dev-bge-m3", found)

    def test_removed_stage6_approval_gate_is_detected(self) -> None:
        tampered = self.text.replace("ONCE, atomically", "whenever convenient")
        self.assertIn("STAGE6_MISSING:ONCE, atomically", violations(tampered))

    def test_removed_stage6_rollback_precondition_is_detected(self) -> None:
        tampered = self.text.replace("NO_ROLLBACK_COPY", "X")
        self.assertIn("STAGE6_MISSING:NO_ROLLBACK_COPY", violations(tampered))

    def test_extra_executable_block_is_detected(self) -> None:
        tampered = self.text + "\n```bash\nnee ingest-document --mutate-graph\n```\n"
        self.assertIn("UNEXPECTED_COMMAND_BLOCK_COUNT", violations(tampered))

    def test_exactly_two_command_blocks(self) -> None:
        self.assertEqual(len(command_blocks(self.text)), 2)

    # --- boundary / malformed input ----------------------------------------------------

    def test_empty_prompt_is_rejected(self) -> None:
        found = violations("")
        self.assertIn("NO_COMMAND_BLOCK", found)
        self.assertTrue(any(v.startswith("MISSING_SECTION") for v in found))

    def test_prose_mention_of_forbidden_flag_is_not_executable(self) -> None:
        self.assertIn("--no-mutate-graph", self.text)  # in the prohibition prose
        self.assertNotIn("--no-mutate-graph", command_block(self.text))

    def test_unterminated_code_fence_yields_no_command_block(self) -> None:
        self.assertEqual(command_block("```bash\nnee ingest-document --mutate-graph\n"), "")

    # --- hardened energy & decision intelligence specifications ------------------------

    def test_hardened_research_domains_and_sources_present(self) -> None:
        self.assertIn("FPSC", self.text)
        self.assertIn("FERC", self.text)
        self.assertIn("EIA", self.text)
        self.assertIn("NERC", self.text)
        self.assertIn("EPRI", self.text)
        self.assertIn("hyperscale", self.text.lower())
        self.assertIn("transmission", self.text.lower())
        self.assertIn("bess", self.text.lower())
        self.assertIn("7-day", self.text)
        self.assertIn("90-day", self.text)

    def test_hardened_signal_filter_and_epistemic_modalities_present(self) -> None:
        self.assertIn("STAGE 1.5: SIGNAL FILTERING & GRAPH LOOKUP", self.text)
        self.assertIn("nee ask", self.text)
        self.assertIn("Source Authority", self.text)
        self.assertIn("NEE Relevance", self.text)
        self.assertIn("Materiality", self.text)
        self.assertIn("Novelty", self.text)
        self.assertIn("Actionability", self.text)
        self.assertIn("COMPLETE_NOOP", self.text)
        for modality in ("OBSERVED_FACT", "FORECAST", "PROPOSAL", "SCENARIO", "ANALYST_HYPOTHESIS"):
            self.assertIn(modality, self.text)

    def test_hardened_continuation_and_physical_proof_tokens_present(self) -> None:
        self.assertIn("INV-GRAPH-MUTATION-PHYSICAL-PROOF", self.text)
        self.assertIn("runtime_evidence inventory", self.text)
        self.assertIn("without re-extraction", self.text)
        self.assertIn("Retained for approval continuation", self.text)

    def test_hitl_decision_clusters_and_acronym_precision_present(self) -> None:
        self.assertIn("hitl_clusters", self.text)
        self.assertIn("cluster-broader-market-trends", self.text)
        self.assertIn("cluster-apex_global-apex-implications", self.text)
        self.assertIn("IDEMPOTENT_ALREADY_COMMITTED", self.text)


if __name__ == "__main__":
    unittest.main()

