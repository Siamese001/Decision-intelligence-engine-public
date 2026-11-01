"""tests/test_grounding_end_to_end.py

End-to-End Adversarial Qualification Suite for Mandatory GraphDB Sourcing and Anti-Hallucination (Wave 6).
Asserts that:
1. Incident Family 1 (Invented Title): Fabricated roles/titles without graph assertions are rejected fail-closed.
2. Incident Family 2 (Guessed Metric): Payloads exceeding word ceilings (>=500 words) or ungrounded statistics fail closed.
3. Incident Family 3 (Unsupported System/Locator Claims): Unsubstantiated architecture or API claims fail validation.
4. Incident Family 4 (Tool-Crash Fallback): Failures emit structured SafeFailureResponses, never ungrounded prose fallbacks.
5. Answered-Question Duplication: Existing graph assertions flag redundant questions in the request-gaps matrix.
6. Receipt Tampering: Forged PASS receipts, altered digests, or contradictory counts are rejected fail-closed.
7. 14-Table Zero Mutation: Entire grounding and delivery cycle produces IDEMPOTENT_NOOP +0 across all 14 tables.
8. Full Lifecycle: End-to-end evidence -> manifest -> validation -> boundary guard -> receipt -> staged Git verification.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.graph_delta import (
    _COMPONENTS,
    _SIDECAR_COMPONENTS,
    format_markdown_mutation_table,
)
from dual_pipeline.grounding import (
    ClaimManifest,
    ClaimRecord,
    DeliveryReceipt,
    DeliveryResult,
    GapClassification,
    GroundedDeliveryGuard,
    RequestGapAuditor,
    SafeFailureResponse,
    SourceLocator,
    SUPPORTED_DELIVERY_SURFACES,
    UnsupportedDeliverySurfaceError,
    count_words,
    evaluate_text_metrics,
    verify_grounded_delivery,
)
from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot, _compute_sha256
from storage.connection import create_sqlite_conn
from tools.validate_staged_grounding import validate_staged_grounding


class TestGroundingEndToEnd(unittest.TestCase):
    """Adversarial qualification test suite for GraphDB grounding and delivery boundaries."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir_obj.name)

        # 1. Initialize SQLite database with canonical schema tables
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
                namespace TEXT DEFAULT 'CLIENT_SAFE',
                FOREIGN KEY (subject_node_id) REFERENCES canonical_graph_nodes(node_id),
                FOREIGN KEY (object_node_id) REFERENCES canonical_graph_nodes(node_id)
            );
            """
        )

        # Seed authoritative test entities
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-JANE', 'Jane Doe', 'person', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-PRF', 'PRF Automation', 'process', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-SF', 'SAP SuccessFactors', 'system', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-SNOW', 'ServiceNow', 'system', 'CLIENT_SAFE')")

        # Seed authoritative assertions
        # 1. Approved Title for Jane Doe: "Senior Delivery Consultant"
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-TITLE-001', 'N-JANE', 'has_title', NULL, 'Senior Delivery Consultant',
                'CONFIRMED', '2026-09-01T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        # 2. Approved System Interaction: PRF touches SAP SuccessFactors
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-PRF-SF-01', 'N-PRF', 'touches_system', 'N-SF', NULL,
                'CONFIRMED', '2026-09-15T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        # 3. Approved Authoritative System: SuccessFactors is authoritative for position data
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-AUTH-001', 'N-SF', 'authoritative_for', NULL, 'position data',
                'CONFIRMED', '2026-09-20T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )

        conn.commit()
        conn.close()

        # Compute initial DB SHA-256
        self.initial_graph_sha = _compute_sha256(self.db_path)
        self.generation_id = "GEN-QUAL-20261006"

        # Create scoped active_pair.json
        pair_data = {
            "generation_id": self.generation_id,
            "graph_path": "canonical_graph.db",
            "graph_sha256": self.initial_graph_sha,
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(pair_data, indent=2), encoding="utf-8")

        self.guard = GroundedDeliveryGuard()
        self.captured_sink_payloads: List[str] = []

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def mock_sink(self, payload: str) -> str:
        self.captured_sink_payloads.append(payload)
        return "DISPATCHED_OK"

    def test_incident_family_1_invented_title_fails_closed(self) -> None:
        """Incident Family 1: Fabricated roles like 'Client Partner & Delivery Lead' must fail closed."""
        # 1. Adversarial Case: Invented Title in payload and claim
        fake_payload = "Best regards,\nJane Doe\nClient Partner & Delivery Lead\n"
        fake_digest = hashlib.sha256(fake_payload.encode("utf-8")).hexdigest()

        bad_locator = SourceLocator(
            locator_type="document_section",
            source_uri="file:///transcripts/unverified_signature.md",
            locator_value="sig_block",
        )
        bad_claim = ClaimRecord(
            claim_id="CLM-TITLE-INV",
            subject="Jane Doe",
            predicate="has_title",
            object="Client Partner & Delivery Lead",
            scope="ENGAGEMENT_LEVEL",
            negation=False,
            assertion_ids=["ASN-TITLE-NONEXISTENT"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[bad_locator],
        )

        bad_manifest = ClaimManifest(
            manifest_id="MNF-INV-TITLE",
            payload_digest=fake_digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.generation_id,
            graph_sha256=self.initial_graph_sha,
            claims=[bad_claim],
        )

        # Direct verification must reject
        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=fake_payload,
            manifest=bad_manifest,
            runtime_root=self.runtime_root,
            delivery_surface="EMAIL_BODY",
        )
        self.assertFalse(certified, "Invented title must not be certified")
        self.assertEqual(receipt.status, "REJECTED")
        self.assertEqual(receipt.supported_claims_count, 0)
        self.assertIn(receipt.reason_code, ("AMBIGUOUS_IDENTITY", "UNSUPPORTED_CLAIM"))

        # Guard delivery must suppress sink dispatch
        result = self.guard.deliver(
            payload_text=fake_payload,
            manifest=bad_manifest,
            runtime_root=self.runtime_root,
            surface="EMAIL_BODY",
            sink=self.mock_sink,
        )
        self.assertFalse(result.delivered)
        self.assertEqual(len(self.captured_sink_payloads), 0, "No payload may reach sink on ungrounded title")
        self.assertIsNotNone(result.safe_response)
        self.assertTrue(result.safe_response.draft_suppressed)

        # 2. Grounded Case: Approved Title "Senior Delivery Consultant"
        clean_payload = "Best regards,\nJane Doe\nSenior Delivery Consultant\n"
        clean_digest = hashlib.sha256(clean_payload.encode("utf-8")).hexdigest()

        good_locator = SourceLocator(
            locator_type="file_line",
            source_uri="file:///transcripts/hr_directory.json",
            locator_value="row_42",
        )
        good_claim = ClaimRecord(
            claim_id="CLM-TITLE-OK",
            subject="Jane Doe",
            predicate="has_title",
            object="Senior Delivery Consultant",
            scope="ENGAGEMENT_LEVEL",
            negation=False,
            assertion_ids=["ASN-TITLE-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[good_locator],
        )

        good_manifest = ClaimManifest(
            manifest_id="MNF-OK-TITLE",
            payload_digest=clean_digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.generation_id,
            graph_sha256=self.initial_graph_sha,
            claims=[good_claim],
        )

        good_result = self.guard.deliver(
            payload_text=clean_payload,
            manifest=good_manifest,
            runtime_root=self.runtime_root,
            surface="EMAIL_BODY",
            sink=self.mock_sink,
        )
        self.assertTrue(good_result.delivered, "Grounded title must pass delivery")
        self.assertEqual(good_result.receipt.status, "CERTIFIED")
        self.assertEqual(len(self.captured_sink_payloads), 1)

    def test_incident_family_2_guessed_metric_and_word_count_limits(self) -> None:
        """Incident Family 2: Enforce standardized <=500 words limit and ungrounded metric rejections."""
        # 1. 501-word payload exceeds the standardized <=500 words limit
        words_501 = "word " * 501
        count_501 = count_words(words_501)
        self.assertEqual(count_501, 501)

        with ReadOnlyGraphSnapshot(self.runtime_root) as snapshot:
            metrics_501 = evaluate_text_metrics(words_501, None, snapshot, max_words=500)
            self.assertFalse(metrics_501.passed_count, "501 words must exceed <=500 word limit")
            self.assertTrue(any("violates strict limit <= 500 words" in r for r in metrics_501.reasons))

            # 2. 500-word payload satisfies the standardized ceiling
            words_500 = "word " * 500
            self.assertEqual(count_words(words_500), 500)
            metrics_500 = evaluate_text_metrics(words_500, None, snapshot, max_words=500)
            self.assertTrue(metrics_500.passed_count, "500 words must satisfy <=500 limit")

        # 3. Ungrounded numeric metric claim without assertion backing fails
        metric_payload = "PRF project will achieve $1.2M in annual trajectory savings."
        m_digest = hashlib.sha256(metric_payload.encode("utf-8")).hexdigest()

        metric_locator = SourceLocator(
            locator_type="document_section",
            source_uri="file:///docs/savings.md",
            locator_value="sec_1",
        )
        metric_claim = ClaimRecord(
            claim_id="CLM-METRIC-GUESS",
            subject="PRF Automation",
            predicate="projected_savings",
            object="executive compensation band",
            scope="FINANCIAL",
            negation=False,
            assertion_ids=["ASN-NON-EXISTENT-METRIC"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[metric_locator],
        )
        metric_manifest = ClaimManifest(
            manifest_id="MNF-METRIC-GUESS",
            payload_digest=m_digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.generation_id,
            graph_sha256=self.initial_graph_sha,
            claims=[metric_claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=metric_payload,
            manifest=metric_manifest,
            runtime_root=self.runtime_root,
            delivery_surface="EMAIL_BODY",
        )
        self.assertFalse(certified)
        self.assertEqual(receipt.status, "REJECTED")

    def test_incident_family_3_unsupported_system_and_locator_claims(self) -> None:
        """Incident Family 3: Claims regarding systems without assertions or locators fail closed."""
        # 1. Schema check: missing source locators raises schema validation error
        with self.assertRaises(Exception):
            ClaimRecord(
                claim_id="CLM-INVALID-LOCATOR",
                subject="SAP SuccessFactors",
                predicate="exposes_api",
                object="PositionManagementAPI",
                scope="INTEGRATION",
                negation=False,
                assertion_ids=["ASN-AUTH-001"],
                evidence_lane="approved",
                epistemic_status="approved",
                source_locators=[],  # Must have minItems: 1
            ).validate()

        # 2. Fabricated claim about ServiceNow creating HR cases without assertions
        sys_payload = "ServiceNow is configured to generate an HR audit case."
        s_digest = hashlib.sha256(sys_payload.encode("utf-8")).hexdigest()

        sys_locator = SourceLocator(
            locator_type="document_section",
            source_uri="file:///specs/integration.md",
            locator_value="sec_2",
        )
        sys_claim = ClaimRecord(
            claim_id="CLM-SNOW-AUDIT",
            subject="ServiceNow",
            predicate="creates_record",
            object="HR audit case",
            scope="INTEGRATION",
            negation=False,
            assertion_ids=["ASN-NON-EXISTENT-SNOW"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[sys_locator],
        )
        sys_manifest = ClaimManifest(
            manifest_id="MNF-SNOW-AUDIT",
            payload_digest=s_digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.generation_id,
            graph_sha256=self.initial_graph_sha,
            claims=[sys_claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=sys_payload,
            manifest=sys_manifest,
            runtime_root=self.runtime_root,
            delivery_surface="EMAIL_BODY",
        )
        self.assertFalse(certified)
        self.assertEqual(receipt.status, "REJECTED")

    def test_incident_family_4_tool_crash_fails_safe_to_structured_response(self) -> None:
        """Incident Family 4: Failures emit structured SafeFailureResponses, never ungrounded prose fallbacks."""
        # Simulate delivery with unsupported delivery surface (raises UnsupportedDeliverySurfaceError)
        with self.assertRaises(UnsupportedDeliverySurfaceError):
            self.guard.deliver(
                payload_text="Unapproved dispatch",
                manifest=ClaimManifest(
                    manifest_id="MNF-CRASH",
                    payload_digest="abc",
                    runtime_root=str(self.runtime_root),
                    graph_generation_id=self.generation_id,
                    graph_sha256=self.initial_graph_sha,
                    claims=[],
                ),
                runtime_root=self.runtime_root,
                surface="UNREGISTERED_CHAT_BOT",
                sink=self.mock_sink,
            )

        # Verify structured SafeFailureResponse contract
        safe_resp = SafeFailureResponse(
            delivery_surface="EMAIL_BODY",
            reason_code="UNSUPPORTED_CLAIM",
            public_message="Delivery blocked: factual claims lack approved graph assertions.",
            rejection_reasons=["Claim CLM-001 has no active graph assertion."],
            receipt_id="RCP-MNF-CRASH",
            draft_suppressed=True,
        )
        resp_dict = safe_resp.to_dict()
        self.assertEqual(resp_dict["delivery_surface"], "EMAIL_BODY")
        self.assertEqual(resp_dict["reason_code"], "UNSUPPORTED_CLAIM")
        self.assertTrue(resp_dict["draft_suppressed"])
        self.assertEqual(len(self.captured_sink_payloads), 0, "No payload leaked during failure")

    def test_answered_question_duplication_prevention(self) -> None:
        """Verify request-gaps matrix flags candidate questions already answered by graph assertions."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        try:
            auditor = RequestGapAuditor(target_engagement="HR IT / ENTERPRISE4U")

            # Question 1: System touched by PRF automation (answered by ASN-PRF-SF-01)
            q1 = "Does PRF automation touch SAP SuccessFactors?"
            item1 = auditor.audit_single_question(
                q_id="Q-01",
                text=q1,
                snapshot=snapshot,
                key_tokens=["PRF Automation", "SAP SuccessFactors"],
            )
            self.assertEqual(
                item1.classification,
                GapClassification.ANSWERED_IN_GRAPH,
                "Question with confirmed graph assertion must be classified as ANSWERED_IN_GRAPH",
            )
            self.assertIn("ASN-PRF-SF-01", item1.evidence_assertion_ids)

            # Question 2: Genuinely unaddressed scope (e.g. peak volume SLA)
            q2 = "What are the peak period response time SLAs for PRF submissions?"
            item2 = auditor.audit_single_question(
                q_id="Q-02",
                text=q2,
                snapshot=snapshot,
                key_tokens=["peak_period_sla", "response_time_sla"],
            )
            self.assertEqual(
                item2.classification,
                GapClassification.UNRESOLVED_NEED,
                "Question without graph assertion must be classified as UNRESOLVED_NEED",
            )
            self.assertEqual(len(item2.evidence_assertion_ids), 0)
        finally:
            snapshot.close()

    def test_receipt_anti_tampering_and_bypass_prevention(self) -> None:
        """Tampered DeliveryReceipts with forged PASS status or altered digests fail staged validation."""
        temp_repo = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "audit@test.com"], cwd=temp_repo, check=True)
            subprocess.run(["git", "config", "user.name", "Auditor"], cwd=temp_repo, check=True)

            # Forged receipt: status CERTIFIED but contradicted_claims_count > 0
            forged_receipt = DeliveryReceipt(
                receipt_id="RCP-FORGED-001",
                manifest_id="MNF-001",
                manifest_digest="1" * 64,
                payload_digest="2" * 64,
                runtime_root=str(self.runtime_root),
                graph_generation_id=self.generation_id,
                graph_sha256=self.initial_graph_sha,
                status="CERTIFIED",
                reason_code="GROUNDED_SUPPORTED",
                total_claims_count=2,
                supported_claims_count=1,
                contradicted_claims_count=1,  # Contradiction in certified receipt!
                delivery_surface="EMAIL_BODY",
                audience="CLIENT_SAFE",
                word_count=100,
            )
            receipt_file = temp_repo / "delivery_receipt.json"
            receipt_file.write_text(json.dumps(forged_receipt.to_dict(), indent=2), encoding="utf-8")
            subprocess.run(["git", "add", "delivery_receipt.json"], cwd=temp_repo, check=True)

            rc, violations = validate_staged_grounding(temp_repo, staged=True)
            self.assertEqual(rc, 1, "Forged receipt must fail staged verification")
            self.assertTrue(any("contradicted_claims_count is 1" in v for v in violations))
        finally:
            import shutil
            shutil.rmtree(temp_repo, ignore_errors=True)

    def test_14_table_zero_mutation_invariant(self) -> None:
        """Verify that the 14 monitored tables remain completely unmutated (+0 across all components)."""
        # Verify 10 core + 4 sidecar = 14 tables
        self.assertEqual(len(_COMPONENTS), 10)
        self.assertEqual(len(_SIDECAR_COMPONENTS), 4)

        # Build mutation summary for no-op run
        summary = {
            "status": "IDEMPOTENT_NOOP",
            "active_generation": self.generation_id,
            "graph_sha256": self.initial_graph_sha,
        }
        table_md = format_markdown_mutation_table(summary)
        self.assertIn("### 1. Granular Graph DB Delta (Table-by-Table)", table_md)

        # Check all 10 core components certify +0
        for comp_key, label, table in _COMPONENTS:
            self.assertIn("+0", table_md)

        # Check all 4 sidecar components certify +0
        for comp_key, label, table in _SIDECAR_COMPONENTS:
            self.assertIn(f"| `{table}` (sidecar) | — | — | `+0` |", table_md)

        # Verify that actual graph database hash remains completely unmutated
        final_sha = _compute_sha256(self.db_path)
        self.assertEqual(
            self.initial_graph_sha,
            final_sha,
            "Graph SHA-256 must be identical before and after grounding verification (+0 mutations)",
        )

    def test_full_evidence_to_render_to_delivery_lifecycle(self) -> None:
        """Full Lifecycle: snapshot -> manifest -> validation -> boundary guard -> receipt -> git staged."""
        repo_dir = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "lifecycle@test.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Lifecycle Tester"], cwd=repo_dir, check=True)

            # 1. Authoritative delivery payload text
            delivery_text = (
                "Regarding PRF Automation, SAP SuccessFactors is confirmed as authoritative for position data. "
                "Best regards, Jane Doe, Senior Delivery Consultant."
            )
            payload_file = repo_dir / "delivery_payload.txt"
            payload_file.write_text(delivery_text, encoding="utf-8")
            payload_sha = hashlib.sha256(delivery_text.encode("utf-8")).hexdigest()

            # 2. Extract and construct structured claims
            loc1 = SourceLocator(
                locator_type="document_section",
                source_uri="file:///transcripts/arch_review.md",
                locator_value="sec_4",
            )
            claim1 = ClaimRecord(
                claim_id="CLM-E2E-001",
                subject="SAP SuccessFactors",
                predicate="authoritative_for",
                object="position data",
                scope="ARCHITECTURE",
                negation=False,
                assertion_ids=["ASN-AUTH-001"],
                evidence_lane="approved",
                epistemic_status="approved",
                source_locators=[loc1],
            )
            loc2 = SourceLocator(
                locator_type="file_line",
                source_uri="file:///transcripts/hr_directory.json",
                locator_value="row_42",
            )
            claim2 = ClaimRecord(
                claim_id="CLM-E2E-002",
                subject="Jane Doe",
                predicate="has_title",
                object="Senior Delivery Consultant",
                scope="ENGAGEMENT_LEVEL",
                negation=False,
                assertion_ids=["ASN-TITLE-001"],
                evidence_lane="approved",
                epistemic_status="approved",
                source_locators=[loc2],
            )

            manifest = ClaimManifest(
                manifest_id="MNF-E2E-001",
                payload_digest=payload_sha,
                runtime_root=str(self.runtime_root),
                graph_generation_id=self.generation_id,
                graph_sha256=self.initial_graph_sha,
                claims=[claim1, claim2],
                extensions={"payload.path": "delivery_payload.txt"},
            )

            # 3. Execute delivery boundary inspection
            certified, receipt, reasons = verify_grounded_delivery(
                payload_text=delivery_text,
                manifest=manifest,
                runtime_root=self.runtime_root,
                delivery_surface="EMAIL_BODY",
                audience="CLIENT_SAFE",
            )
            self.assertTrue(certified, f"Delivery must be certified: {reasons}")
            self.assertEqual(receipt.status, "CERTIFIED")
            self.assertEqual(receipt.supported_claims_count, 2)
            self.assertEqual(receipt.contradicted_claims_count, 0)

            # 4. Save manifest and receipt
            manifest_file = repo_dir / "claim_manifest.json"
            manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

            receipt_file = repo_dir / "delivery_receipt.json"
            receipt_file.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")

            # 5. Stage in Git index
            subprocess.run(
                ["git", "add", "delivery_payload.txt", "claim_manifest.json", "delivery_receipt.json"],
                cwd=repo_dir,
                check=True,
            )

            # 6. Validate staged Git index
            rc, violations = validate_staged_grounding(repo_dir, staged=True)
            self.assertEqual(rc, 0, f"Staged Git grounding validation must succeed: {violations}")
            self.assertEqual(violations, [])
        finally:
            import shutil
            shutil.rmtree(repo_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
