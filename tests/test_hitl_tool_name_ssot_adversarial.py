#!/usr/bin/env python3
"""Adversarial and boundary tests for SSOT tool name enforcement and linter."""

import tempfile
import unittest
from pathlib import Path

import pytest

from config.hitl_tool_name import (
    is_canonical_tool_name,
    is_deprecated_tool_name,
    validate_tool_name,
)
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    format_runtime_question_call,
)
from tools.lint_hitl_tool_name import (
    is_allowlisted_path,
    scan_file_for_deprecated_names,
)


class TestHitlToolNameSSOTAdversarial(unittest.TestCase):
    """Adversarial test cases covering casing variations, boundary regexes, and evasion attempts."""

    def test_casing_variations_deprecation_check(self):
        cases = [
            ("ASK_QUESTION", True),
            ("Ask_Question", True),
            ("ask_Question", True),
            ("ASK_USER", True),
            ("Ask_User", True),
            ("ask_HUMAN", True),
            ("PROMPT_USER", True),
            ("REQUEST_HUMAN_INPUT", True),
            ("ask_user_question", False),
            ("ASK_USER_QUESTION", False),
            ("Ask_User_Question", False),
            ("random_tool_name", False),
        ]
        for case_variant, expected_deprecated in cases:
            with self.subTest(case_variant=case_variant):
                assert is_deprecated_tool_name(case_variant) is expected_deprecated

    def test_canonical_casing_strictness(self):
        # Only exact lowercase matches is_canonical_tool_name
        assert is_canonical_tool_name("ask_user_question") is True
        assert is_canonical_tool_name("Ask_User_Question") is False
        assert is_canonical_tool_name("ASK_USER_QUESTION") is False

        with pytest.raises(ValueError):
            validate_tool_name("Ask_User_Question")

    def test_regex_boundary_no_false_positive_on_canonical_name(self):
        """Ensure substring matches do NOT misclassify 'ask_user_question' as 'ask_user'."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_file = tmp_path / "valid_module.py"
            test_file.write_text(
                """
def run_interaction():
    payload = {"action": "ask_user_question"}
    # Call the canonical ask_user_question tool
    result = ask_user_question(payload)
    return result
""",
                encoding="utf-8",
            )

            # Scanner must return 0 findings
            findings = scan_file_for_deprecated_names(test_file, tmp_path)
            assert len(findings) == 0, f"False positive detected: {findings}"

    def test_linter_detects_deprecated_names_accurately(self):
        """Ensure linter detects deprecated names in non-exempt files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            bad_file = tmp_path / "subsystem" / "violating_code.py"
            bad_file.parent.mkdir(parents=True, exist_ok=True)
            bad_file.write_text(
                """
# Old deprecated pattern
def trigger():
    tool = "ask_question"
    return execute_tool(tool)
""",
                encoding="utf-8",
            )

            findings = scan_file_for_deprecated_names(bad_file, tmp_path)
            assert len(findings) == 1
            line_no, term, content = findings[0]
            assert term == "ask_question"
            assert 'tool = "ask_question"' in content

    def test_linter_detects_ask_user_isolated(self):
        """Ensure isolated 'ask_user' is caught without matching 'ask_user_question'."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            bad_file = tmp_path / "legacy.py"
            bad_file.write_text(
                """
def ask_user(prompt):
    print(prompt)
""",
                encoding="utf-8",
            )

            findings = scan_file_for_deprecated_names(bad_file, tmp_path)
            assert len(findings) == 1
            assert findings[0][1] == "ask_user"

    def test_allowlist_exemptions_honored(self):
        """Verify allowlisted paths are not flagged even when containing deprecated names."""
        assert is_allowlisted_path("config/hitl_tool_name.py") is True
        assert is_allowlisted_path("dual_pipeline/hitl_governance_core/runtime_tool_adapter.py") is True
        assert is_allowlisted_path("tools/lint_hitl_tool_name.py") is True
        assert is_allowlisted_path("config/operator_decision_vocabulary.py") is True
        assert is_allowlisted_path("reports/some_report.md") is True
        assert is_allowlisted_path("receipts/wave1_receipt.json") is True
        assert is_allowlisted_path("docs/some_spec.md") is True
        assert is_allowlisted_path("dual_pipeline/agent_sdk/policies.py") is True

        # Non-exempt path must return False
        assert is_allowlisted_path("dual_pipeline/new_feature.py") is False
        assert is_allowlisted_path(".agents/rules/new_rule.md") is False

    def test_adversarial_payload_formatting(self):
        """Test malicious or malformed options formatting through adapter."""
        evil_question = "Do you accept <script>alert('xss')</script> & 'quotes'?"
        evil_options = [
            {"label": "Option 1", "display_text": "Normal Option with \\n newline"},
            {"label": "Option 2", "other_field": 123},  # Missing display_text/label
            "<xml>malformed</xml>",
        ]

        formatted = format_runtime_question_call(
            question=evil_question,
            options=evil_options,
            is_multi_select=True,
            target_mode="ide",
        )

        assert formatted["tool"] == "ask_question"
        q_entry = formatted["arguments"]["questions"][0]
        assert q_entry["question"] == evil_question
        assert q_entry["is_multi_select"] is True
        assert len(q_entry["options"]) == 3
        assert "Normal Option" in q_entry["options"][0]
        assert "<xml>malformed</xml>" in q_entry["options"][2]
