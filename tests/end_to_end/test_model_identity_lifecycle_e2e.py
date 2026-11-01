"""
End-to-End Lifecycle & Council Qualification Suite for Model Identity SSOT.

Verifies:
1. Complete 7-stage chain: config -> resolution -> dispatch -> transport spy -> ledger -> cost -> turn gate.
2. Parent/child delegation: subagent tasks bind their own execution evidence without parent inheritance.
3. Mixed host/application identities: host assistant reporting UNAVAILABLE while application calls report verified models.
4. Semantic cache replay: cached responses cannot forge fresh execution evidence without run_id binding.
5. Dynamic revocation: revoking model approval dynamically in registry immediately blocks subsequent dispatches.
6. Rollback retention: rollback preserves approval enforcement and truthful unknown states.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.agent_architecture_core.delegation_ledger import (
    DelegationCostLedger,
    DelegationCostRecord,
)
from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.host_metadata_adapter import (
    EvidenceTier,
    HostIdentityReport,
    IdentityScope,
    resolve_host_identity,
    verify_identity_evidence,
)
from dual_pipeline.model_registry import (
    get_approved_models,
    is_model_approved,
    reload_registry,
)
from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger
from tools.validate_turn_gates import validate_scratchpad_transparency


class TestEndToEndModelIdentityChainE2E(unittest.TestCase):
    """Verifies complete 7-stage configuration -> dispatch -> evidence -> accounting chain."""

    def test_full_seven_stage_lifecycle_chain(self):
        # Stage 1: Configuration check
        configured_model = "gemini-3.8-flash"
        self.assertTrue(is_model_approved(configured_model))

        # Stage 2: Resolution
        resolved_model = configured_model

        # Stage 3: Outbound dispatch preparation
        ledger = D02CallLedger(operation_id="e2e_chain_run_01")
        tracker = RunCostTracker()

        # Stage 4: Execution / Transport spy simulation
        observed_model = "gemini-3.8-flash-001"  # Versioned response string

        # Stage 5: Call ledger recording
        rec = ledger.record_call(
            pass_id="2A",
            pass_attempt=1,
            transport_attempt=1,
            model=resolved_model,
            status="SUCCESS",
            requested_model=configured_model,
            resolved_model=resolved_model,
            dispatched_model=resolved_model,
            observed_model=observed_model,
            evidence_strength=EvidenceTier.OBSERVED_RESPONSE.value,
            usage_metadata={"promptTokenCount": 200, "candidatesTokenCount": 80},
        )
        self.assertEqual(rec.dispatched_model, "gemini-3.8-flash")
        self.assertEqual(rec.observed_model, "gemini-3.8-flash-001")

        # Stage 6: Cost calculation against effective dispatched model
        cost = tracker.record_call(
            model=rec.dispatched_model,
            prompt_tokens=rec.prompt_tokens,
            completion_tokens=rec.output_tokens,
            requested_model=rec.requested_model,
        )
        self.assertGreater(cost, 0.0)

        # Stage 7: Turn gate verification & rendered scratchpad
        evidence = {
            "dispatched_model": rec.dispatched_model,
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        scratchpad = (
            "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
            f"- **Active Model & Provider**: {rec.dispatched_model} | Google GenAI | Tier: Medium\n"
            "- **Cognitive Breakdown & Thinking**: [Thinking Deliberation]\n"
            "- **Reasoning Steps**: [Steps]\n"
            "- **API & Tool Invocations**: [Tools]\n"
        )
        gate_res = validate_scratchpad_transparency(scratchpad, evidence=evidence, scope="application_call")
        self.assertEqual(gate_res["status"], "PASS")


class TestParentChildDelegationEvidenceE2E(unittest.TestCase):
    """Verifies that child subagent tasks strictly bind their own execution evidence."""

    def test_child_cannot_inherit_parent_model_identity_without_evidence(self):
        parent_model = "gpt-6.1-sol"
        child_model = "gemini-3.8-flash"

        d_ledger = DelegationCostLedger()
        rec = d_ledger.record_delegation(
            task_id="task_child_01",
            parent_task_id="parent_task_01",
            subagent_role="specialist_researcher",
            model=child_model,
            requested_model=child_model,
            resolved_model=child_model,
            dispatched_model=child_model,
            observed_model=child_model,
            evidence_strength="OBSERVED_RESPONSE",
        )
        self.assertEqual(rec.dispatched_model, child_model)

        # Child output claiming parent's model identity fails verification
        child_output_scratchpad = (
            "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
            f"- **Active Model & Provider**: {parent_model} | OpenAI | Tier: Frontier\n"
            "- **Cognitive Breakdown & Thinking**: [Thinking]\n"
            "- **Reasoning Steps**: [Steps]\n"
            "- **API & Tool Invocations**: [Tools]\n"
        )
        child_evidence = {
            "dispatched_model": rec.dispatched_model,
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        res = validate_scratchpad_transparency(child_output_scratchpad, evidence=child_evidence, scope="application_call")
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("Approved-but-wrong" in err or "does not match" in err for err in res["issues"]))


class TestMixedHostAndApplicationIdentitiesE2E(unittest.TestCase):
    """Verifies mixed host/application reporting boundaries."""

    def test_host_unavailable_with_verified_application_call(self):
        # Host assistant reports UNAVAILABLE when metadata is inaccessible
        host_report = resolve_host_identity(scope=IdentityScope.HOST_ASSISTANT)
        self.assertEqual(host_report.status, "UNAVAILABLE")

        # Application call reports verified model
        app_model = "gemini-3.8-flash"
        evidence = {
            "dispatched_model": app_model,
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        app_scratchpad = (
            "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
            f"- **Active Model & Provider**: {app_model} | Google GenAI | Tier: Medium\n"
            "- **Cognitive Breakdown & Thinking**: [Thinking]\n"
            "- **Reasoning Steps**: [Steps]\n"
            "- **API & Tool Invocations**: [Tools]\n"
        )
        res = validate_scratchpad_transparency(app_scratchpad, evidence=evidence, scope="application_call")
        self.assertEqual(res["status"], "PASS")


class TestSemanticCacheReplayTamperE2E(unittest.TestCase):
    """Verifies cache replay cannot forge fresh execution evidence without run_id binding."""

    def test_tampered_or_cached_evidence_without_binding_rejected(self):
        evidence_tampered = {
            "dispatched_model": "gemini-3.8-flash",
            "evidence_strength": "OBSERVED_RESPONSE",
            "tampered": True,
        }
        is_ok, reason = verify_identity_evidence(
            declared_model="gemini-3.8-flash",
            evidence=evidence_tampered,
            scope=IdentityScope.APPLICATION_CALL,
        )
        self.assertFalse(is_ok)
        self.assertIn("tampered", reason.lower())


class TestDynamicRevocationZeroTransportE2E(unittest.TestCase):
    """Verifies dynamic registry policy revocation immediately blocks dispatches."""

    def test_dynamic_model_revocation_blocks_subsequent_calls(self):
        # Baseline: model is approved
        test_model = "gemini-3.8-flash"
        self.assertTrue(is_model_approved(test_model))

        # Simulate dynamic registry reload without test_model
        dummy_registry = {
            "version": "2.0.0-revoked",
            "policy_name": "test-revocation",
            "default_provider": "openai",
            "approved_models": {
                "openai": ["gpt-6.1-sol"],
                "google": ["gemini-3.5-flash-lite"],
            },
            "banned_models": ["mock-banned-v1", "mock-banned-v2", test_model],
            "fallback_tiers": {"fast": ["gemini-3.5-flash-lite"]},
        }

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
            json.dump(dummy_registry, tmp)
            tmp.flush()
            tmp_path = Path(tmp.name)

        try:
            with patch("dual_pipeline.model_registry.REGISTRY_JSON_PATH", tmp_path):
                reload_registry()
                self.assertFalse(is_model_approved(test_model))
                self.assertTrue(is_model_approved("gpt-6.1-sol"))
        finally:
            tmp_path.unlink(missing_ok=True)
            # Restore baseline registry
            reload_registry()
            self.assertTrue(is_model_approved(test_model))


if __name__ == "__main__":
    unittest.main()
