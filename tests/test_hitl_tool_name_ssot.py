#!/usr/bin/env python3
"""Unit tests for SSOT tool name (`ask_user_question`), runtime adapter, and hook synchronizer."""

import json
import unittest
import warnings

import pytest

from config.hitl_tool_name import (
    ASK_USER_QUESTION,
    CANONICAL_TOOL_NAMES,
    DEPRECATED_NAMES,
    canonicalize_tool_name,
    get_tool_name_deprecation_message,
    is_canonical_tool_name,
    is_deprecated_tool_name,
    validate_tool_name,
)
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    CANONICAL_TOOL_NAME,
    IDE_RUNTIME_TOOL_NAME,
    adapt_incoming_tool_result,
    adapt_outgoing_tool_call,
    format_runtime_question_call,
    get_adapter_audit_record,
    get_runtime_target_tool_name,
)
from tools.sync_hitl_tool_name import sync_hook_dict


class TestHitlToolNameSSOT(unittest.TestCase):
    """Test suite for SSOT tool name definitions and helper utilities."""

    def test_canonical_constant(self):
        self.assertEqual(ASK_USER_QUESTION, "ask_user_question")
        self.assertEqual(CANONICAL_TOOL_NAMES, frozenset({"ask_user_question"}))
        self.assertEqual(CANONICAL_TOOL_NAME, "ask_user_question")

    def test_deprecated_names_containment(self):
        expected_deprecated = {
            "ask_question",
            "ask_user",
            "ask_human",
            "prompt_user",
            "ask_operator",
            "request_human_input",
        }
        for name in expected_deprecated:
            assert name in DEPRECATED_NAMES

    def test_is_canonical_tool_name(self):
        assert is_canonical_tool_name("ask_user_question") is True
        assert is_canonical_tool_name("  ask_user_question  ") is True
        assert is_canonical_tool_name("ask_question") is False
        assert is_canonical_tool_name("ask_user") is False
        assert is_canonical_tool_name("") is False
        assert is_canonical_tool_name(None) is False
        assert is_canonical_tool_name(123) is False

    def test_is_deprecated_tool_name(self):
        assert is_deprecated_tool_name("ask_question") is True
        assert is_deprecated_tool_name("ASK_QUESTION") is True
        assert is_deprecated_tool_name("ask_user") is True
        assert is_deprecated_tool_name("ask_human") is True
        assert is_deprecated_tool_name("ask_user_question") is False
        assert is_deprecated_tool_name("some_random_tool") is False
        assert is_deprecated_tool_name("") is False
        assert is_deprecated_tool_name(None) is False

    def test_validate_tool_name(self):
        # Should not raise for canonical
        validate_tool_name("ask_user_question")

        # Must raise ValueError for deprecated or invalid names
        with pytest.raises(ValueError, match="Non-canonical tool name 'ask_question'"):
            validate_tool_name("ask_question")
        with pytest.raises(ValueError, match="Non-canonical tool name 'ask_user'"):
            validate_tool_name("ask_user")
        with pytest.raises(ValueError, match="Non-canonical tool name 'unknown'"):
            validate_tool_name("unknown")

    def test_canonicalize_tool_name(self):
        # Canonical pass-through without warnings
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            canonical = canonicalize_tool_name("ask_user_question")
            assert canonical == "ask_user_question"
            assert len(recorded) == 0

        # Deprecated mapping with DeprecationWarning
        with pytest.warns(DeprecationWarning, match="Tool name 'ask_question' is deprecated"):
            canonical = canonicalize_tool_name("ask_question", emit_warning=True)
            assert canonical == "ask_user_question"

        # Deprecated mapping with warning suppressed
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            canonical = canonicalize_tool_name("ask_user", emit_warning=False)
            assert canonical == "ask_user_question"
            assert len(recorded) == 0

        # Invalid type
        with pytest.raises(TypeError, match="Tool name must be str"):
            canonicalize_tool_name(123)  # type: ignore

        # Unknown name
        with pytest.raises(ValueError, match="Invalid tool name 'unknown_tool'"):
            canonicalize_tool_name("unknown_tool")

    def test_get_tool_name_deprecation_message(self):
        msg = get_tool_name_deprecation_message("ask_question")
        assert "ask_question" in msg
        assert "ask_user_question" in msg


class TestRuntimeToolAdapter(unittest.TestCase):
    """Test suite for Decision D7 runtime tool adapter."""

    def test_runtime_targets(self):
        assert IDE_RUNTIME_TOOL_NAME == "ask_question"
        assert get_runtime_target_tool_name("ide") == "ask_question"
        assert get_runtime_target_tool_name("native") == "ask_user_question"
        assert get_runtime_target_tool_name("custom") == "ask_user_question"

    def test_adapt_outgoing_tool_call_ide(self):
        args = {"questions": [{"question": "Proceed?", "options": ["Yes", "No"]}]}
        target_name, adapted_args = adapt_outgoing_tool_call(
            "ask_user_question", args, target_mode="ide"
        )
        assert target_name == "ask_question"
        assert adapted_args == args

    def test_adapt_outgoing_tool_call_native(self):
        args = {"questions": [{"question": "Proceed?", "options": ["Yes", "No"]}]}
        target_name, adapted_args = adapt_outgoing_tool_call(
            "ask_user_question", args, target_mode="native"
        )
        assert target_name == "ask_user_question"
        assert adapted_args == args

    def test_adapt_outgoing_unrelated_tool(self):
        args = {"path": "test.txt"}
        target_name, adapted_args = adapt_outgoing_tool_call(
            "view_file", args, target_mode="ide"
        )
        assert target_name == "view_file"
        assert adapted_args == args

    def test_adapt_incoming_tool_result(self):
        raw_result = {"selected_option": "Option A"}
        res = adapt_incoming_tool_result("ask_question", raw_result)
        assert res["canonical_tool_name"] == "ask_user_question"
        assert res["runtime_tool_name"] == "ask_question"
        assert res["adapted"] is True
        assert res["result"] == raw_result
        assert "Decision D7" in res["decision_d7_audit"]

        # Native incoming
        res_native = adapt_incoming_tool_result("ask_user_question", raw_result)
        assert res_native["adapted"] is False
        assert res_native["decision_d7_audit"] is None

    def test_format_runtime_question_call(self):
        call = format_runtime_question_call(
            question="Which database strategy?",
            options=[
                "(Recommended) Option A",
                {"label": "Option B", "display_text": "(Confidence: 40%) Option B"},
            ],
            is_multi_select=False,
            target_mode="ide",
        )
        assert call["tool"] == "ask_question"
        assert len(call["arguments"]["questions"]) == 1
        q_entry = call["arguments"]["questions"][0]
        assert q_entry["question"] == "Which database strategy?"
        assert q_entry["options"] == [
            "(Recommended) Option A",
            "(Confidence: 40%) Option B",
        ]
        assert q_entry["is_multi_select"] is False

    def test_get_adapter_audit_record(self):
        rec = get_adapter_audit_record()
        assert rec["canonical_name"] == "ask_user_question"
        assert rec["runtime_target_name"] == "ask_question"
        assert rec["governing_decision"] == "D7"
        assert rec["margin_delta"] == 0.35
        assert rec["resolution_type"] == "AUTONOMOUS"
        assert rec["status"] == "ACTIVE"


class TestSyncHitlToolName(unittest.TestCase):
    """Test suite for hook synchronization tool."""

    def test_sync_hook_dict_detects_and_fixes(self):
        hook_sample = {
            "id": "operator_approval_hook",
            "description": "Intercepts ask_question executions.",
            "instructions": "Ensure ask_user calls are logged.",
            "match": {
                "tool": "ask_question",
                "also_match": [{"tool": "ask_user"}],
                "command_patterns": ["ask_question", "ask_user", "git commit"],
            },
        }

        # Check mode: reports issues without changing dict
        changed, issues = sync_hook_dict(dict(hook_sample), fix=False)
        assert len(issues) >= 4
        assert changed is False

        # Fix mode: modifies dict in place
        sample_copy = json.loads(json.dumps(hook_sample))
        changed, issues = sync_hook_dict(sample_copy, fix=True)
        assert changed is True
        assert sample_copy["match"]["tool"] == "ask_user_question"
        assert sample_copy["match"]["also_match"][0]["tool"] == "ask_user_question"
        assert "ask_user_question" in sample_copy["match"]["command_patterns"]
        assert "ask_question" not in sample_copy["match"]["command_patterns"]
        assert "ask_user" not in sample_copy["match"]["command_patterns"]
        assert "ask_user_question" in sample_copy["description"]
        assert "ask_user_question" in sample_copy["instructions"]
