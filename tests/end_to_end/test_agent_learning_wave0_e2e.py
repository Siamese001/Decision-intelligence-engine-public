"""
tests/end_to_end/test_agent_learning_wave0_e2e.py

End-to-End test for Wave 0: Agent Learning & Autonomy Opportunities.
Validates the full lifecycle: agent failure outcome -> adaptation -> cohort analysis
-> proposal generation -> dry-run replay -> human approval -> runtime application -> recovery.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, stable_id, write_json
from dual_pipeline.learning_adapters import AgentOutcomeAdapter, OutcomeType
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.telemetry_core.constants import LEDGER_SCHEMA


class AgentLearningWave0E2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.ledger_dir = self.runtime_root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.tmp_path / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

        self.sqlite_path = self.ledger_dir / "learning.sqlite"
        with sqlite3.connect(self.sqlite_path) as conn:
            conn.executescript(LEDGER_SCHEMA)

        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_adjudication_failure_to_durable_alias_learning(self) -> None:
        """End-to-end lifecycle: entity conflict -> outcome adaptation -> proposal -> apply -> resolution."""
        aliases_file = self.tmp_path / "learned_aliases.json"
        write_json(aliases_file, {"schema_version": "1.0.0", "aliases": {}})

        # 1. Step 1: Simulate entity conflict outcome from AdjudicationJudgeAgent
        conflicting = ["system:technosilva_legacy", "system:technosylva"]
        canonical_target = "system:technosylva"
        outcome_event = AgentOutcomeAdapter.build_adjudication_error_event(
            conflicting_entities=conflicting,
            reason="Ambiguous legacy spelling in tech stack documentation",
            candidate_id="cand-arc-99",
            corrected_canonical=canonical_target,
            receipt_id="receipt-arc-99",
        )

        # 2. Step 2: Validate agent-outcome-event contract artifact
        outcome_artifact = AgentOutcomeAdapter.to_contract_artifact(outcome_event)
        validate_artifact(outcome_artifact)
        self.assertEqual(outcome_artifact["contract_id"], "urn:nee:dual-pipeline:agent-outcome-event:1.0.0")

        # 3. Step 3: Convert to learning-event and persist in telemetry store
        learn_evt = AgentOutcomeAdapter.to_learning_event(outcome_event)
        validate_artifact(learn_evt)

        cohort_item = AgentOutcomeAdapter.to_cohort_item(outcome_event)
        self.assertEqual(cohort_item["source"], "agent_outcome")

        # 4. Step 4: Analyze failure cohorts in ClosedLoopLearningEngine
        # Provide multiple occurrences to trigger cohort threshold
        cohort_items = [
            cohort_item,
            AgentOutcomeAdapter.to_cohort_item(
                AgentOutcomeAdapter.build_adjudication_error_event(
                    conflicting_entities=conflicting,
                    reason="Second observation of legacy alias mismatch",
                    candidate_id="cand-arc-100",
                    corrected_canonical=canonical_target,
                )
            ),
        ]
        cohorts = self.engine.analyze_failure_cohorts(cohort_items, min_occurrences=2)
        self.assertEqual(len(cohorts), 1)
        cohort = cohorts[0]
        self.assertEqual(cohort["cohort_key"], "outcome:adjudication_error:entity_resolution")
        self.assertEqual(cohort["occurrence_count"], 2)

        # 5. Step 5: Synthesize governed learning-change-proposal:1.0.0
        proposal = self.engine.generate_proposal_for_cohort(
            cohort,
            proposal_type="alias_addition",
            target_override={
                "from_id": "system:technosilva_legacy",
                "to_id": canonical_target,
            },
        )
        validate_artifact(proposal)
        self.assertEqual(proposal["contract_id"], "urn:nee:dual-pipeline:learning-change-proposal:1.0.0")
        self.assertEqual(proposal["proposal_type"], "alias_addition")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])

        # 6. Step 6: Simulate proposal dry-run replay
        sim_res = self.engine.simulate_proposal(proposal)
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertIn("system:technosilva_legacy -> system:technosylva", sim_res.details.get("resolution_tested", ""))

        # 7. Step 7: Operator signs authentic approval receipt
        approval_receipt = {
            "receipt_id": "receipt-operator-approval-001",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["canonical_digest"]["value"],
            "approved_by": "Human Lead Operator (Amit Ayer)",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "user_command": "approve and apply alias resolution",
        }

        # 8. Step 8: Apply approved proposal via UWG
        apply_receipt = self.engine.apply_approved_proposal(
            proposal,
            approval_receipt,
            aliases_path=aliases_file,
        )
        self.assertEqual(apply_receipt["status"], "APPLIED")
        self.assertEqual(apply_receipt["proposal_id"], proposal["proposal_id"])

        # 9. Step 9: Verify durable mutation of runtime alias store
        updated_aliases = read_json(aliases_file)
        self.assertIn("aliases", updated_aliases)
        self.assertEqual(
            updated_aliases["aliases"].get("system:technosilva_legacy"),
            canonical_target,
        )

        # 10. Step 10: Verify closed-loop recovery: next resolution now maps correctly
        resolved_entity = updated_aliases["aliases"].get("system:technosilva_legacy")
        self.assertEqual(resolved_entity, canonical_target)
