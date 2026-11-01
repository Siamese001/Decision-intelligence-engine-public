#!/usr/bin/env python3
"""Regression tests for the W1 query-time retrieval shield."""

from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.index import render_retrieval_records
from dual_pipeline.retrieval_clearance import retrieval_clearance

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_GRAPH = (
    ROOT
    / ".local"
    / "main"
    / "runtime-bge-m3-sherpa-strategic-20260729"
    / "generations"
    / "generation-combined-remediation-1d2b1a2f0c1e77c644b734c6acb3"
    / "graph.sqlite"
)


class RetrievalClearanceTests(unittest.TestCase):
    def test_derived_assertions_are_denied_even_when_flags_claim_eligibility(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="recommendation",
            classification="internal",
            audiences=["internal_consulting"],
        )
        self.assertFalse(allowed)
        self.assertEqual("derived_assertion_type_not_retrieval_eligible", reason)

    def test_client_and_restricted_audiences_are_denied(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="internal",
            audiences=["nee-engineering", "internal_working_team"],
        )
        self.assertFalse(allowed)
        self.assertEqual("client_and_restricted_audiences_mixed", reason)

    def test_confidential_client_exposure_is_denied(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="direct_statement",
            classification="confidential",
            audiences=["client_approved_external"],
        )
        self.assertFalse(allowed)
        self.assertEqual("confidential_record_exposed_to_client_audience", reason)

    def test_malformed_audience_metadata_is_denied(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="internal",
            audiences="not-json",
        )
        self.assertFalse(allowed)
        self.assertEqual("invalid_audience_metadata", reason)

    def test_missing_nonlegacy_lineage_is_denied(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="direct_statement",
            classification="internal",
            audiences=["internal_consulting"],
            origin_type="human_source",
            extraction_run_id=None,
        )
        self.assertFalse(allowed)
        self.assertEqual("missing_nonlegacy_extraction_lineage", reason)

    def test_incomplete_hitl_assignment_is_denied(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="direct_statement",
            classification="internal",
            audiences=["internal_consulting"],
            origin_type="legacy_migration",
            extraction_run_id=None,
            hitl_required=1,
            validation_priority=None,
            validation_owner=None,
        )
        self.assertFalse(allowed)
        self.assertEqual("hitl_assignment_incomplete", reason)

    def test_unparsed_legacy_assertion_is_denied(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="legacy_claim_record",
            classification="internal",
            audiences=["internal_consulting"],
            origin_type="legacy_migration",
            atomicity_state="unparsed_legacy_text",
        )
        self.assertFalse(allowed)
        self.assertEqual("unparsed_legacy_assertion_not_retrieval_eligible", reason)

    def test_safe_shared_fact_remains_allowed(self) -> None:
        allowed, reason = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="internal",
            audiences=["internal_consulting", "nee-engineering"],
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    @unittest.skipUnless(ACTIVE_GRAPH.exists(), "active W0 graph is not present")
    def test_active_render_has_no_known_shield_violations(self) -> None:
        records = render_retrieval_records(ACTIVE_GRAPH, "w1-retrieval-shield-test")
        self.assertTrue(records)
        self.assertEqual(
            0,
            sum(
                record.get("extensions", {}).get("com.partner_advisory.nee.assertion-type")
                in {"human_interpretation", "recommendation", "hypothesis", "pov"}
                for record in records
            ),
        )
        self.assertEqual(
            0,
            sum(
                record.get("extensions", {}).get("com.partner_advisory.nee.atomicity-state")
                == "unparsed_legacy_text"
                for record in records
            ),
        )
        self.assertEqual(
            0,
            sum(
                record.get("extensions", {}).get("com.partner_advisory.nee.origin-type")
                not in (None, "legacy_migration")
                and not record.get("extensions", {}).get(
                    "com.partner_advisory.nee.extraction-run-id"
                )
                for record in records
            ),
        )
        self.assertEqual(
            0,
            sum(
                record.get("extensions", {}).get("com.partner_advisory.nee.hitl-required") in (1, True)
                and (
                    not record.get("extensions", {}).get(
                        "com.partner_advisory.nee.validation-priority"
                    )
                    or not record.get("extensions", {}).get(
                        "com.partner_advisory.nee.validation-owner"
                    )
                )
                for record in records
            ),
        )
        self.assertEqual(
            0,
            sum(
                bool(set(record["audiences"]) & {"nee-engineering", "client_approved_external"})
                and bool(
                    set(record["audiences"])
                    & {"internal_working_team", "partner_advisory_account_leadership", "private_personal"}
                )
                for record in records
            ),
        )
        self.assertEqual(
            0,
            sum(
                record["classification"] in {"confidential", "restricted"}
                and bool(set(record["audiences"]) & {"nee-engineering", "client_approved_external"})
                for record in records
            ),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
