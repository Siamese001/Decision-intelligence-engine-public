#!/usr/bin/env python3
"""Regression tests for request-specific governed L2 output constraints."""

from __future__ import annotations

import unittest
from copy import deepcopy

from dual_pipeline.l2 import DECISION_MODEL_OUTPUT_SCHEMA, canonical_query_type
from dual_pipeline.l2_output_contract import (
    PAIRING_INVARIANT_DESCRIPTION,
    material_citation_obligations,
    output_contract_digest,
    record_bound_decision_schema,
    record_evidence_binding_map,
)


class L2OutputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "record-approved-fact",
                        "record_type": "assertion",
                        "evidence_ids": ["evidence-approved-fact"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "evidence": [],
                },
                {
                    "record": {
                        "record_id": "record-approved-guidance",
                        "record_type": "action_item",
                        "evidence_ids": [],
                    },
                    "retrieval_metadata": {"answer_use": "guidance"},
                    "evidence": [{"evidence_id": "evidence-approved-guidance-hydrated"}],
                },
                {
                    "record": {
                        "record_id": "record-approved-intent",
                        "record_type": "assertion",
                        "evidence_ids": ["evidence-approved-intent"],
                    },
                    "retrieval_metadata": {"answer_use": "strategic_context"},
                    "evidence": [],
                },
            ],
            "candidate": [],
            "external_public": [
                {
                    "record": {
                        "record_id": "record-public-fact",
                        "record_type": "assertion",
                        "evidence_ids": ["evidence-public-fact"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "evidence": [],
                },
                {
                    "record": {
                        "record_id": "record-public-analogy",
                        "record_type": "assertion",
                        "evidence_ids": ["evidence-public-analogy"],
                    },
                    "retrieval_metadata": {"answer_use": "external_analogy"},
                    "evidence": [],
                },
            ],
        }

    def _schema(self) -> dict:
        return record_bound_decision_schema(
            DECISION_MODEL_OUTPUT_SCHEMA,
            self.packet,
            expected_query_type="cross_workstream_synthesis",
            canonicalize_query_type=canonical_query_type,
        )

    def test_fields_are_bound_by_lane_and_answer_use(self) -> None:
        schema = self._schema()
        current = schema["properties"]["current_state"]["items"]["properties"]
        self.assertEqual(
            ["record-approved-fact", "record-public-fact"],
            current["citations"]["items"]["enum"],
        )
        self.assertEqual(
            ["evidence-approved-fact", "evidence-public-fact"],
            current["evidence_ids"]["items"]["enum"],
        )

        guidance = schema["properties"]["guidance"]["items"]["properties"]
        self.assertEqual(
            ["record-approved-guidance"],
            guidance["citations"]["items"]["enum"],
        )
        self.assertEqual(
            ["evidence-approved-guidance-hydrated"],
            guidance["evidence_ids"]["items"]["enum"],
        )

        actions = schema["properties"]["next_best_actions"]["items"]["properties"]
        self.assertEqual(
            ["record-approved-fact"],
            actions["basis_citations"]["items"]["enum"],
        )

    def test_fields_with_no_eligible_records_are_forced_empty(self) -> None:
        schema = self._schema()
        self.assertEqual(0, schema["properties"]["hypotheses"]["maxItems"])
        self.assertEqual(0, schema["properties"]["proposed_assertions"]["maxItems"])
        self.assertEqual(4, schema["properties"]["guidance"]["maxItems"])

    def test_pairing_guidance_and_query_type_are_embedded(self) -> None:
        schema = self._schema()
        self.assertEqual(
            "cross_workstream_synthesis",
            schema["properties"]["query_type"]["const"],
        )
        guidance = schema["properties"]["guidance"]["items"]["properties"]
        description = guidance["evidence_ids"]["description"]
        self.assertEqual(PAIRING_INVARIANT_DESCRIPTION, description)
        self.assertIn("C0M", description)
        self.assertEqual(
            ["evidence-approved-guidance-hydrated"],
            record_evidence_binding_map(self.packet)["record-approved-guidance"],
        )
        self.assertNotIn("record-approved-guidance", description)

    def test_material_obligations_bind_required_records_to_eligible_fields(self) -> None:
        packet = deepcopy(self.packet)
        packet["required_material_record_ids_by_lane"] = {
            "approved": ["record-approved-guidance", "record-approved-fact"],
            "candidate": [],
            "external_public": [],
        }
        obligations = material_citation_obligations(
            packet,
            query_type="cross_workstream_synthesis",
        )
        self.assertEqual(
            [
                {
                    "lane": "approved",
                    "record_id": "record-approved-guidance",
                    "evidence_ids": ["evidence-approved-guidance-hydrated"],
                    "answer_use": "guidance",
                    "eligible_answer_fields": ["guidance"],
                    "record_present_in_packet": True,
                },
                {
                    "lane": "approved",
                    "record_id": "record-approved-fact",
                    "evidence_ids": ["evidence-approved-fact"],
                    "answer_use": "factual_answer",
                    "eligible_answer_fields": [
                        "current_state",
                        "counterevidence",
                        "pain_points",
                        "opportunities",
                    ],
                    "record_present_in_packet": True,
                },
            ],
            obligations,
        )

    def test_projection_does_not_mutate_static_schema(self) -> None:
        original = deepcopy(DECISION_MODEL_OUTPUT_SCHEMA)
        self._schema()
        self.assertEqual(original, DECISION_MODEL_OUTPUT_SCHEMA)

    def test_contract_digest_is_bound_to_schema(self) -> None:
        original = output_contract_digest(DECISION_MODEL_OUTPUT_SCHEMA)
        changed_schema = deepcopy(DECISION_MODEL_OUTPUT_SCHEMA)
        changed_schema["properties"]["rationale"]["description"] = "changed"
        self.assertNotEqual(original, output_contract_digest(changed_schema))

    def test_opportunity_contract_admits_scoped_strategic_basis_only(self) -> None:
        schema = record_bound_decision_schema(
            DECISION_MODEL_OUTPUT_SCHEMA,
            self.packet,
            expected_query_type="opportunity_nba",
            canonicalize_query_type=canonical_query_type,
        )
        opportunities = schema["properties"]["opportunities"]["items"]["properties"]
        self.assertEqual(
            [
                "record-approved-fact",
                "record-approved-intent",
                "record-public-analogy",
                "record-public-fact",
            ],
            opportunities["citations"]["items"]["enum"],
        )
        actions = schema["properties"]["next_best_actions"]["items"]["properties"]
        self.assertEqual(
            [
                "record-approved-fact",
                "record-approved-intent",
                "record-public-analogy",
                "record-public-fact",
            ],
            actions["basis_citations"]["items"]["enum"],
        )
        current = schema["properties"]["current_state"]["items"]["properties"]
        self.assertNotIn("record-approved-intent", current["citations"]["items"]["enum"])
        self.assertNotIn("record-public-analogy", current["citations"]["items"]["enum"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
