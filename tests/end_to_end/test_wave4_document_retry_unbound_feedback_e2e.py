"""
tests/end_to_end/test_wave4_document_retry_unbound_feedback_e2e.py

Wave 4 End-to-End Test Suite: Retry Loop That Can Improve.
Covers:
1. Exact production regression: Feedback naming no object ID triggers full re-extraction
   with judge feedback, producing a non-identical second candidate (Attempt 2 != Attempt 1).
2. Boundary payload & recovery: Invalid bounded patch response falls back to full regeneration
   with remediation directive rather than halting with an unrecoverable failure.
3. Rollback & recovery: Identical candidate artifacts trigger full regeneration fallback
   in the synthesis loop.
4. Prompt contract alignment: Document extractor schema accepts and preserves both exact_text
   and text in evidence_index.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import unittest

import pytest

from dual_pipeline.canonical_pipeline.document_remediation import (
    prepare_document_patch_or_handoff,
    synthesize_with_document_handoff,
)
from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    compute_candidate_digest,
    handle_identical_candidate_fallback,
)
from dual_pipeline.document_pipeline_core.bounded_patch import (
    InvalidBoundedPatchResponseError,
    UnboundPatchScopeError,
)
from dual_pipeline.document_pipeline_core.executor import (
    DocumentGraphExtractorExecutor,
    _post_process_document_projection,
)
from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY


class MockSynthesisProvider:
    """Mock synthesis provider capturing prompts and returning sequential candidates."""

    def __init__(self, responses: List[str]):
        self.responses = list(responses)
        self.call_history: List[Dict[str, str]] = []

    def synthesize_graph(self, prompt: str, user_content: str) -> str:
        self.call_history.append({"prompt": prompt, "user_content": user_content})
        if not self.responses:
            raise RuntimeError("MockSynthesisProvider has no more responses configured")
        return self.responses.pop(0)


class MockStrategy:
    """Mock synthesis strategy wrapping MockSynthesisProvider."""

    def __init__(self, responses: List[str]):
        self.provider = MockSynthesisProvider(responses)
        self.executor = DocumentGraphExtractorExecutor(self.provider)

    def synthesize(
        self,
        source_text: str,
        pa_context: Dict[str, Any],
        metadata: Dict[str, Any],
        attempt: int = 1,
        remediation_directive: Optional[str] = None,
    ) -> Any:
        raw = self.executor.execute(
            document_text=source_text,
            pa_context=pa_context,
            remediation_directive=remediation_directive,
            attempt=attempt,
        )
        return _MockSynthesisResult(output_3=raw)


class _MockSynthesisResult:
    def __init__(self, output_1: str = "", output_2: str = "", output_3: str = ""):
        self.output_1 = output_1
        self.output_2 = output_2
        self.output_3 = output_3


def _base_projection(entities: List[str], text_span: str) -> Dict[str, Any]:
    rel_signals = (
        [
            {
                "signal_id": f"rel-{entities[0].lower().replace(' ', '-')}-{entities[i].lower().replace(' ', '-')}",
                "from_id": f"org:{entities[0].lower().replace(' ', '-')}",
                "to_id": f"org:{entities[i].lower().replace(' ', '-')}",
                "relationship": "OWNS",
                "mutation": "CREATE",
                "evidence_ids": ["ev-01"],
            }
            for i in range(1, len(entities))
        ]
        if len(entities) > 1
        else [
            {
                "signal_id": f"rel-{entities[0].lower().replace(' ', '-')}-system",
                "from_id": f"org:{entities[0].lower().replace(' ', '-')}",
                "to_id": "system:power-grid",
                "relationship": "OPERATES",
                "mutation": "CREATE",
                "evidence_ids": ["ev-01"],
            }
        ]
    )
    return {
        "evidence_index": {
            "ev-01": {
                "text": text_span,
                "exact_text": text_span,
                "source_classification": "FACT",
                "locator": {"kind": "section", "value": "Overview"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": f"org:{name.lower().replace(' ', '-')}",
                    "type": "org",
                    "name": name,
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-01"],
                }
                for name in entities
            ],
            "relationship_signals": rel_signals,
            "decision_updates": [],
            "proposal_updates": [],
            "risk_updates": [],
            "action_updates": [],
            "strategic_intent_signals": [],
        },
    }


class TestWave4DocumentRetryUnboundFeedbackE2E(unittest.TestCase):
    """E2E suite verifying retry progression under unbound and invalid patch conditions."""

    def setUp(self) -> None:
        self.source_text = (
            "Apex Global Enterprises announced strong Q1 performance. "
            "Apex Power & Grid expanded transmission investments and battery storage capacity. "
            "Commerce selected Energy Resources to construct dedicated clean generation facilities."
        )

    def test_unbound_judge_feedback_triggers_full_reextraction_producing_different_candidate(self) -> None:
        """Exact production regression: Judge feedback naming no objects produces a different attempt 2 candidate."""
        cand1 = _base_projection(["Apex Global Enterprises"], "Apex Global Enterprises announced strong Q1 performance.")
        cand2 = _base_projection(
            ["Apex Global Enterprises", "Apex Power & Grid"],
            "Apex Power & Grid expanded transmission investments and battery storage capacity.",
        )

        provider = MockSynthesisProvider([json.dumps(cand1), json.dumps(cand2)])
        executor = DocumentGraphExtractorExecutor(provider)

        meta = {"is_document": True, "project": "project_atlas"}
        pa_ctx = {"metadata": meta}

        # Attempt 1: Initial extraction
        out1 = executor.execute(self.source_text, pa_context=pa_ctx, attempt=1)
        hash1 = hashlib.sha256(out1.encode("utf-8")).hexdigest()

        # Judge evaluation rejects with unbound feedback (names no specific object ID)
        judge_feedback = (
            "Add grounded strategic intent signals for Apex Power & Grid transmission growth "
            "and battery storage capacity expansion."
        )

        # Attempt 2: Bounded patch request is None (unbound feedback), directive passed
        out2 = executor.execute(
            self.source_text,
            pa_context=pa_ctx,
            attempt=2,
            remediation_directive=judge_feedback,
            document_patch_request=None,
            prior_graph_candidate=out1,
        )
        hash2 = hashlib.sha256(out2.encode("utf-8")).hexdigest()

        # Assert Attempt 2 is not byte-identical to Attempt 1
        self.assertNotEqual(hash1, hash2, "Attempt 2 must not be byte-identical to Attempt 1")

        # Verify remediation directive was injected into prompt user content for Attempt 2
        self.assertEqual(len(provider.call_history), 2)
        second_call = provider.call_history[1]
        self.assertIn("<remediation_directive>", second_call["user_content"])
        self.assertIn("battery storage capacity expansion", second_call["user_content"])

        # Verify parsed data in Attempt 2 contains the new entity
        parsed2 = json.loads(out2)
        names2 = [e["name"] for e in parsed2["graph_mutations"]["entity_upserts"]]
        self.assertTrue(any("Florida Power" in n for n in names2), f"Florida Power entity missing in {names2}")

    def test_prepare_document_patch_handles_unbound_scope_without_halting(self) -> None:
        """Handoff helper records review audit but returns True with None request for full re-extraction."""
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="test_unbound_handoff_"))

        cand = json.dumps(_base_projection(["Apex Global Enterprises"], "Apex Global Enterprises announced strong Q1 performance."))
        judge_report = {
            "status": "FAIL",
            "evaluation_receipt_digest": "judge-receipt-sha256",
            "output_3_evaluation": {
                "verdict": "FAIL",
                "composite_score": 75,
                "actionable_remediation_diff": "Omitted transmission growth and battery storage.",
            },
        }

        meta: Dict[str, Any] = {"is_document": True}
        can_proceed = prepare_document_patch_or_handoff(
            metadata=meta,
            candidate=cand,
            judge_report=judge_report,
            source_text=self.source_text,
            directive="Add transmission growth and battery storage.",
            output_dir=tmp_dir,
            attempt=1,
        )

        self.assertTrue(can_proceed, "prepare_document_patch_or_handoff must return True to allow re-extraction")
        self.assertIsNone(meta.get("document_patch_request"), "document_patch_request must be None for unbound feedback")

        handoff_file = tmp_dir / "attempts" / "attempt_1" / "document_remediation_handoff.json"
        self.assertTrue(handoff_file.is_file(), "Audit handoff file must be written to disk")
        handoff_data = json.loads(handoff_file.read_text(encoding="utf-8"))
        self.assertEqual(handoff_data["reason_code"], "UNBOUND_DOCUMENT_PATCH_SCOPE")
        self.assertEqual(handoff_data["status"], "REVIEW_REQUIRED")

    def test_invalid_bounded_patch_falls_back_to_full_regeneration(self) -> None:
        """Boundary payload & recovery: Invalid bounded patch response falls back to full regeneration."""
        cand1 = _base_projection(["Apex Global Enterprises"], "Apex Global Enterprises announced strong Q1 performance.")
        cand2 = _base_projection(["Apex Global Enterprises", "Commerce"], "Commerce selected Energy Resources")

        class FailingPatchStrategy:
            def __init__(self, fallback_cand: Dict[str, Any]):
                self.calls = 0
                self.fallback_cand = fallback_cand

            def synthesize(self, **kwargs: Any) -> _MockSynthesisResult:
                self.calls += 1
                if kwargs.get("metadata", {}).get("document_patch_request"):
                    raise InvalidBoundedPatchResponseError("Patch source coverage digest mismatch")
                return _MockSynthesisResult(output_3=json.dumps(self.fallback_cand))

        strategy = FailingPatchStrategy(cand2)
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="test_patch_fallback_"))

        prior_synth = _MockSynthesisResult(output_3=json.dumps(cand1))
        class MockEval:
            judge_report = {"evaluation_receipt_digest": "prior-judge-digest", "output_3_evaluation": {"verdict": "FAIL", "composite_score": 70}}

        meta = {"is_document": True, "document_patch_request": {"operation": "PATCH_FAILED_EXISTING_NODES"}}
        pa_ctx = {"document_patch_request": {"operation": "PATCH_FAILED_EXISTING_NODES"}}

        res = synthesize_with_document_handoff(
            strategy=strategy,
            source_text=self.source_text,
            pa_context=pa_ctx,
            metadata=meta,
            attempt=2,
            directive="Fix grounding mismatch",
            prior_synthesis=prior_synth,
            prior_evaluation=MockEval(),
            output_dir=tmp_dir,
            is_document=True,
        )

        self.assertIsNotNone(res, "Fallback to full regeneration must return valid synthesis result")
        self.assertIsNone(meta.get("document_patch_request"), "document_patch_request must be cleared on fallback")
        self.assertEqual(strategy.calls, 2, "Strategy must be called twice: once failed patch, once fallback")

    def test_identical_candidate_fallback_regenerates_before_terminating(self) -> None:
        """Rollback & recovery: Identical candidate detection triggers one full regeneration before aborting."""
        cand1 = _base_projection(["Apex Global Enterprises"], "Apex Global Enterprises announced strong Q1 performance.")
        cand2 = _base_projection(["Apex Global Enterprises", "APEX"], "Apex Power & Grid expanded transmission investments")

        class StubStrategy:
            def __init__(self, regenerated_cand: Dict[str, Any]):
                self.regenerated_cand = regenerated_cand
                self.called = False

            def synthesize(self, **kwargs: Any) -> _MockSynthesisResult:
                self.called = True
                return _MockSynthesisResult(output_3=json.dumps(self.regenerated_cand))

        synth1 = _MockSynthesisResult(output_3=json.dumps(cand1))
        digest1 = compute_candidate_digest(synth1)

        strategy = StubStrategy(cand2)
        meta: Dict[str, Any] = {}
        pa_ctx: Dict[str, Any] = {}

        can_continue, new_result, new_digest = handle_identical_candidate_fallback(
            attempt=2,
            max_attempts=3,
            metadata=meta,
            pa_context=pa_ctx,
            synthesis_strategy=strategy,
            source_text=self.source_text,
            current_directive="Add APEX entity",
            prior_digest=digest1,
        )

        self.assertTrue(can_continue, "Fallback must allow loop to continue when regenerated output differs")
        self.assertTrue(strategy.called, "Full regeneration must be invoked")
        self.assertNotEqual(new_digest, digest1, "New digest must differ from prior identical digest")
        self.assertTrue(meta.get("_did_full_regeneration_fallback"), "Fallback flag must be set")

    def test_prompt_contract_aligns_text_and_exact_text_in_evidence_index(self) -> None:
        """Prompt contract alignment: Document extractor prompt explicitly defines exact_text and text."""
        prompt_content = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_DOCUMENT_GRAPH")
        self.assertIn("exact_text", prompt_content)
        self.assertIn('"exact_text":', prompt_content)
        self.assertIn('"text":', prompt_content)


if __name__ == "__main__":
    unittest.main()
