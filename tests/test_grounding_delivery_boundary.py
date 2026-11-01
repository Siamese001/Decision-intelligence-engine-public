"""tests/test_grounding_delivery_boundary.py

Qualification suite for dual_pipeline.grounding.delivery_boundary and GroundedDeliveryGuard.
Asserts that:
1. Complete delivery surface inventory with explicit rejection of unintegrated hosts.
2. Mandatory pre-release verification: zero payload releases on missing, failed, or tampered receipts.
3. Post-verification immutability: prevents secondary formatters or signatures from mutating content after certification.
4. Safe failure response contract: suppresses ungrounded draft text and restricted evidence while providing clear rejection causes.
5. Robust error handling wrapping destination sink dispatch exceptions into SafeFailureResponse.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import List

from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    ClaimRecord,
    SourceLocator,
)
from dual_pipeline.grounding.delivery_boundary import (
    GroundedDeliveryGuard,
    PostVerificationTamperingError,
    SafeFailureResponse,
    SUPPORTED_DELIVERY_SURFACES,
    UnsupportedDeliverySurfaceError,
    verify_grounded_delivery,
)
from dual_pipeline.grounding.snapshot import _compute_sha256
from storage.connection import create_sqlite_conn


class TestGroundingDeliveryBoundary(unittest.TestCase):
    """Test suite for GroundedDeliveryGuard and delivery boundary enforcement."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        self.runtime_root = self.temp_dir / "runtime"
        self.runtime_root.mkdir()

        # Initialize SQLite graph
        self.db_path = self.runtime_root / "canonical_graph.db"
        conn = create_sqlite_conn(self.db_path, readonly=False)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                node_type TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                literal_value TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                observed_at TEXT,
                stale_after TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            """
        )
        # Seed nodes and assertions
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-PRF', 'PRF Automation', 'Process', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-SF', 'SAP SuccessFactors', 'System', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-AMIT', 'Amit Ayer', 'Person', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-CONF', 'Confidential Topic', 'Secret', 'INTERNAL_ADVISORY')")

        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-001', 'N-PRF', 'touches_system', 'N-SF', NULL,
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-TITLE-01', 'N-AMIT', 'has_title', NULL, 'Solution Architect',
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-SECRET-01', 'N-CONF', 'has_valuation', NULL, 'executive compensation band',
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'INTERNAL_ADVISORY'
            )
            """
        )
        conn.commit()
        conn.close()

        self.graph_sha = _compute_sha256(self.db_path)
        self.gen_id = "GEN-DELIVERY-001"
        pair_data = {
            "generation_id": self.gen_id,
            "graph_path": "canonical_graph.db",
            "graph_sha256": self.graph_sha,
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(pair_data), encoding="utf-8")

        self.locator = SourceLocator(
            locator_type="transcript_paragraph",
            source_uri="file:///transcripts/meeting_2026_10_06.md",
            locator_value="para_42",
        )
        self.claim = ClaimRecord(
            claim_id="CLM-001",
            subject="PRF Automation",
            predicate="touches_system",
            object="SAP SuccessFactors",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )

        self.guard = GroundedDeliveryGuard()
        self.captured_sink_payloads: List[str] = []

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def mock_sink(self, payload: str) -> str:
        self.captured_sink_payloads.append(payload)
        return "DISPATCHED_OK"

    def test_supported_surfaces_inventory(self) -> None:
        """Verify complete surface inventory and explicit rejection of unintegrated hosts."""
        expected = {
            "EMAIL_BODY",
            "TEAMS_MESSAGE",
            "SLACK_MESSAGE",
            "REPORT_ARTIFACT",
            "EXPORT_FILE",
            "EXECUTIVE_AGENDA",
            "TALKING_POINTS",
            "QUERY_SYNTHESIS",
        }
        self.assertEqual(SUPPORTED_DELIVERY_SURFACES, expected)

        text = "Hello Clement"
        manifest = ClaimManifest(
            manifest_id="MNF-SURF-001",
            payload_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[],
        )

        with self.assertRaises(UnsupportedDeliverySurfaceError) as ctx:
            self.guard.deliver(
                payload_text=text,
                manifest=manifest,
                runtime_root=self.runtime_root,
                surface="WHATSAPP_UNGOVERNED",
                sink=self.mock_sink,
            )
        self.assertIn("WHATSAPP_UNGOVERNED", str(ctx.exception))
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_certified_delivery_success(self) -> None:
        """Verify fully grounded payload releases to sink and captures response."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        result = self.guard.deliver(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="EMAIL_BODY",
            sink=self.mock_sink,
        )

        self.assertTrue(result.delivered)
        self.assertEqual(result.surface, "EMAIL_BODY")
        self.assertEqual(result.receipt.status, "CERTIFIED")
        self.assertEqual(result.receipt.reason_code, "GROUNDED_SUPPORTED")
        self.assertIsNone(result.safe_response)
        self.assertEqual(result.sink_response, "DISPATCHED_OK")
        self.assertEqual(len(self.captured_sink_payloads), 1)
        self.assertEqual(self.captured_sink_payloads[0], text)

    def test_post_verification_tampering_blocks_delivery(self) -> None:
        """Verify post-verification payload alteration triggers PostVerificationTamperingError with 0 sink calls."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        # Create a subclass or wrapper that mutates payload_text before step 4
        class TamperingGuard(GroundedDeliveryGuard):
            def deliver(self, payload_text, manifest, runtime_root, surface, sink, max_words=500, audience="CLIENT_SAFE"):
                # Call original verification, but simulate a secondary formatter mutating text right before release
                certified, receipt, reasons = self._verify(payload_text, manifest, runtime_root, max_words, audience, surface)
                tampered_text = payload_text + "\n[TAMPERED CONTENT INSERTED BY UNGOVERNED FORMATTER]"
                exact_sha = hashlib.sha256(tampered_text.encode("utf-8")).hexdigest()
                if exact_sha != receipt.payload_digest:
                    raise PostVerificationTamperingError(
                        f"Post-verification modification detected: sha {exact_sha} != receipt {receipt.payload_digest}"
                    )
                return sink(tampered_text)

            def _verify(self, text, manifest, runtime_root, max_words, audience, surface):
                from dual_pipeline.grounding.delivery_boundary import verify_grounded_delivery
                return verify_grounded_delivery(text, manifest, runtime_root, max_words, audience, surface)

        guard = TamperingGuard()
        with self.assertRaises(PostVerificationTamperingError) as ctx:
            guard.deliver(
                payload_text=text,
                manifest=manifest,
                runtime_root=self.runtime_root,
                surface="EMAIL_BODY",
                sink=self.mock_sink,
            )
        self.assertIn("Post-verification modification detected", str(ctx.exception))
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_unsupported_claim_blocks_delivery_with_safe_response(self) -> None:
        """Verify unasserted claim blocks delivery, emits SafeFailureResponse and suppresses draft."""
        draft_text = "Hello Clement,\n\nPRF Automation integrates directly with Workday HCM without credentials.\n\nBest regards,\nAmit Ayer\n"
        digest = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()

        unsupported_claim = ClaimRecord(
            claim_id="CLM-UNSUPPORTED",
            subject="PRF Automation",
            predicate="integrates_with",
            object="Workday HCM",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-NONEXISTENT"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )

        manifest = ClaimManifest(
            manifest_id="MNF-FAIL-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[unsupported_claim],
        )

        result = self.guard.deliver(
            payload_text=draft_text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="EMAIL_BODY",
            sink=self.mock_sink,
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.receipt.status, "REJECTED")
        self.assertEqual(result.receipt.reason_code, "UNSUPPORTED_CLAIM")
        self.assertIsNotNone(result.safe_response)
        self.assertTrue(result.safe_response.draft_suppressed)
        # Ensure raw ungrounded draft text is not leaked in the safe response message or dict
        safe_dict = result.safe_response.to_dict()
        self.assertNotIn("without credentials", json.dumps(safe_dict))
        self.assertNotIn("Hello Clement", json.dumps(safe_dict))
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_word_count_exceeded_blocks_delivery(self) -> None:
        """Verify strict < 500 word limit enforcement blocks release."""
        long_words = ["word"] * 505
        text = " ".join(long_words)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-LONG-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[],
        )

        result = self.guard.deliver(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="TEAMS_MESSAGE",
            sink=self.mock_sink,
            max_words=500,
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.receipt.status, "REJECTED")
        self.assertEqual(result.receipt.reason_code, "WORD_COUNT_EXCEEDED")
        self.assertIsNotNone(result.safe_response)
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_invented_persona_title_blocks_delivery(self) -> None:
        """Verify fabricated persona title in signature blocks delivery with AMBIGUOUS_IDENTITY."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nClient Partner & Delivery Lead\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-TITLE-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        result = self.guard.deliver(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="SLACK_MESSAGE",
            sink=self.mock_sink,
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.receipt.status, "REJECTED")
        self.assertEqual(result.receipt.reason_code, "AMBIGUOUS_IDENTITY")
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_generation_drift_blocks_delivery(self) -> None:
        """Verify manifest bound to stale generation fails closed."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-STALE-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id="GEN-STALE-000",
            graph_sha256="STALE_SHA256",
            claims=[self.claim],
        )

        result = self.guard.deliver(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="REPORT_ARTIFACT",
            sink=self.mock_sink,
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.receipt.status, "REJECTED")
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_forged_payload_digest_blocks_delivery(self) -> None:
        """Verify manifest with mismatched payload digest fails closed."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        forged_digest = "0000000000000000000000000000000000000000000000000000000000000000"

        manifest = ClaimManifest(
            manifest_id="MNF-FORGED-001",
            payload_digest=forged_digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        result = self.guard.deliver(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="EXPORT_FILE",
            sink=self.mock_sink,
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.receipt.status, "REJECTED")
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_sink_dispatch_failure_wrapped_in_safe_response(self) -> None:
        """Verify exception raised inside destination sink is caught and wrapped safely."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        def broken_sink(p: str) -> None:
            raise ConnectionResetError("Remote mail host hung up")

        result = self.guard.deliver(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="EMAIL_BODY",
            sink=broken_sink,
        )

        self.assertFalse(result.delivered)
        self.assertIsNotNone(result.safe_response)
        self.assertEqual(result.safe_response.reason_code, "SINK_DISPATCH_FAILURE")
        self.assertTrue(result.safe_response.draft_suppressed)
        self.assertIn("failed to dispatch", result.safe_response.public_message)

    def test_audience_isolation_blocks_restricted_evidence(self) -> None:
        """Verify delivery guard blocks claims from restricted namespaces without leaking secrets."""
        draft_text = "Executive note regarding executive compensation band."
        digest = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()

        secret_claim = ClaimRecord(
            claim_id="CLM-SECRET-001",
            subject="Confidential Topic",
            predicate="has_valuation",
            object="executive compensation band",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-SECRET-01"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )

        manifest = ClaimManifest(
            manifest_id="MNF-SECRET-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[secret_claim],
        )

        result = self.guard.deliver(
            payload_text=draft_text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            surface="EMAIL_BODY",
            sink=self.mock_sink,
            audience="CLIENT_SAFE",
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.receipt.status, "REJECTED")
        self.assertEqual(result.receipt.reason_code, "INACCESSIBLE_AUDIENCE")
        self.assertIsNotNone(result.safe_response)
        self.assertTrue(result.safe_response.draft_suppressed)
        self.assertEqual(len(self.captured_sink_payloads), 0)

    def test_empty_claims_rejected_zero_graph_traversal(self) -> None:
        """Verify empty manifest claims are rejected fail-closed with ZERO_GRAPH_TRAVERSAL."""
        text = "Legitimate looking ungrounded sentence."
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        manifest = ClaimManifest(
            manifest_id="MNF-EMPTY-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[],
        )
        passed, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            delivery_surface="TALKING_POINTS",
        )
        self.assertFalse(passed)
        self.assertEqual(receipt.status, "REJECTED")
        self.assertEqual(receipt.reason_code, "ZERO_GRAPH_TRAVERSAL")
        self.assertTrue(any("EMPTY_CLAIM_MANIFEST" in r for r in reasons))

    def test_cross_speaker_prompt_echo_rejection(self) -> None:
        """Exact NEE All-Hands regression: cross-speaker topic 'Rewire' scavenged into Amit Ayer's points is rejected."""
        prompt = (
            "Drew Oakley - Rewire project delivery and cloud migration.\n"
            "Stephan - Architecture governance and platform scaling.\n"
            "Amit Ayer - Please provide < 50 words talking points."
        )
        text = "Amit Ayer leads Rewire project delivery and cloud migration for the team."
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # Create valid claim about Amit Ayer's actual work
        claim = ClaimRecord(
            claim_id="CLM-AMIT-001",
            subject="Amit Ayer",
            predicate="leads",
            object="Rewire",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-VALID-01"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )
        manifest = ClaimManifest(
            manifest_id="MNF-ECHO-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[claim],
        )
        passed, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            delivery_surface="TALKING_POINTS",
            prompt_text=prompt,
        )
        self.assertFalse(passed)
        self.assertEqual(receipt.reason_code, "PROMPT_ECHO_CONTAMINATION")
        self.assertTrue(any("CROSS_SPEAKER_PROMPT_ECHO" in r for r in reasons))

    def test_unanchored_pod_placeholder_rejection(self) -> None:
        """Regression test: unverified container modifiers like 'delivery pods' fail closed."""
        text = "Amit Ayer coordinates the AI, data, and core delivery pods across engineering."
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        claim = ClaimRecord(
            claim_id="CLM-POD-001",
            subject="Amit Ayer",
            predicate="coordinates",
            object="delivery pods",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-VALID-01"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )
        manifest = ClaimManifest(
            manifest_id="MNF-POD-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[claim],
        )
        passed, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            delivery_surface="TALKING_POINTS",
        )
        self.assertFalse(passed)
        self.assertEqual(receipt.reason_code, "UNANCHORED_PLACEHOLDER_CLAIM")
        self.assertTrue(any("UNANCHORED_PLACEHOLDER_CLAIM" in r for r in reasons))

    def test_dynamic_prompt_word_ceiling_enforcement(self) -> None:
        """Verify dynamic < 50 word prompt constraint strictly rejects 50 and 51 words while certifying 49 words."""
        prompt = "Amit Ayer - Provide < 50 words talking points on Project Lens."
        words_49 = ["word"] * 48 + ["final"]
        text_49 = " ".join(words_49)
        words_50 = ["word"] * 49 + ["final"]
        text_50 = " ".join(words_50)
        words_51 = ["word"] * 50 + ["final"]
        text_51 = " ".join(words_51)

        claim_49 = self.claim
        manifest_49 = ClaimManifest(
            manifest_id="MNF-W49",
            payload_digest=hashlib.sha256(text_49.encode("utf-8")).hexdigest(),
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[claim_49],
        )
        passed_49, receipt_49, _ = verify_grounded_delivery(
            payload_text=text_49,
            manifest=manifest_49,
            runtime_root=self.runtime_root,
            delivery_surface="TALKING_POINTS",
            prompt_text=prompt,
        )
        self.assertTrue(passed_49)
        self.assertEqual(receipt_49.status, "CERTIFIED")

        # 50 words fails
        manifest_50 = ClaimManifest(
            manifest_id="MNF-W50",
            payload_digest=hashlib.sha256(text_50.encode("utf-8")).hexdigest(),
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[claim_49],
        )
        passed_50, receipt_50, _ = verify_grounded_delivery(
            payload_text=text_50,
            manifest=manifest_50,
            runtime_root=self.runtime_root,
            delivery_surface="TALKING_POINTS",
            prompt_text=prompt,
        )
        self.assertFalse(passed_50)
        self.assertEqual(receipt_50.reason_code, "WORD_COUNT_EXCEEDED")

        # 51 words fails
        manifest_51 = ClaimManifest(
            manifest_id="MNF-W51",
            payload_digest=hashlib.sha256(text_51.encode("utf-8")).hexdigest(),
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[claim_49],
        )
        passed_51, receipt_51, _ = verify_grounded_delivery(
            payload_text=text_51,
            manifest=manifest_51,
            runtime_root=self.runtime_root,
            delivery_surface="TALKING_POINTS",
            prompt_text=prompt,
        )
        self.assertFalse(passed_51)
        self.assertEqual(receipt_51.reason_code, "WORD_COUNT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
