"""
tests/end_to_end/test_fail_closed_boundaries_e2e.py

End-to-End lifecycle tests verifying fail-closed isolation across:
1. E2E Outbound Dispatch Perimeter: Verifies full completion call lifecycle halts before outbound socket.
2. E2E Copilot Meeting Feedback Perimeter: Verifies live meeting turns and suggestions sanitize C-19 and reject secrets.
3. E2E Multi-Tenant Graph Retrieval Perimeter: Verifies cross-tenant isolation and fail-closed unscoped access.
4. E2E Governed MCP Server Perimeter: Verifies end-to-end isolation for database tools and judge evaluation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_validator import validate_copilot_feedback
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer
from dual_pipeline.transcript_stages_core.completion_call import _one_call
from tools.mcp_graphdb_server import graphdb_read_query
from tools.mcp_judge_eval_core.rubrics import judge_evaluate_candidate


class TestFailClosedBoundariesE2E(unittest.TestCase):
    """End-to-end lifecycle verification of fail-closed boundaries."""

    def test_e2e_llm_dispatch_lifecycle_aborts_on_secret(self) -> None:
        """Verify end-to-end that outbound LLM completion calls abort prior to provider execution."""
        mock_provider = MagicMock()
        mock_recorder = MagicMock()
        mock_usage = MagicMock()

        # Adversarial payload with OpenAI project key
        adversarial_kwargs = {
            "model": "gpt-5.6-sol",
            "messages": [
                {"role": "system", "content": "You are a decision intelligence engine."},
                {"role": "user", "content": "Authorization: Bearer sk_test_redacted_placeholder"},
            ],
        }

        with self.assertRaises(EgressSanitizationError):
            _one_call(
                provider=mock_provider,
                effort="medium",
                kwargs=adversarial_kwargs,
                deadline=30.0,
                recorder=mock_recorder,
                model="gpt-5.6-sol",
                batch=False,
                started=0.0,
                record_usage=mock_usage,
            )

        # Invariant: Outbound provider must NEVER have been called
        mock_provider.chat.completions.create.assert_not_called()
        mock_recorder.record.assert_not_called()

    def test_e2e_copilot_feedback_lifecycle_defense(self) -> None:
        """Verify end-to-end live meeting copilot cleans C-19 and drops secret-bearing advice."""
        live_turns = [
            {"id": "turn_101", "speaker": "Alex", "text": "Let's review the cloud migration timeline and budget."},
            {"id": "turn_102", "speaker": "Jordan", "text": "We need to ensure AWS security standards are met."},
        ]

        # Scenario 1: Model attempts to emit an AWS credential in copilot guidance
        tainted_model_output = {
            "has_guidance": True,
            "suggested_script": "Configure the cluster using root access key AKIA_REDACTED_EXAMPLE.",
            "snap_cue": "Security credentials",
            "claims": [],
        }

        result_secret = validate_copilot_feedback(
            tainted_model_output,
            live_turns=live_turns,
            valid_assertion_ids=[],
        )
        # Fail-closed: Must return None, zero advice sent to user
        self.assertIsNone(result_secret)

        # Scenario 2: Model emits career leverage and political advice mixed with valid recommendation
        c19_model_output = {
            "has_guidance": True,
            "suggested_script": "Leverage this migration to secure personal promotion and executive compensation band.",
            "snap_cue": "Career leverage opportunity",
            "speaker_summary_quotes": [
                "Alex asked about migration budget.",
                "Targeting compensation trajectory leverage.",
            ],
            "supporting_details_bullets": [
                "Establish political power map across engineering leads.",
                "Adopt infrastructure-as-code for audit compliance.",
            ],
            "claims": [],
        }

        result_c19 = validate_copilot_feedback(
            c19_model_output,
            live_turns=live_turns,
            valid_assertion_ids=[],
        )

        self.assertIsNotNone(result_c19)
        # Verify script replaced with safe executive fallback
        self.assertNotIn("1.2M", result_c19["suggested_script"])
        self.assertNotIn("promotion", result_c19["suggested_script"])
        self.assertIn("verify technical delivery ownership", result_c19["suggested_script"])

        # Verify bullets scrubbed: safe bullet kept, political bullet removed
        bullets = result_c19.get("supporting_details_bullets", [])
        self.assertTrue(any("compliance" in b for b in bullets))
        self.assertFalse(any("political" in b for b in bullets))

    def test_e2e_multitenant_retrieval_isolation_lifecycle(self) -> None:
        """Verify strict isolation between tenants across scoped and unscoped graph retrieval cycles."""
        nodes: Dict[str, Any] = {
            "project:acme": {
                "canonical_name": "Project ACME",
                "node_type": "project",
                "engagement_id": "project:acme",
                "namespace": "CANONICAL",
            },
            "project:globex": {
                "canonical_name": "Project Globex",
                "node_type": "project",
                "engagement_id": "project:globex",
                "namespace": "CANONICAL",
            },
            "system:kubernetes": {
                "canonical_name": "Production K8s",
                "node_type": "system",
                "namespace": "CANONICAL",
            },
            "policy:global_audit": {
                "canonical_name": "Enterprise Audit Standard",
                "node_type": "policy",
                "is_enterprise_policy": True,
                "namespace": "ENTERPRISE_POLICY",
            },
        }

        assertions: Dict[str, Any] = {
            "asrt_acme_k8s": {
                "assertion_id": "asrt_acme_k8s",
                "subject_node_id": "project:acme",
                "predicate": "DEPLOYS",
                "object_node_id": "system:kubernetes",
                "claim_text": "ACME deploys internal proprietary financial engine to Production K8s.",
                "engagement_id": "project:acme",
                "canonical_status": "ACTIVE",
            },
            "asrt_globex_k8s": {
                "assertion_id": "asrt_globex_k8s",
                "subject_node_id": "project:globex",
                "predicate": "DEPLOYS",
                "object_node_id": "system:kubernetes",
                "claim_text": "Globex deploys proprietary healthcare analytics engine to Production K8s.",
                "engagement_id": "project:globex",
                "canonical_status": "ACTIVE",
            },
            "asrt_audit_policy": {
                "assertion_id": "asrt_audit_policy",
                "subject_node_id": "policy:global_audit",
                "predicate": "MANDATES",
                "object_node_id": "system:kubernetes",
                "claim_text": "Enterprise Audit Standard mandates immutable audit logging for Production K8s.",
                "is_enterprise_policy": True,
                "canonical_status": "ACTIVE",
            },
        }

        mock_topology = SimpleNamespace(
            nodes=nodes,
            assertions=assertions,
            forward_adjacency={
                "project:acme": [{"to_node": "system:kubernetes", "relationship_family": "DEPLOYS"}],
                "project:globex": [{"to_node": "system:kubernetes", "relationship_family": "DEPLOYS"}],
            },
        )

        # 1. ACME Scoped Session
        acme_retriever = CopilotGraphRetriever(
            topology=mock_topology,
            engagement_id="project:acme",
            session_id="acme-meeting-01",
        )
        acme_results = [r["assertion_id"] for r in acme_retriever.retrieve_structured_assertions("Production K8s", max_results=10)]
        self.assertIn("asrt_acme_k8s", acme_results)
        self.assertIn("asrt_audit_policy", acme_results)
        self.assertNotIn("asrt_globex_k8s", acme_results, "Cross-tenant leakage: ACME session saw Globex claims")

        # 2. Globex Scoped Session
        globex_retriever = CopilotGraphRetriever(
            topology=mock_topology,
            engagement_id="project:globex",
            session_id="globex-meeting-02",
        )
        globex_results = [r["assertion_id"] for r in globex_retriever.retrieve_structured_assertions("Production K8s", max_results=10)]
        self.assertIn("asrt_globex_k8s", globex_results)
        self.assertIn("asrt_audit_policy", globex_results)
        self.assertNotIn("asrt_acme_k8s", globex_results, "Cross-tenant leakage: Globex session saw ACME claims")

        # 3. Unscoped Session (engagement_id=None)
        unscoped_retriever = CopilotGraphRetriever(
            topology=mock_topology,
            engagement_id=None,
            session_id="unscoped-meeting-03",
        )
        unscoped_results = [r["assertion_id"] for r in unscoped_retriever.retrieve_structured_assertions("Production K8s", max_results=10)]
        self.assertIn("asrt_audit_policy", unscoped_results, "Enterprise policy claims should be accessible")
        self.assertNotIn("asrt_acme_k8s", unscoped_results, "Unscoped session leaked ACME proprietary claims")
        self.assertNotIn("asrt_globex_k8s", unscoped_results, "Unscoped session leaked Globex proprietary claims")

    def test_e2e_mcp_perimeter_isolation_lifecycle(self) -> None:
        """Verify end-to-end that MCP queries and evaluation tools enforce hermetic containment."""
        # 1. MCP GraphDB Server: Denies shadow graph queries
        res_shadow = graphdb_read_query("SELECT * FROM shadow_graph_nodes LIMIT 5")
        self.assertIn("Permission denied", res_shadow)

        # 2. MCP LLM Judge Evaluation: Fails closed on tainted candidate artifacts
        tainted_candidate = {
            "recap": "Executive alignment on infrastructure.",
            "notes": "Targeting executive compensation band and political leverage.",
        }
        res_judge = json.loads(
            judge_evaluate_candidate(
                rubric_id="executive_recap",
                candidate_text=json.dumps(tainted_candidate),
            )
        )
        self.assertEqual(res_judge["verdict"], "FAIL")
        self.assertEqual(res_judge["confidence_score"], 0.0)
        self.assertTrue(any("Rule C-19" in err for err in res_judge["errors"]))


if __name__ == "__main__":
    unittest.main()
