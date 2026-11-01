#!/usr/bin/env python3
"""End-to-End test suite for SSOT tool name enforcement and runtime question lifecycle."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from config.hitl_tool_name import ASK_USER_QUESTION
from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    adapt_incoming_tool_result,
    adapt_outgoing_tool_call,
    format_runtime_question_call,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestHitlToolNameSSOTE2E(unittest.TestCase):
    """E2E verification of SSOT tool name compliance, linters, sync tools, and decision lifecycle."""

    def test_repo_wide_linter_zero_violations_e2e(self):
        """Run repo-wide linter tool and verify 0 deprecated tool names across active files."""
        res = subprocess.run(
            [sys.executable, "tools/lint_hitl_tool_name.py", "--scope", "all", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Linter failed with stdout: {res.stdout}\nstderr: {res.stderr}"
        data = json.loads(res.stdout)
        assert data["exit_code"] == 0
        assert data["total_violations"] == 0
        assert data["canonical_tool_name"] == ASK_USER_QUESTION
        assert len(data["findings"]) == 0

    def test_hooks_synchronizer_in_sync_e2e(self):
        """Verify hooks.json is 100% synchronized with the canonical SSOT tool name."""
        res = subprocess.run(
            [sys.executable, "tools/sync_hitl_tool_name.py", "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Hooks out of sync: {res.stdout}"
        assert f"All hook configurations are in sync with SSOT tool name '{ASK_USER_QUESTION}'." in res.stdout

    def test_full_question_lifecycle_through_runtime_adapter_e2e(self):
        """Full lifecycle: Builder -> Validation -> Adapter -> Simulated Run -> Writer -> Ledger."""
        # 1. Build an atomic question using QuestionBuilder
        builder = QuestionBuilder("Which database migration approach should be activated?")
        builder.add_option(
            label="Blue-green rollout",
            plain_english_summary="Deploy new release to standby cluster and switch active traffic.",
            impact_statement="Guarantees zero customer downtime while preserving rollback safety.",
            confidence_score=0.82,
            recommended=True,
        )
        builder.add_option(
            label="Scheduled maintenance",
            plain_english_summary="Perform scheduled maintenance window migration with traffic pause.",
            impact_statement="Reduces customer exposure risk but introduces brief scheduled downtime.",
            confidence_score=0.74,
            recommended=False,
        )

        payload = builder.build()
        assert len(payload.options) == 2

        # 2. Validate with validate_question_payload
        val_result = validate_question_payload(payload.question, payload.options)
        assert val_result.is_valid, f"Validation errors: {val_result.errors}"

        # 3. Adapt outgoing call for IDE runtime
        tool_call = format_runtime_question_call(
            question=payload.question,
            options=payload.display_options,
            is_multi_select=False,
            target_mode="ide",
        )
        target_name, adapted_args = adapt_outgoing_tool_call(
            tool_name=ASK_USER_QUESTION,
            arguments=tool_call["arguments"],
            target_mode="ide",
        )
        assert target_name == "ask_question"
        assert len(adapted_args["questions"]) == 1

        # 4. Simulate operator response in runtime
        simulated_runtime_response = {
            "selected_index": 0,
            "selected_option": payload.display_options[0],
        }

        # 5. Adapt incoming response back to canonical
        normalized_result = adapt_incoming_tool_result(
            runtime_tool_name=target_name,
            result=simulated_runtime_response,
        )
        assert normalized_result["canonical_tool_name"] == ASK_USER_QUESTION
        assert normalized_result["adapted"] is True

        # 6. Record decision persistently via record_operator_answer
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sqlite_path = tmp_path / "hitl_decision_ledger.sqlite"
            json_path = tmp_path / "hitl_decision_ledger.json"

            human_origin = {"source": "USER_INPUT", "text": "I approve the blue-green rollout"}

            res = record_operator_answer(
                decision_id="dec-migration-001",
                question=payload.question,
                options=payload.display_options,
                selected_option=payload.display_options[0],
                operator_origin=human_origin,
                category="migration",
                rationale="Selected zero-downtime path per verified capacity.",
                sqlite_path=sqlite_path,
                json_path=json_path,
            )

            assert res.decision_id == "dec-migration-001"
            assert res.sequence_number == 0
            assert len(res.entry_digest) == 64
            assert res.status == "PERSISTED"

            # Verify persisted files
            assert sqlite_path.exists()
            assert json_path.exists()
            stored = json.loads(json_path.read_text(encoding="utf-8"))
            decisions = stored.get("operator_decisions", [])
            assert len(decisions) == 1
            assert decisions[0]["decision_id"] == "dec-migration-001"

