"""
tests/property/test_hypothesis_pipeline_properties.py

Hypothesis Property-Based Testing Suite for Core Pipeline Invariants.
Validates boundary cases, malformed payloads, and overflow conditions across:
1. Subagent Context Profile Token Ceilings & Overflow Truncation.
2. Token Accounting Non-Negativity & Financial Ledger Invariants.
3. HITL Pre-Surfacing Margin Bounds & Autonomous Decision Boundaries.
4. Malformed Storage Rows & Safe Key Extraction.
5. Canonical Digest Determinism & Idempotency.

Compatible with both pytest and unittest test discovery runners.
Governed under Finding X-32 and Decision H-8 (Wave 13).
"""

from __future__ import annotations

import string
import unittest
from typing import Any, Dict, List
import hypothesis.strategies as st
from hypothesis import given, settings

from dual_pipeline.agent_architecture_core.context_profile import (
    MAX_SUBAGENT_CONTEXT_TOKENS,
    SubagentContextProfile,
    estimate_token_count,
)
from dual_pipeline.agent_architecture_core.delegation_ledger import (
    DelegationCostLedger,
)
from dual_pipeline.core import digest
from dual_pipeline.hitl_governance_core.pre_surfacing import (
    evaluate_pre_surfacing_gate,
)
from dual_pipeline.storage.hitl_sqlite_store import (
    _extract_sel_key,
    _record_to_row,
)

task_id_strategy = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=32,
)


class TestHypothesisPipelineProperties(unittest.TestCase):
    """Property-based generative test suite verifying core system invariants."""

    # ── Property 1: Subagent Context Profile Overflow & Ceiling Invariant ─────────

    @settings(max_examples=50, deadline=None)
    @given(
        task_id=task_id_strategy,
        objective=st.text(min_size=1, max_size=500),
        context=st.text(min_size=0, max_size=30000),  # Potential massive overflow
        max_tokens=st.integers(min_value=300, max_value=MAX_SUBAGENT_CONTEXT_TOKENS),
    )
    def test_subagent_context_profile_never_exceeds_token_budget(
        self, task_id: str, objective: str, context: str, max_tokens: int
    ) -> None:
        """Regardless of massive context overflow, compiled context strictly respects token budget."""
        contract = {
            "task_id": task_id,
            "parent_task_id": "parent-task-1",
            "objective": objective,
            "definition_of_done": "Complete verification",
            "allowed_tools": ["tool_a", "tool_b"],
        }

        profile = SubagentContextProfile.from_contract(
            contract, delegated_context=context, max_context_tokens=max_tokens
        )
        compiled = profile.compile_context()
        actual_tokens = estimate_token_count(compiled)

        self.assertLessEqual(
            actual_tokens,
            max_tokens,
            f"Token overflow: {actual_tokens} exceeds max budget {max_tokens}",
        )
        self.assertIn(task_id, compiled)
        self.assertIn("## Objective", compiled)

    # ── Property 2: Token Accounting Non-Negativity & Bounds ─────────────────────

    @settings(max_examples=50, deadline=None)
    @given(
        prompt_tokens=st.integers(min_value=-1000, max_value=1000000),
        cached_tokens=st.integers(min_value=-1000, max_value=1000000),
        completion_tokens=st.integers(min_value=-1000, max_value=1000000),
        cost_usd=st.floats(min_value=-100.0, max_value=10000.0, allow_nan=False),
    )
    def test_delegation_ledger_token_accounting_properties(
        self, prompt_tokens: int, cached_tokens: int, completion_tokens: int, cost_usd: float
    ) -> None:
        """Ledger sanitizes inputs, clamps negative values to 0, and guarantees valid ratios."""
        ledger = DelegationCostLedger()
        rec = ledger.record_delegation(
            task_id="task-prop-1",
            parent_task_id="task-parent-1",
            subagent_role="property_tester",
            model="claude-sonnet-4-6",
            prompt_tokens=prompt_tokens,
            cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )

        self.assertGreaterEqual(rec.prompt_tokens, 0)
        self.assertGreaterEqual(rec.cached_tokens, 0)
        self.assertGreaterEqual(rec.completion_tokens, 0)
        self.assertGreaterEqual(rec.cost_usd, 0.0)

        if rec.prompt_tokens > 0:
            expected_ratio = rec.cached_tokens / rec.prompt_tokens
            self.assertGreaterEqual(rec.cached_token_share, 0.0)
            self.assertLessEqual(rec.cached_token_share, max(1.0, expected_ratio) + 1e-4)
        else:
            self.assertEqual(rec.cached_token_share, 0.0)

    # ── Property 3: HITL Pre-Surfacing Autonomous Decision Margin Boundaries ─────

    @settings(max_examples=50, deadline=None)
    @given(
        conf1=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        conf2=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    def test_hitl_pre_surfacing_margin_boundary_properties(
        self, conf1: float, conf2: float
    ) -> None:
        """Pre-surfacing gate accurately evaluates confidence margins and produces valid receipts."""
        options = [
            {"key": "opt_a", "label": "Option A", "confidence": conf1},
            {"key": "opt_b", "label": "Option B", "confidence": conf2},
        ]

        res = evaluate_pre_surfacing_gate(
            options=options,
            decision_id="prop-dec-test",
            category="testing_protocol",
            margin_threshold=0.15,
        )

        receipt = res.get("audit_receipt", {})
        self.assertIn("receipt_digest", receipt)
        self.assertIn("resolved_autonomously", receipt)
        self.assertIn("should_surface", receipt)
        self.assertIsNotNone(res.get("margin"))
        self.assertTrue(0.0 <= float(res["margin"]) <= 1.0)

    # ── Property 4: Malformed Storage Payloads & Key Extraction ───────────────────

    @settings(max_examples=50, deadline=None)
    @given(
        sel_opt=st.one_of(
            st.text(),
            st.integers(),
            st.booleans(),
            st.dictionaries(st.text(max_size=10), st.text(max_size=20), max_size=5),
            st.none(),
        ),
        meta=st.dictionaries(st.text(max_size=10), st.text(max_size=20), max_size=5),
    )
    def test_malformed_storage_row_safe_conversion(
        self, sel_opt: Any, meta: Dict[str, Any]
    ) -> None:
        """Storage serializers safely handle malformed or arbitrary types without exceptions."""
        rec = {
            "decision_id": "prop-malformed-test",
            "category": "malformed_testing",
            "selected_option": sel_opt,
            "metadata": meta,
        }

        key = _extract_sel_key(sel_opt, rec)
        self.assertIsInstance(key, str)

        row = _record_to_row(rec)
        self.assertIsInstance(row, tuple)
        self.assertEqual(len(row), 14)  # 14-column SQLite schema
        self.assertIsInstance(row[0], str)  # entry_id
        self.assertIsInstance(row[8], str)  # selected_key
        self.assertIsInstance(row[13], str)  # entry_digest

    # ── Property 5: Canonical Digest Determinism & Idempotency ────────────────────

    @settings(max_examples=50, deadline=None)
    @given(
        data=st.dictionaries(
            st.text(min_size=1, max_size=20),
            st.one_of(st.text(), st.integers(), st.booleans(), st.lists(st.integers())),
            max_size=10,
        )
    )
    def test_digest_determinism_and_hex_length(self, data: Dict[str, Any]) -> None:
        """RFC-8259 canonical digest is strictly deterministic, non-empty, and valid sha256."""
        d1 = digest(data)
        d2 = digest(data)

        self.assertEqual(d1, d2, "Digest must be idempotent and deterministic")
        self.assertEqual(len(d1), 64, "SHA-256 digest must be 64 hex characters")
        self.assertTrue(all(c in "0123456789abcdef" for c in d1))


if __name__ == "__main__":
    unittest.main()
