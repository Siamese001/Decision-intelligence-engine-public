"""Unit tests for Phase 8 — Calibration Pipeline."""
from __future__ import annotations

import unittest

from dual_pipeline.calibration import (
    apply_calibrated_parameter,
    create_calibration_proposal,
    verify_calibration_approval,
)
from dual_pipeline.contracts import validate_artifact


class TestCalibrationPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = create_calibration_proposal(
            proposal_type="retrieval_weight",
            target_parameter="colbert_weight",
            current_value=0.4,
            proposed_value=0.55,
            problem_description="ColBERT recall degradation observed on multi-hop technical queries.",
            run_ids=["run-eval-001", "run-eval-002"],
            evidence_event_ids=["ev-event-001"],
            evaluation_plan=["Run QREL benchmark suite", "Verify recall@20 >= 0.85"],
            risk="medium",
        )

    def test_proposal_contract_conformance(self) -> None:
        # Must validate against learning-change-proposal contract schema
        validate_artifact(self.proposal)
        self.assertEqual(
            self.proposal["contract_id"],
            "urn:nee:dual-pipeline:learning-change-proposal:1.0.0",
        )

    def test_invariants_enforced(self) -> None:
        self.assertEqual(self.proposal["status"], "proposed")
        self.assertTrue(self.proposal["approval_required"])
        self.assertFalse(self.proposal["auto_apply"])
        self.assertIn("canonical_digest", self.proposal)

    def test_apply_without_approval_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            apply_calibrated_parameter(self.proposal, None)

    def test_apply_with_mismatched_digest_rejected(self) -> None:
        bogus_receipt = {
            "receipt_id": "rcpt-001",
            "proposal_id": self.proposal["proposal_id"],
            "proposal_digest": "0" * 64,
            "approved_by": "operator-alice",
            "approved_at": "2026-09-10T14:00:00Z",
        }
        with self.assertRaises(PermissionError):
            apply_calibrated_parameter(self.proposal, bogus_receipt)

    def test_apply_with_valid_approval_succeeds(self) -> None:
        valid_receipt = {
            "receipt_id": "rcpt-001",
            "proposal_id": self.proposal["proposal_id"],
            "proposal_digest": self.proposal["canonical_digest"]["value"],
            "approved_by": "operator-alice",
            "approved_at": "2026-09-10T14:00:00Z",
        }
        self.assertTrue(verify_calibration_approval(self.proposal, valid_receipt))
        applied = apply_calibrated_parameter(self.proposal, valid_receipt, persist=False)
        self.assertEqual(applied["parameter"], "colbert_weight")
        self.assertEqual(applied["applied_value"], 0.55)
        self.assertEqual(applied["previous_value"], 0.4)
        self.assertEqual(applied["approval_receipt_id"], "rcpt-001")

    def test_apply_parameter_persistence_and_reload(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from dual_pipeline.parameters import ParameterRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_params_path = Path(tmpdir) / "parameters.json"
            seed = {
                "schema_version": "1.0.0",
                "updated_at": "2026-09-15T00:00:00Z",
                "parameters": {
                    "hitl": {"ambiguity_margin_threshold": 0.25},
                    "retrieval": {"colbert_weight": 0.40},
                },
            }
            tmp_params_path.write_text(json.dumps(seed, indent=2), encoding="utf-8")

            registry = ParameterRegistry(config_path=tmp_params_path)
            self.assertEqual(registry.get("retrieval.colbert_weight"), 0.40)

            valid_receipt = {
                "receipt_id": "rcpt-002",
                "proposal_id": self.proposal["proposal_id"],
                "proposal_digest": self.proposal["canonical_digest"]["value"],
                "approved_by": "operator-bob",
                "approved_at": "2026-09-15T01:00:00Z",
            }
            # Update target_parameter in proposal extensions to match dotted key
            self.proposal["extensions"]["com.partner_advisory.nee.calibration"]["target_parameter"] = "retrieval.colbert_weight"

            applied = apply_calibrated_parameter(self.proposal, valid_receipt, config_path=tmp_params_path, persist=True)
            self.assertEqual(applied["applied_value"], 0.55)

            # Reload and check persistent disk state
            reloaded = registry.reload()
            self.assertEqual(reloaded.get("retrieval", {}).get("colbert_weight"), 0.55)

    def test_dynamic_learned_aliases_registration_and_healing(self) -> None:
        import tempfile
        from pathlib import Path

        from dual_pipeline.transcript_qa_core.alias_registry import (
            load_learned_aliases,
            register_learned_alias,
        )
        from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_aliases_path = Path(tmpdir) / "learned_aliases.json"
            register_learned_alias(
                "system:jira_nexus",
                "system:jira-nexus",
                aliases_path=tmp_aliases_path,
            )

            loaded = load_learned_aliases(tmp_aliases_path)
            self.assertIn("system:jira_nexus", loaded)
            self.assertEqual(loaded["system:jira_nexus"], "system:jira-nexus")

            # Verify heal_graph_projection uses dynamic alias across entities and signals
            proj = {
                "graph_mutations": {
                    "entity_upserts": [{"id": "system:jira_nexus", "name": "Jira Nexus"}],
                    "relationship_signals": [
                        {
                            "from_id": "system:jira_nexus",
                            "to_id": "system:nexus",
                            "predicate": "INTEGRATES_WITH",
                        }
                    ],
                }
            }
            # Patch default aliases path temporarily for test
            import dual_pipeline.transcript_qa_core.alias_registry as ar
            orig_path = ar.DEFAULT_ALIASES_PATH
            try:
                ar.DEFAULT_ALIASES_PATH = tmp_aliases_path
                changes: list[str] = []
                healed = heal_graph_projection(proj, changes)
                mutations = healed["graph_mutations"]
                entity_ids = [e["id"] for e in mutations["entity_upserts"]]
                self.assertIn("system:jira-nexus", entity_ids)
                self.assertEqual(mutations["relationship_signals"][0]["from_id"], "system:jira-nexus")
                self.assertTrue(any("system:jira-nexus" in c for c in changes))
            finally:
                ar.DEFAULT_ALIASES_PATH = orig_path


if __name__ == "__main__":
    unittest.main()

