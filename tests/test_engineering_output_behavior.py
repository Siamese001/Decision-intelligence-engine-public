"""Unit and integration tests for engineering output behavior governance.

Verifies:
1. Identical success text with unchanged state is rejected.
2. Rollback claims require independent observation of approved content and cache rejection.
3. Stale observations, uncommitted source edits, and later unresolved failures fail closed.
4. Unrelated crashes disguised as expected rejection are detected and rejected.
5. Partial success retains material limitations; omitting them fails closed.
6. Execution environment must be explicitly declared (local, sandbox, or production).
7. Word (<=300) and character (<=3500) limits and forbidden process sections are enforced.
8. Plan-only responses report plans without functional execution claims.
9. Integration with validate_turn_gates.py and validate_implementation_summary.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools.engineering_output import (
    ExecutionEnvironment,
    Observation,
    count_characters,
    count_words,
    render_concise_response,
    render_deliverables_completion,
    render_failure_response,
    render_plan_only_response,
    validate_engineering_response,
    validate_progress_update,
    verify_crash_not_disguised,
    verify_deliverable_links,
    verify_observation_completeness,
    verify_observation_freshness,
    verify_rollback_observation,
    verify_state_change,
)
from tools.validate_implementation_summary import validate_summary_content
from tools.validate_turn_gates import validate_post_turn_gate

ROOT = Path(__file__).resolve().parents[1]


class TestEngineeringOutputBehavior(unittest.TestCase):
    """Test suite verifying behavior-grounded engineering output governance."""

    def test_unchanged_state_with_success_claim_is_rejected(self) -> None:
        """Verify that identical pre-state and post-state rejects a success claim."""
        pre_digest = "sha256:abc123def456"
        post_digest = "sha256:abc123def456"  # Unchanged!

        is_valid, errors = verify_state_change(pre_digest, post_digest, is_success_claim=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("Unchanged state rejected" in e for e in errors))

        # Changed state should pass
        changed_digest = "sha256:fedcba654321"
        is_valid_changed, errors_changed = verify_state_change(pre_digest, changed_digest, is_success_claim=True)
        self.assertTrue(is_valid_changed)
        self.assertEqual(len(errors_changed), 0)

    def test_rollback_claim_requires_independent_content_and_cache_observation(self) -> None:
        """Verify that rollback requires both approved content accessible and cache rejected."""
        # 1. Metadata alone (flags are False)
        obs_metadata_only = Observation(
            action="Executed rollback",
            input_description="Revert to approved release hash",
            expected_effect="Restore approved state",
            observed_effect="Function returned success=True",
            is_rollback=True,
            rollback_approved_accessible=False,
            rollback_incompatible_rejected=False,
        )
        is_valid, errors = verify_rollback_observation(obs_metadata_only)
        self.assertFalse(is_valid)
        self.assertTrue(any("approved content is active" in e for e in errors))
        self.assertTrue(any("incompatible cached data" in e for e in errors))

        # 2. Approved accessible but cache rejection missing
        obs_half = Observation(
            action="Executed rollback",
            input_description="Revert to approved release hash",
            expected_effect="Restore approved state",
            observed_effect="Restored approved content",
            is_rollback=True,
            rollback_approved_accessible=True,
            rollback_incompatible_rejected=False,
        )
        is_valid_half, errors_half = verify_rollback_observation(obs_half)
        self.assertFalse(is_valid_half)
        self.assertTrue(any("incompatible cached data" in e for e in errors_half))

        # 3. Both independently verified
        obs_complete = Observation(
            action="Executed rollback",
            input_description="Revert to approved release hash",
            expected_effect="Restore approved state",
            observed_effect="Verified approved content active and invalid cache unretrievable",
            is_rollback=True,
            rollback_approved_accessible=True,
            rollback_incompatible_rejected=True,
        )
        is_valid_ok, errors_ok = verify_rollback_observation(obs_complete)
        self.assertTrue(is_valid_ok)
        self.assertEqual(len(errors_ok), 0)

    def test_stale_observation_due_to_modified_source_is_rejected(self) -> None:
        """Verify that changed target source files invalidate an earlier observation."""
        obs = Observation(
            action="Ran unit tests",
            input_description="All fixtures",
            expected_effect="Pass",
            observed_effect="All tests passed",
            source_files_hash="sha256:source_v1",
        )
        current_hash = "sha256:source_v2_dirty"
        is_valid, errors = verify_observation_freshness(obs, current_source_hash=current_hash)
        self.assertFalse(is_valid)
        self.assertTrue(any("Stale observation rejected" in e for e in errors))

    def test_unresolved_later_failure_rejects_earlier_success(self) -> None:
        """Verify that an earlier success cannot conceal a subsequent failure."""
        obs = Observation(
            action="Ran integration suite",
            input_description="Full test inputs",
            expected_effect="Pass",
            observed_effect="Pass on attempt 1",
            attempt_number=1,
            is_success=True,
        )
        is_valid, errors = verify_observation_freshness(obs, latest_attempt_succeeded=False)
        self.assertFalse(is_valid)
        self.assertTrue(any("Unresolved later failure" in e for e in errors))

    def test_unrelated_crash_disguised_as_expected_rejection_is_rejected(self) -> None:
        """Verify that python tracebacks or segfaults are not accepted as business rejection."""
        crash_output = """Traceback (most recent call last):
  File "engine.py", line 42, in process
    raise ZeroDivisionError("division by zero")
"""
        is_valid, errors = verify_crash_not_disguised(crash_output, expected_rejection=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("Unrelated crash disguised as expected rejection" in e for e in errors))

        clean_error = "ValidationFailure: Input score -1.0 is outside allowable domain [0.0, 1.0]."
        is_clean_valid, clean_errors = verify_crash_not_disguised(clean_error, expected_rejection=True)
        self.assertTrue(is_clean_valid)
        self.assertEqual(len(clean_errors), 0)

    def test_partial_success_retains_material_limitations(self) -> None:
        """Verify that omitted material limitations fail response validation."""
        obs = Observation(
            action="Ran batch ingestion",
            input_description="100 candidate items",
            expected_effect="Complete ingestion",
            observed_effect="Processed 90 items successfully",
            limitations=["10 items skipped due to corrupted audio header"],
            pre_state_digest="hash1",
            post_state_digest="hash2",
            environment=ExecutionEnvironment.LOCAL,
        )

        # Response omitting the limitation
        omitted_text = (
            "Ran batch ingestion with 100 candidate items in local environment. "
            "Observed 90 items processed successfully. Everything completed."
        )
        res_omitted = validate_engineering_response(omitted_text, observation=obs)
        self.assertFalse(res_omitted.is_valid)
        self.assertTrue(any("Omitted material limitation" in e for e in res_omitted.errors))

        # Response retaining the limitation
        retained_text = (
            "Ran batch ingestion with 100 candidate items in local environment. "
            "Observed 90 items processed successfully. Remaining limitation: 10 items "
            "skipped due to corrupted audio header."
        )
        res_retained = validate_engineering_response(retained_text, observation=obs)
        self.assertTrue(res_retained.is_valid, f"Expected valid, got: {res_retained.errors}")

    def test_unspecified_environment_is_rejected(self) -> None:
        """Verify that execution environment must be explicitly declared."""
        obs = Observation(
            action="Tested module",
            input_description="Input data",
            expected_effect="Pass",
            observed_effect="Passed",
            environment=ExecutionEnvironment.UNSPECIFIED,
        )
        is_valid, errors = verify_observation_completeness(obs)
        self.assertFalse(is_valid)
        self.assertTrue(any("Execution environment must be explicitly declared" in e for e in errors))

    def test_concise_response_length_ceilings(self) -> None:
        """Verify that output exceeding 300 words or 3500 chars is rejected."""
        # 301 words
        oversized_words = "word " * 301
        res_words = validate_engineering_response(oversized_words, require_observed_behavior=False)
        self.assertFalse(res_words.is_valid)
        self.assertTrue(any("exceeds maximum allowed ceiling of 300 words" in e for e in res_words.errors))

        # > 3500 chars
        oversized_chars = "a" * 3501
        res_chars = validate_engineering_response(oversized_chars, require_observed_behavior=False)
        self.assertFalse(res_chars.is_valid)
        self.assertTrue(any("exceeds maximum allowed ceiling of 3500 characters" in e for e in res_chars.errors))

        # Minimal progress update <= 20 words
        valid_progress = "Fixed parser error in tokenizer; running verification tests."
        p_res = validate_progress_update(valid_progress)
        self.assertTrue(p_res.is_valid)

        oversized_progress = "word " * 21
        p_bad = validate_progress_update(oversized_progress)
        self.assertFalse(p_bad.is_valid)
        self.assertTrue(any("exceeds maximum ceiling of 20 words" in e for e in p_bad.errors))

    def test_forbidden_process_sections_are_rejected(self) -> None:
        """Verify that scratchpad headers, model telemetry, and all-wave tables are rejected."""
        text_with_scratchpad = """### 🧠 Agent Reasoning Scratchpad
- **Active Model & Provider**: gpt-4
- **Invocation Role**: Code Refactoring
Ran test in local. Observed pass."""
        res_sp = validate_engineering_response(text_with_scratchpad)
        self.assertFalse(res_sp.is_valid)
        self.assertTrue(any("Agent Reasoning Scratchpad header" in e for e in res_sp.errors))
        self.assertTrue(any("Active Model & Provider telemetry" in e for e in res_sp.errors))

        text_with_wave_table = """Ran tests in local. Observed pass.

| Wave Number | Description | Completion Status |
| :--- | :--- | :--- |
| Wave 1 | Core logic | COMPLETE |
"""
        res_table = validate_engineering_response(text_with_wave_table)
        self.assertFalse(res_table.is_valid)
        self.assertTrue(any("All-wave status table must be omitted" in e for e in res_table.errors))

    def test_plan_only_response_rules(self) -> None:
        """Verify plan-only responses report plans and reject false execution claims."""
        valid_plan = render_plan_only_response("concise runtime output governance")
        res_ok = validate_engineering_response(valid_plan, plan_only=True)
        self.assertTrue(res_ok.is_valid)

        bad_plan = "The plan is ready. We successfully verified behavior and all tests passed."
        res_bad = validate_engineering_response(bad_plan, plan_only=True)
        self.assertFalse(res_bad.is_valid)
        self.assertTrue(any("must not claim functional or behavioral execution success" in e for e in res_bad.errors))

    def test_turn_gates_integration(self) -> None:
        """Verify validate_post_turn_gate integration with candidate text and engineering route."""
        # 1. Missing candidate text with engineering route fails
        res_missing = validate_post_turn_gate(candidate_response=None, response_route="engineering")
        self.assertEqual(res_missing["status"], "FAIL")
        self.assertTrue(any("Missing candidate response text" in i for i in res_missing["issues"]))

        # 2. Compliant concise text passes
        compliant_text = "Ran unit test suite with 10 test cases in sandbox. Observed all 10 tests passed."
        res_pass = validate_post_turn_gate(candidate_response=compliant_text, response_route="engineering")
        self.assertEqual(res_pass["status"], "PASS")
        self.assertTrue(res_pass["checks"]["engineering_output"]["is_valid"])

    def test_implementation_summary_accepts_concise_summary(self) -> None:
        """Verify validate_summary_content accepts valid concise engineering summary without table."""
        concise_summary = (
            "Ran test suite with full coverage fixtures in local environment. "
            "Observed all 8 tests passing without regressions. Generated wave 2 runtime receipt."
        )
        is_valid, errors, records = validate_summary_content(concise_summary, filename="summary.md")
        self.assertTrue(is_valid, f"Expected valid concise summary, got: {errors}")
        self.assertEqual(len(records), 1)
    def test_turn_gates_cli_execution(self) -> None:
        """Verify CLI execution of validate_turn_gates.py with response text and file options."""
        py = sys.executable
        script = ROOT / "tools" / "validate_turn_gates.py"

        # 1. Missing candidate text exits with 1
        proc_missing = subprocess.run(
            [py, str(script), "--mode", "post-turn", "--response-route", "engineering"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(proc_missing.returncode, 1)
        self.assertIn("Missing candidate response text", proc_missing.stdout)

        # 2. Compliant response text exits with 0
        proc_pass = subprocess.run(
            [
                py, str(script), "--mode", "post-turn", "--response-route", "engineering",
                "--response-text", "Ran verification suite with 5 test fixtures in sandbox. Observed all 5 tests passed.",
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(proc_pass.returncode, 0, f"Expected 0, stderr: {proc_pass.stderr}")
        out_json = json.loads(proc_pass.stdout)
        self.assertEqual(out_json["post_turn"]["status"], "PASS")

        # 3. Response file with forbidden scratchpad exits with 1
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("### 🧠 Agent Reasoning Scratchpad\n- **Active Model & Provider**: gpt-4\nRan tests.")
            temp_path = Path(f.name)
        try:
            proc_file = subprocess.run(
                [py, str(script), "--mode", "post-turn", "--response-route", "engineering", "--response-file", str(temp_path)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(proc_file.returncode, 1)
            self.assertIn("Forbidden process section detected", proc_file.stdout)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_meeting_deliverable_links_presentation(self) -> None:
        """Verify that meeting deliverable links are rendered with 4 clickable links and pass validation."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="test_meeting_links_") as td:
            dir_path = Path(td)
            t1 = dir_path / "01_test_transcript.md"
            t2 = dir_path / "02_test_recap.md"
            t3 = dir_path / "03_test_canonical_signal_projection.json"
            t_judge = dir_path / "llm_judge_evaluation.json"

            t1.write_text("# Transcript", encoding="utf-8")
            t2.write_text("# Recap", encoding="utf-8")
            t3.write_text("{}", encoding="utf-8")
            t_judge.write_text('{"status": "PASS", "composite_score": 95.0}', encoding="utf-8")

            deliverables = {
                "Output 01 Transcript": t1,
                "Output 02 Recap": t2,
                "Output 03 Signal Projection": t3,
                "LLM Judge Evaluation": t_judge,
            }

            rendered = render_deliverables_completion(
                action="ingest-meeting",
                scope="Sprint Planning (10-09-2026)",
                input_desc="source recording and metadata",
                environment=ExecutionEnvironment.LOCAL,
                deliverables=deliverables,
                judge_verdict="PASS",
                judge_score=95.0,
                package_only=True,
                limitation_or_next="Package-only intake completed; publication to canonical graph requires activation.",
            )

            # Assert text limits and conciseness
            val_res = validate_engineering_response(rendered)
            self.assertTrue(val_res.is_valid, f"Validation errors: {val_res.errors}")
            self.assertLessEqual(val_res.word_count, 300)
            self.assertLessEqual(val_res.char_count, 3500)

            # Assert link targets and resolution
            is_links_valid, link_errors = verify_deliverable_links(
                rendered,
                deliverables,
                is_package_only=True,
                expected_judge_verdict="PASS",
                verify_files_on_disk=True,
            )
            self.assertTrue(is_links_valid, f"Link errors: {link_errors}")

    def test_missing_deliverable_file_fails_verification(self) -> None:
        """Verify that a link to a nonexistent file on disk fails verification."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="test_missing_file_") as td:
            dir_path = Path(td)
            t1 = dir_path / "01_exists.md"
            t1.write_text("content", encoding="utf-8")
            t2_missing = dir_path / "02_missing.md"  # Does NOT exist!

            deliverables = {
                "Output 01 Transcript": t1,
                "Output 02 Recap": t2_missing,
            }
            rendered = render_deliverables_completion(
                action="ingest-meeting",
                scope="TestScope",
                environment=ExecutionEnvironment.LOCAL,
                deliverables=deliverables,
            )

            is_valid, errors = verify_deliverable_links(
                rendered,
                deliverables,
                verify_files_on_disk=True,
            )
            self.assertFalse(is_valid)
            self.assertTrue(any("does not exist on disk" in e for e in errors))

    def test_failed_judge_verdict_cannot_be_advertised_as_pass(self) -> None:
        """Verify that a failed judge evaluation cannot be advertised as pass."""
        text_claiming_pass = (
            "Executed ingest-meeting for Scope in local. "
            "Synthesized all deliverables and passed all judge gates. "
            "- **Output 01 Transcript**: [`01.md`](file:///tmp/01.md)"
        )
        is_valid, errors = verify_deliverable_links(
            text_claiming_pass,
            expected_links={"Output 01 Transcript": "/tmp/01.md"},
            expected_judge_verdict="FAIL",
            verify_files_on_disk=False,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("claims PASS despite failed judge evaluation" in e for e in errors))

    def test_package_only_cannot_claim_canonical_mutation(self) -> None:
        """Verify that package-only completion cannot claim active graph publication."""
        text_with_mutation_claim = (
            "Executed ingest-meeting for Scope in local. "
            "Synthesized deliverables and published to canonical graph. "
            "- **Output 01 Transcript**: [`01.md`](file:///tmp/01.md)"
        )
        is_valid, errors = verify_deliverable_links(
            text_with_mutation_claim,
            expected_links={"Output 01 Transcript": "/tmp/01.md"},
            is_package_only=True,
            verify_files_on_disk=False,
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("Package-only response cannot claim active graph publication" in e for e in errors))

    def test_non_meeting_modalities_deliverables(self) -> None:
        """Verify document and notes modalities render and pass verification with smaller output contracts."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="test_non_meeting_") as td:
            dir_path = Path(td)
            out_03 = dir_path / "03_doc_signal_projection.json"
            notes = dir_path / "extraction_notes.md"
            judge = dir_path / "llm_judge_evaluation.json"

            out_03.write_text("{}", encoding="utf-8")
            notes.write_text("# Notes", encoding="utf-8")
            judge.write_text('{"status": "PASS", "composite_score": 91.0}', encoding="utf-8")

            # 1. Document modality: Output 03 + extraction notes + judge
            doc_deliverables = {
                "Output 03 Signal Projection": out_03,
                "Extraction Notes": notes,
                "LLM Judge Evaluation": judge,
            }
            doc_rendered = render_deliverables_completion(
                action="ingest-document",
                scope="Architecture Spec",
                input_desc="technical document",
                environment=ExecutionEnvironment.LOCAL,
                deliverables=doc_deliverables,
                judge_verdict="PASS",
                judge_score=91.0,
                package_only=True,
                limitation_or_next="Document package complete; active graph mutation deferred pending activation.",
            )
            val_doc = validate_engineering_response(doc_rendered)
            self.assertTrue(val_doc.is_valid, f"Doc validation errors: {val_doc.errors}")
            doc_links_ok, doc_errs = verify_deliverable_links(
                doc_rendered, doc_deliverables, is_package_only=True, expected_judge_verdict="PASS", verify_files_on_disk=True
            )
            self.assertTrue(doc_links_ok, f"Doc link errors: {doc_errs}")

            # 2. Notes modality: Output 03 + judge
            notes_deliverables = {
                "Output 03 Signal Projection": out_03,
                "LLM Judge Evaluation": judge,
            }
            notes_rendered = render_deliverables_completion(
                action="ingest-notes",
                scope="Weekly Memo",
                input_desc="raw notes text",
                environment=ExecutionEnvironment.LOCAL,
                deliverables=notes_deliverables,
                judge_verdict="PASS",
                judge_score=91.0,
                package_only=True,
                limitation_or_next="Notes package complete; isolated until activation.",
            )
            val_notes = validate_engineering_response(notes_rendered)
            self.assertTrue(val_notes.is_valid, f"Notes validation errors: {val_notes.errors}")
            notes_links_ok, notes_errs = verify_deliverable_links(
                notes_rendered, notes_deliverables, is_package_only=True, expected_judge_verdict="PASS", verify_files_on_disk=True
            )
            self.assertTrue(notes_links_ok, f"Notes link errors: {notes_errs}")

    def test_jargon_in_engineering_response_is_rejected(self) -> None:
        """Verify that snake_case, code keywords, Greek math symbols, and plumbing acronyms fail validation."""
        jargon_payloads = [
            ("Ran tests in local. Observed margin_threshold updated.", "snake_case"),
            ("Ran tests in local. Observed def run_worker() passed.", "code keyword"),
            ("Ran tests in local. Observed kappa = 0.85 agreement score.", "Greek or mathematical symbol"),
            ("Ran tests in local. Observed ACID transactions verified.", "technical plumbing acronym"),
            ("Ran tests in local. Observed WAL logging operational.", "technical plumbing acronym"),
            ("Ran tests in local. Observed Pydantic schema validated.", "PascalCase code identifier"),
        ]
        for payload, category in jargon_payloads:
            res = validate_engineering_response(payload)
            self.assertFalse(
                res.is_valid,
                f"Expected validation failure for {category} in payload: '{payload}'",
            )
            self.assertTrue(
                any("Forbidden technical jargon detected" in e for e in res.errors),
                f"Expected jargon error message for payload: '{payload}', got: {res.errors}",
            )
            self.assertIn("jargon_violations", res.details)
            self.assertGreater(len(res.details["jargon_violations"]), 0)

    def test_valid_plain_language_response_passes(self) -> None:
        """Verify natural plain English without code jargon passes validation cleanly."""
        plain_payload = (
            "Ran verification suite with five test fixtures in sandbox. "
            "Observed all five tests passed without regression."
        )
        res = validate_engineering_response(plain_payload)
        self.assertTrue(res.is_valid, f"Expected valid plain language, got: {res.errors}")
        self.assertEqual(len(res.errors), 0)


if __name__ == "__main__":
    unittest.main()
