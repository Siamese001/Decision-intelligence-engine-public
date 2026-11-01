"""tests/test_grounding_contract.py

Qualification suite for grounding contracts, claim manifest schema, and delivery receipt.
Asserts that:
1. SourceLocator enforces typed evidence pointers and validates required locator fields.
2. ClaimRecord models structured facts and rejects incomplete or invalid assertions.
3. ClaimManifest validates against JSON Schema (Draft 2020-12) with common schema referencing.
4. DeliveryReceipt validates against JSON Schema (Draft 2020-12) with complete runtime evidence.
5. RFC-8259 canonical hashing is deterministic, key-order invariant, and detects tampering.
6. Malformed payloads, invalid statuses, and missing required properties fail closed.
"""
from __future__ import annotations

import json
import unittest

import jsonschema

from dual_pipeline.grounding.contracts import (
    CLAIM_MANIFEST_SCHEMA_PATH,
    DELIVERY_RECEIPT_SCHEMA_PATH,
    ClaimManifest,
    ClaimRecord,
    DeliveryReceipt,
    SourceLocator,
    _build_validator,
    compute_rfc8259_digest,
)


class TestGroundingContract(unittest.TestCase):
    """Test suite for grounding schemas, data classes, and cryptographic sealing."""

    def setUp(self) -> None:
        self.sample_locator = SourceLocator(
            locator_type="transcript_paragraph",
            source_uri="file:///transcripts/meeting_2026_10_06.md",
            locator_value="para_42",
        )

        self.sample_claim = ClaimRecord(
            claim_id="CLM-001",
            subject="PRF Automation",
            predicate="touches_system",
            object="SAP SuccessFactors",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.sample_locator],
            qualifiers=["production"],
            temporal_validity={"valid_from": "2026-10-06T10:00:00Z", "valid_to": None},
            supporting_assertion_ids=["ASN-001"],
            contradicting_assertion_ids=[],
        )

        self.sample_manifest = ClaimManifest(
            manifest_id="MNF-20261006-001",
            payload_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            runtime_root="/deployments/live/runtime",
            graph_generation_id="GEN-48-20261006",
            graph_sha256="abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
            claims=[self.sample_claim],
            created_at="2026-10-06T10:05:00Z",
        )

    def test_source_locator_types_and_serialization(self) -> None:
        """Verify typed source locators preserve type and serialize cleanly."""
        loc_dict = self.sample_locator.to_dict()
        self.assertEqual(loc_dict["locator_type"], "transcript_paragraph")
        self.assertEqual(loc_dict["source_uri"], "file:///transcripts/meeting_2026_10_06.md")
        self.assertEqual(loc_dict["locator_value"], "para_42")

        # Test file line locator round-trip
        file_loc = SourceLocator(
            locator_type="file_line",
            source_uri="file:///src/models.py",
            locator_value="line_105",
        )
        self.assertEqual(file_loc.locator_type, "file_line")
        self.assertNotEqual(file_loc.locator_type, self.sample_locator.locator_type)
        reconstructed = SourceLocator.from_dict(file_loc.to_dict())
        self.assertEqual(file_loc, reconstructed)

    def test_claim_record_completeness(self) -> None:
        """Verify ClaimRecord captures full subject/predicate/object and attributes."""
        claim_dict = self.sample_claim.to_dict()
        self.assertEqual(claim_dict["claim_id"], "CLM-001")
        self.assertEqual(claim_dict["subject"], "PRF Automation")
        self.assertEqual(claim_dict["predicate"], "touches_system")
        self.assertEqual(claim_dict["object"], "SAP SuccessFactors")
        self.assertEqual(len(claim_dict["source_locators"]), 1)
        self.assertEqual(claim_dict["evidence_lane"], "approved")

        reconstructed = ClaimRecord.from_dict(claim_dict)
        self.assertEqual(self.sample_claim, reconstructed)

    def test_claim_manifest_valid_schema(self) -> None:
        """Verify valid ClaimManifest passes JSON Schema Draft 2020-12 validation."""
        self.sample_manifest.validate()
        manifest_dict = self.sample_manifest.to_dict()
        self.assertEqual(manifest_dict["schema_version"], "1.0.0")
        self.assertEqual(manifest_dict["claims"][0]["subject"], "PRF Automation")
        self.assertIn("canonical_digest", manifest_dict)
        self.assertEqual(len(manifest_dict["canonical_digest"]["value"]), 64)

    def test_claim_manifest_rfc8259_digest_and_tamper_detection(self) -> None:
        """Verify deterministic RFC-8259 digest calculation and tampering sensitivity."""
        dict1 = self.sample_manifest.to_dict()
        digest1 = dict1["canonical_digest"]["value"]
        self.assertEqual(len(digest1), 64)

        # Invariance to recalculation
        dict2 = self.sample_manifest.to_dict()
        self.assertEqual(digest1, dict2["canonical_digest"]["value"])

        # Key ordering invariance in dict hashing
        d1 = {"z": 1, "a": 2, "m": {"b": 3, "a": 4}}
        d2 = {"a": 2, "z": 1, "m": {"a": 4, "b": 3}}
        self.assertEqual(compute_rfc8259_digest(d1), compute_rfc8259_digest(d2))

        # Tampering with claim object changes the manifest digest
        tampered_manifest = ClaimManifest(
            manifest_id="MNF-20261006-001",
            payload_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            runtime_root="/deployments/live/runtime",
            graph_generation_id="GEN-48-20261006",
            graph_sha256="abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
            claims=[
                ClaimRecord(
                    claim_id="CLM-001",
                    subject="PRF Automation",
                    predicate="touches_system",
                    object="Workday",  # Tampered object
                    scope="PROJECT_LEVEL",
                    negation=False,
                    assertion_ids=["ASN-001"],
                    evidence_lane="approved",
                    epistemic_status="approved",
                    source_locators=[self.sample_locator],
                )
            ],
            created_at="2026-10-06T10:05:00Z",
        )
        tampered_dict = tampered_manifest.to_dict()
        self.assertNotEqual(digest1, tampered_dict["canonical_digest"]["value"])

    def test_claim_manifest_rejection_on_invalid_epistemic_status(self) -> None:
        """Verify ClaimManifest rejects unapproved epistemic status values."""
        bad_claim_dict = self.sample_claim.to_dict()
        bad_claim_dict["epistemic_status"] = "UNVERIFIED_RUMOR"

        bad_manifest_dict = self.sample_manifest.to_dict()
        bad_manifest_dict["claims"] = [bad_claim_dict]

        validator = _build_validator(CLAIM_MANIFEST_SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(bad_manifest_dict)

    def test_claim_manifest_rejection_on_missing_required_fields(self) -> None:
        """Verify ClaimManifest rejects payloads missing required top-level fields."""
        bad_dict = self.sample_manifest.to_dict()
        del bad_dict["payload_digest"]

        validator = _build_validator(CLAIM_MANIFEST_SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(bad_dict)

    def test_claim_manifest_rejection_on_invalid_locator_type(self) -> None:
        """Verify ClaimManifest schema blocks unknown source locator types."""
        bad_claim_dict = self.sample_claim.to_dict()
        bad_claim_dict["source_locators"] = [
            {"locator_type": "telepathic_intuition", "source_uri": "brain://clement", "locator_value": "memory"}
        ]
        bad_manifest_dict = self.sample_manifest.to_dict()
        bad_manifest_dict["claims"] = [bad_claim_dict]

        validator = _build_validator(CLAIM_MANIFEST_SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(bad_manifest_dict)

    def test_delivery_receipt_valid_schema_and_digest(self) -> None:
        """Verify DeliveryReceipt passes JSON Schema validation and computes canonical digest."""
        receipt = DeliveryReceipt(
            receipt_id="RCP-20261006-001",
            manifest_id=self.sample_manifest.manifest_id,
            manifest_digest="72fa15fad5eb5794635670f85d47a4d741babd0e7dd2441e4c341b6543745ca6",
            payload_digest=self.sample_manifest.payload_digest,
            runtime_root=self.sample_manifest.runtime_root,
            graph_generation_id=self.sample_manifest.graph_generation_id,
            graph_sha256=self.sample_manifest.graph_sha256,
            status="CERTIFIED",
            reason_code="GROUNDED_SUPPORTED",
            total_claims_count=1,
            supported_claims_count=1,
            contradicted_claims_count=0,
            delivery_surface="EMAIL_BODY",
            audience="internal_consulting",
            word_count=45,
            certified_at="2026-10-06T10:06:01Z",
        )
        receipt.validate()
        receipt_dict = receipt.to_dict()
        self.assertEqual(receipt_dict["status"], "CERTIFIED")
        self.assertIn("canonical_digest", receipt_dict)
        self.assertEqual(len(receipt_dict["canonical_digest"]["value"]), 64)

    def test_delivery_receipt_rejection_on_invalid_status(self) -> None:
        """Verify DeliveryReceipt rejects non-conforming status strings."""
        receipt_dict = DeliveryReceipt(
            receipt_id="RCP-20261006-001",
            manifest_id=self.sample_manifest.manifest_id,
            manifest_digest="72fa15fad5eb5794635670f85d47a4d741babd0e7dd2441e4c341b6543745ca6",
            payload_digest=self.sample_manifest.payload_digest,
            runtime_root=self.sample_manifest.runtime_root,
            graph_generation_id=self.sample_manifest.graph_generation_id,
            graph_sha256=self.sample_manifest.graph_sha256,
            status="CERTIFIED",
            reason_code="GROUNDED_SUPPORTED",
            total_claims_count=1,
            supported_claims_count=1,
            contradicted_claims_count=0,
            delivery_surface="EMAIL_BODY",
            audience="internal_consulting",
            word_count=45,
            certified_at="2026-10-06T10:06:01Z",
        ).to_dict()
        receipt_dict["status"] = "SUCCESSFUL"  # Invalid status

        validator = _build_validator(DELIVERY_RECEIPT_SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(receipt_dict)

    def test_delivery_receipt_rejection_on_invalid_reason_code(self) -> None:
        """Verify DeliveryReceipt rejects unapproved reason codes."""
        receipt_dict = DeliveryReceipt(
            receipt_id="RCP-20261006-001",
            manifest_id=self.sample_manifest.manifest_id,
            manifest_digest="72fa15fad5eb5794635670f85d47a4d741babd0e7dd2441e4c341b6543745ca6",
            payload_digest=self.sample_manifest.payload_digest,
            runtime_root=self.sample_manifest.runtime_root,
            graph_generation_id=self.sample_manifest.graph_generation_id,
            graph_sha256=self.sample_manifest.graph_sha256,
            status="BLOCKED",
            reason_code="GROUNDED_SUPPORTED",
            total_claims_count=1,
            supported_claims_count=0,
            contradicted_claims_count=1,
            delivery_surface="EMAIL_BODY",
            audience="internal_consulting",
            word_count=45,
            certified_at="2026-10-06T10:06:01Z",
        ).to_dict()
        receipt_dict["reason_code"] = "RANDOM_REASON"

        validator = _build_validator(DELIVERY_RECEIPT_SCHEMA_PATH)
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(receipt_dict)


if __name__ == "__main__":
    unittest.main()
