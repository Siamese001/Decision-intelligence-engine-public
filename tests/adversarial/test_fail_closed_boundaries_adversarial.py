"""
tests/adversarial/test_fail_closed_boundaries_adversarial.py

Adversarial boundary verification suite covering:
1. Boundary Payloads: Empty, whitespace, null, and oversized inputs across sanitizers and validators.
2. Malformed Inputs: Missing schema keys, truncated dictionaries, invalid path traversal.
3. Invariant Bypass Attempts:
   - Modern API keys (OpenAI sk-proj, Anthropic sk-ant, AWS AKIA, GitHub PAT, RSA Private Keys).
   - Rule C-19 Career Leverage leakage attempts across multi-field copilot payloads.
   - MCP GraphDB query interception against shadow tables and quarantined advisory nodes.
   - LLM Judge candidate evaluation pre-checks intercepting tainted proposals.
   - Unscoped cross-tenant assertion leakage in CopilotGraphRetriever.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_validator import validate_copilot_feedback
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer
from tools.mcp_graphdb_server import _is_safe_db_path, graphdb_read_query
from tools.mcp_judge_eval_core.rubrics import judge_evaluate_candidate


class TestFailClosedBoundariesAdversarial(unittest.TestCase):
    """Adversarial and boundary tests across LLM, MCP, Copilot, and Retrieval perimeters."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # -------------------------------------------------------------------------
    def test_boundary_empty_and_whitespace_sanitization(self) -> None:
        """Verify empty and whitespace-only payloads pass cleanly without false positive errors."""
        empty_messages = []
        LocalPreEgressSanitizer.sanitize_messages(empty_messages)

        whitespace_messages = [
            {"role": "system", "content": "   \n\t  "},
            {"role": "user", "content": ""},
        ]
        LocalPreEgressSanitizer.sanitize_messages(whitespace_messages)

        self.assertEqual(LocalPreEgressSanitizer.sanitize_text(""), "")
        self.assertEqual(LocalPreEgressSanitizer.sanitize_text("   \n\t  "), "   \n\t  ")

    def test_boundary_empty_copilot_validator_payloads(self) -> None:
        """Verify empty, None, and partial payloads in copilot validator fail closed."""
        self.assertIsNone(validate_copilot_feedback(None, valid_assertion_ids=[]))
        self.assertIsNone(validate_copilot_feedback({}, valid_assertion_ids=[]))
        self.assertIsNone(validate_copilot_feedback({"has_guidance": False}, valid_assertion_ids=[]))

    def test_boundary_oversized_string_sanitization(self) -> None:
        """Verify sanitization handles large string payloads (>500KB) deterministically."""
        clean_large_text = "Standard enterprise architecture governance documentation. " * 10000
        # Should not raise
        sanitized = LocalPreEgressSanitizer.sanitize_text(clean_large_text)
        self.assertEqual(len(sanitized), len(clean_large_text))

        # Embedded secret in oversized payload must be detected
        tainted_large_text = clean_large_text + " sk_test_redacted_placeholder"
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_text(tainted_large_text)

    # -------------------------------------------------------------------------
    # Category 2: Malformed Inputs & Path Traversal
    # -------------------------------------------------------------------------
    def test_malformed_path_traversal_blocked_in_graphdb_mcp(self) -> None:
        """Verify absolute and relative directory traversal paths are blocked in MCP server."""
        self.assertFalse(_is_safe_db_path("/etc/passwd"))
        self.assertFalse(_is_safe_db_path("../../../../../etc/shadow"))
        self.assertFalse(_is_safe_db_path("/root/.ssh/id_rsa"))

        # Temp directory paths and workspace-relative paths must be accepted
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            self.assertTrue(_is_safe_db_path(tmp.name))
            self.assertTrue(_is_safe_db_path(Path(tmp.name)))

    def test_malformed_copilot_feedback_schema(self) -> None:
        """Verify malformed nested fields do not crash validator and sanitize cleanly."""
        malformed_feedback = {
            "has_guidance": True,
            "claims": "not-a-list",  # wrong type
            "supporting_details_bullets": 12345,  # wrong type
            "speaker_summary_quotes": None,
            "suggested_script": "Strategic update on system architecture.",
        }
        res = validate_copilot_feedback(malformed_feedback, valid_assertion_ids=[])
        self.assertIsNotNone(res)
        self.assertEqual(res.get("claims"), [])

    # -------------------------------------------------------------------------
    # Category 3: Invariant Bypass Attempts & Secret Leakage
    # -------------------------------------------------------------------------
    def test_bypass_attempt_modern_api_keys_in_outbound_llm(self) -> None:
        """Verify all modern API secret formats are blocked fail-closed."""
        adversarial_keys = [
            "sk_test_redacted_placeholder",
            "sk_test_redacted_placeholder",
            "AKIA_REDACTED_EXAMPLE",
            "ghp_redacted_placeholder",
            "ghp_redacted_placeholder",
            "REDACTED_PRIVATE_KEY_HEADER\nMIIEowIBAAKCAQEA0...",
            "Bearer ya29.a0AfH6SMD_test_google_oauth_token",
        ]

        for secret in adversarial_keys:
            messages = [{"role": "user", "content": f"Here is the token: {secret}"}]
            with self.assertRaises(EgressSanitizationError, msg=f"Failed to intercept: {secret}"):
                LocalPreEgressSanitizer.sanitize_messages(messages)

    def test_bypass_attempt_c19_multi_field_copilot_scrubbing(self) -> None:
        """Verify Rule C-19 terms across quotes, bullets, questions, and claims are scrubbed."""
        tainted_feedback = {
            "has_guidance": True,
            "suggested_script": "We should focus on team delivery rather than personal advancement.",
            "speaker_summary_quotes": [
                "Speaker_Beta mentioned targeting a executive compensation band this fiscal year.",
                "Safe quote regarding roadmap priorities.",
            ],
            "supporting_details_bullets": [
                "Leverage political power map to influence promotion committee.",
                "Deliver core enterprise milestones on time.",
            ],
            "insightful_questions": [
                "How does this impact personal promotion and career leverage?",
                "What are the architecture trade-offs?",
            ],
            "claims": [
                {
                    "role": "FACT",
                    "source_kind": "GRAPH_ASSERTION",
                    "source_ref": "asrt_clean",
                    "verbatim_span": "Deliver core enterprise milestones on time.",
                }
            ],
        }

        scrubbed = validate_copilot_feedback(tainted_feedback, valid_assertion_ids=["asrt_clean"])
        self.assertIsNotNone(scrubbed)

        # Quotes: C-19 quote should be removed, safe quote preserved
        quotes = scrubbed.get("speaker_summary_quotes", "")
        self.assertIn("Safe quote", quotes)
        self.assertNotIn("1.2M", quotes)
        self.assertNotIn("trajectory", quotes)

        # Bullets: C-19 bullet should be removed, safe bullet preserved
        bullets = scrubbed.get("supporting_details_bullets", [])
        self.assertEqual(len(bullets), 1)
        self.assertIn("Deliver core enterprise milestones", bullets[0])

        # Questions: C-19 question should be removed, safe question preserved
        questions = scrubbed.get("insightful_questions", [])
        self.assertEqual(len(questions), 1)
        self.assertIn("architecture trade-offs", questions[0])

    def test_bypass_attempt_secret_in_copilot_feedback_fails_closed(self) -> None:
        """Verify any secret pattern emitted in copilot feedback causes complete fail-closed rejection."""
        secret_feedback = {
            "has_guidance": True,
            "suggested_script": "Use deployment key sk_test_redacted_placeholder for setup.",
            "speaker_summary_quotes": [],
            "supporting_details_bullets": [],
            "claims": [],
        }
        res = validate_copilot_feedback(secret_feedback, valid_assertion_ids=[])
        self.assertIsNone(res, "Feedback with embedded API key must fail closed and return None")

    def test_bypass_attempt_shadow_graph_queries_blocked_in_graphdb_mcp(self) -> None:
        """Verify graphdb_read_query blocks shadow graph inspection and stealth score extraction."""
        prohibited_queries = [
            "SELECT * FROM shadow_graph_nodes",
            "SELECT node_id, stealth_score FROM canonical_graph_nodes",
            "SELECT * FROM canonical_graph_nodes WHERE namespace = 'INTERNAL_ADVISORY'",
            "select * from SHADOW_GRAPH_NODES where id = 1",
        ]

        for query in prohibited_queries:
            res = graphdb_read_query(query=query)
            self.assertIn("Permission denied", res, f"Query '{query}' was not blocked")

    def test_bypass_attempt_llm_judge_eval_fails_closed_on_secret_or_c19(self) -> None:
        """Verify judge_evaluate_candidate returns FAIL and 0.0 confidence on tainted proposals."""
        # Tainted with modern secret
        raw_secret = judge_evaluate_candidate(
            rubric_id="forensic_fidelity",
            candidate_text="Candidate with embedded secret: AKIA_REDACTED_EXAMPLE",
        )
        res_secret = json.loads(raw_secret)
        self.assertEqual(res_secret["verdict"], "FAIL")
        self.assertEqual(res_secret["confidence_score"], 0.0)
        self.assertTrue(any("Credential leak" in e or "Secret" in e for e in res_secret.get("errors", [])))

        # Tainted with C-19 career leverage
        raw_c19 = judge_evaluate_candidate(
            rubric_id="forensic_fidelity",
            candidate_text="Candidate tracks executive compensation band and executive political leverage.",
        )
        res_c19 = json.loads(raw_c19)
        self.assertEqual(res_c19["verdict"], "FAIL")
        self.assertEqual(res_c19["confidence_score"], 0.0)
        self.assertTrue(any("Rule C-19" in e for e in res_c19.get("errors", [])))

    def test_bypass_attempt_unscoped_retrieval_blocks_cross_tenant_claims(self) -> None:
        """Verify CopilotGraphRetriever blocks client-specific claims when engagement_id is unbound."""
        nodes: Dict[str, Any] = {
            "project:tenant_alpha": {
                "canonical_name": "Project Alpha",
                "node_type": "project",
                "engagement_id": "project:tenant_alpha",
                "namespace": "CANONICAL",
            },
            "project:tenant_beta": {
                "canonical_name": "Project Beta",
                "node_type": "project",
                "engagement_id": "project:tenant_beta",
                "namespace": "CANONICAL",
            },
            "system:shared_crm": {
                "canonical_name": "Shared CRM",
                "node_type": "system",
                "namespace": "CANONICAL",
            },
            "policy:security_baseline": {
                "canonical_name": "Enterprise Security Baseline",
                "node_type": "policy",
                "is_enterprise_policy": True,
                "namespace": "ENTERPRISE_POLICY",
            },
        }

        assertions: Dict[str, Any] = {
            "asrt_alpha_crm": {
                "assertion_id": "asrt_alpha_crm",
                "subject_node_id": "project:tenant_alpha",
                "predicate": "USES",
                "object_node_id": "system:shared_crm",
                "claim_text": "Tenant Alpha uses Shared CRM with proprietary custom fields.",
                "engagement_id": "project:tenant_alpha",
                "canonical_status": "ACTIVE",
            },
            "asrt_beta_crm": {
                "assertion_id": "asrt_beta_crm",
                "subject_node_id": "project:tenant_beta",
                "predicate": "USES",
                "object_node_id": "system:shared_crm",
                "claim_text": "Tenant Beta integrates proprietary confidential billing into CRM.",
                "engagement_id": "project:tenant_beta",
                "canonical_status": "ACTIVE",
            },
            "asrt_enterprise_policy": {
                "assertion_id": "asrt_enterprise_policy",
                "subject_node_id": "policy:security_baseline",
                "predicate": "MANDATES",
                "object_node_id": "system:shared_crm",
                "claim_text": "Enterprise Security Baseline mandates MFA on all CRM endpoints.",
                "is_enterprise_policy": True,
                "canonical_status": "ACTIVE",
            },
        }

        mock_graph = SimpleNamespace(
            nodes=nodes,
            assertions=assertions,
            forward_adjacency={
                "project:tenant_alpha": [{"to_node": "system:shared_crm", "relationship_family": "USES"}],
                "project:tenant_beta": [{"to_node": "system:shared_crm", "relationship_family": "USES"}],
            },
        )

        # Unscoped retriever (engagement_id=None)
        unscoped_retriever = CopilotGraphRetriever(
            topology=mock_graph,
            engagement_id=None,
            session_id="unscoped-adv-session",
        )

        results = unscoped_retriever.retrieve_structured_assertions("Shared CRM configurations", max_results=10)
        returned_ids = [r["assertion_id"] for r in results]

        # Invariant: Proprietary claims from Alpha and Beta MUST NOT leak when unscoped
        self.assertNotIn("asrt_alpha_crm", returned_ids)
        self.assertNotIn("asrt_beta_crm", returned_ids)

        # Invariant: Enterprise policy claims ARE allowed when unscoped
        self.assertIn("asrt_enterprise_policy", returned_ids)


if __name__ == "__main__":
    unittest.main()
