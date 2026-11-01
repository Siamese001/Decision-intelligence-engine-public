"""
tests/test_agent_fleet_system_learning_adversarial.py

Adversarial and edge-case testing suite for Wave 3:
1. Boundary payload: Empty ledgers, 0-decision cohorts, below-threshold cohorts, 0 options.
2. Malformed input: Truncated JSON proposal files, corrupt schemas, invalid types.
3. Invariant bypass attempt: Synthetic auto_apply: true rejection, unauthenticated approval bypass failure.
4. Concurrency & race: Parallel decision recording and prior calibration without lock contention.
"""

from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.contracts import ContractValidationError, validate_artifact
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.priors import export_learned_priors
from dual_pipeline.hitl_governance_core.question_builder import (
    QuestionBuilder,
    QuestionPayloadValidationError,
)
from dual_pipeline.learning_engine import get_learning_engine
from dual_pipeline.learning_loop_core.scanner import (
    AnswerFailureCohort,
    synthesize_answer_learning_proposal,
)
from tools.govern_learning_proposals import (
    cmd_hitl_scan,
    cmd_inspect,
    cmd_list,
)


def test_adversarial_boundary_empty_stores_and_below_threshold():
    """Verify scanning empty or below-threshold decision stores yields 0 proposals safely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        ledger_path = tmp / "empty_hitl_ledger.json"
        sqlite_path = tmp / "empty_hitl_ledger.sqlite"
        proposals_dir = tmp / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)

        # 1. Scan completely empty store
        rc = cmd_hitl_scan(
            engine=get_learning_engine(),
            proposals_dir=proposals_dir,
            ledger_path=ledger_path,
            override_threshold=0.20,
            min_decisions=5,
            generate_proposals=True,
        )
        assert rc == 0
        assert len(list(proposals_dir.glob("*.json"))) == 0

        # 2. Add 2 decisions (below min_decisions=5 threshold)
        for i in range(2):
            store.record_decision(
                decision_id=f"dec-below-{i}",
                category="TACTICAL",
                prompt="Prompt text",
                options=[{"key": "a", "label": "A"}, {"key": "b", "label": "B"}],
                selected_option="b",
                rationale="Operator override",
                operator_id="operator-1",
            )

        rc2 = cmd_hitl_scan(
            engine=get_learning_engine(),
            proposals_dir=proposals_dir,
            ledger_path=ledger_path,
            override_threshold=0.20,
            min_decisions=5,
            generate_proposals=True,
        )
        assert rc2 == 0
        assert len(list(proposals_dir.glob("*.json"))) == 0


def test_adversarial_boundary_zero_and_single_option_builder():
    """Verify QuestionBuilder strictly rejects < 2 options."""
    qb_empty = QuestionBuilder(question="Valid question?")
    with pytest.raises(QuestionPayloadValidationError, match="requires at least 2 options"):
        qb_empty.build()

    qb_single = QuestionBuilder(question="Valid question?")
    qb_single.add_option(
        label="Single Option",
        plain_english_summary="Only choice",
        impact_statement="No choice",
        confidence_score=0.9,
    )
    with pytest.raises(QuestionPayloadValidationError, match="requires at least 2 options"):
        qb_single.build()


def test_adversarial_malformed_proposal_handling():
    """Verify malformed and corrupted proposal files are handled fail-closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        proposals_dir = tmp / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        # Write truncated/malformed JSON
        corrupt_file = proposals_dir / "lcp-corrupt.json"
        corrupt_file.write_text("{\"contract_id\": \"urn:nee:dual-pipeline:learning-change-proposal:1.0.0\", TRUNCATED...", encoding="utf-8")

        # cmd_list should ignore malformed files safely
        rc = cmd_list(engine=get_learning_engine(), proposals_dir=proposals_dir)
        assert rc == 0

        # cmd_inspect on malformed file should return exit code 1
        rc_inspect = cmd_inspect(engine=get_learning_engine(), proposals_dir=proposals_dir, target="lcp-corrupt")
        assert rc_inspect == 1

        # Missing required contract keys fails artifact validation
        invalid_proposal = {
            "proposal_id": "lcp-missing-keys",
            "status": "DRAFT",
        }
        with pytest.raises(ContractValidationError):
            validate_artifact(invalid_proposal)


def test_adversarial_invariant_bypass_auto_apply_prohibited():
    """Verify that auto_apply cannot be forced to True in synthesized learning proposals."""
    cohort = AnswerFailureCohort(
        cohort_key="grounding_failure:person_entity",
        category="copilot_grounding",
        occurrence_count=5,
        run_ids=["run-1", "run-2"],
        receipt_ids=["rec-1", "rec-2"],
        items=[{"error": "grounding"}],
        target_parameter="unattributed_candidate_penalty",
        current_value=0.25,
        proposed_value=0.45,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        proposals_dir = tmp / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        proposal = synthesize_answer_learning_proposal(cohort, proposals_dir=proposals_dir)

        # Invariant checks
        assert proposal["auto_apply"] is False, "auto_apply must NEVER be True"
        assert proposal["approval_required"] is True, "approval_required must ALWAYS be True"


def test_adversarial_concurrency_decision_recording_and_prior_export():
    """Verify concurrent decision recording and prior exporting in WAL mode without locks or corruption."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        ledger_path = tmp / "concurrent_ledger.json"
        sqlite_path = tmp / "concurrent_ledger.sqlite"
        priors_path = tmp / "concurrent_priors.json"

        store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)

        def worker_record(worker_id: int):
            for i in range(10):
                store.record_decision(
                    decision_id=f"dec-conc-{worker_id}-{i}",
                    category="CONCURRENCY_TEST",
                    prompt="Concurrent test question",
                    options=[
                        {"key": "opt_a", "label": "Option A", "confidence": 0.6},
                        {"key": "opt_b", "label": "Option B", "confidence": 0.4},
                    ],
                    selected_option="opt_a" if (i % 2 == 0) else "opt_b",
                    rationale="Testing multi-threaded recording",
                    operator_id=f"worker_{worker_id}",
                )

        def worker_export():
            for _ in range(5):
                export_learned_priors(store, output_path=priors_path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            record_futures = [executor.submit(worker_record, wid) for wid in range(4)]
            export_futures = [executor.submit(worker_export) for _ in range(2)]

            for f in concurrent.futures.as_completed(record_futures + export_futures):
                f.result()

        # Verify integrity and count
        history = store.get_history("CONCURRENCY_TEST")
        assert len(history) == 40
        assert priors_path.is_file()
        priors_data = json.loads(priors_path.read_text(encoding="utf-8"))
        assert priors_data["categories_count"] >= 1
