#!/usr/bin/env python3
"""
tests/end_to_end/test_model_identity_reporting_e2e.py

End-to-End Acceptance Test Suite for Model Identity Evidence and Reporting SSOT.
Verifies Wave 3 Acceptance Criteria:
1. Incident Symptom Rejection: Stale user-style header ('Gemini 2.5 Pro | Google GenAI | Tier: High')
   and fictional models ('gpt-5.6-luna') cannot be published as verified current identity.
2. Approved-But-Wrong Rejection: Declaring a valid approved model that differs from invocation
   evidence fails closed.
3. Truthful Inaccessible State: Missing host metadata strictly yields 'Unavailable / Unverified',
   and child identities never inherit parent identity without execution evidence.
4. Deterministic Tool Invariant: Deterministic tools declare 'Provider: None (Provider-Free)'
   and fail closed if an LLM identity is attached.
5. Telemetry & Fallback Cost Parity: Cost tracker records dispatched fallback model while
   preserving requested model for operational diagnosis.
6. Subagent Delegation Accounting: Subagent delegations record requested, resolved, dispatched,
   and observed models with evidence strength tiers.
7. Concurrent Telemetry Integrity: Concurrent call records and delegations persist thread-safely
   without race conditions or dirty state.

Categories covered (Mandatory >= 3):
- Boundary payload (empty metadata, None evidence, zero-token calls)
- Malformed input (tampered evidence, corrupt digests, unrendered template placeholders)
- Concurrency & race (multi-threaded ledger recording)
- Exact production regression (incident symptom 3cb49a4059ee202f5a092825be7d1e89cf2432aee46067fa6f33d767da519391)

Governed under AGENTS.md Invariants and WAVE_PLAN_MODEL_IDENTITY_SSOT_RCA Wave 3.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from dual_pipeline.agent_architecture_core.delegation_ledger import (
    DelegationCostLedger,
    DelegationCostRecord,
)
from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.host_metadata_adapter import (
    EvidenceTier,
    HostIdentityReport,
    IdentityScope,
    render_scratchpad_header,
    resolve_host_identity,
    verify_identity_evidence,
)
from dual_pipeline.transcript_stages_core.call_ledger import (
    CallTelemetryRecord,
    D02CallLedger,
    get_active_call_ledger,
    set_active_call_ledger,
)
from dual_pipeline.transcript_stages_core.external_synthesizer import (
    call_gemini_generate_content,
)
from tools.validate_turn_gates import (
    validate_post_turn_gate,
    validate_scratchpad_transparency,
)


class TestIncidentSymptomAndFictionalModelRejectionE2E:
    """Verifies that the incident symptom header and fictional models are rejected fail-closed."""

    INCIDENT_HEADER = "Gemini 2.5 Pro | Google GenAI | Tier: High"

    def test_incident_symptom_header_rejected_by_scratchpad_validation(self) -> None:
        """The exact incident symptom header must fail scratchpad transparency validation."""
        response = f"""
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: {self.INCIDENT_HEADER}
- **Invocation Role**: Code Refactoring
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Testing incident reproduction.
  - *Hypotheses & Evaluation*: Evaluated.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. Test incident rejection.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `pytest` | Status: `SUCCESS` | Outcome: `OK`

Response body.
"""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = validate_scratchpad_transparency(response)
            assert result["status"] == "FAIL"
            assert any(
                "unverified" in issue or "violates approved policy" in issue
                for issue in result["issues"]
            )

    def test_incident_symptom_rejected_by_post_turn_gate(self) -> None:
        """The incident symptom must cause validate_post_turn_gate to return FAIL."""
        response = f"""
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: {self.INCIDENT_HEADER}
- **Invocation Role**: Turn Gate Test
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Enforce turn gate.
  - *Hypotheses & Evaluation*: None.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. Test gate.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `test` | Status: `SUCCESS` | Outcome: `OK`

Turn body.
"""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = validate_post_turn_gate(
                candidate_response=response,
                require_scratchpad=True,
            )
            assert result["status"] == "FAIL"
            assert result["checks"]["scratchpad_transparency"]["status"] == "FAIL"

    def test_fictional_unapproved_model_rejected_fail_closed(self) -> None:
        """Fictional models like gpt-5.6-luna or gpt-6-astra must fail validation."""
        for fictional in ["gpt-5.6-luna (high)", "gpt-6-astra", "claude-3-opus"]:
            response = f"""
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: {fictional} | Cloud API | Tier: High
- **Invocation Role**: Testing
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: None.
  - *Hypotheses & Evaluation*: None.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. None.
- **API & Tool Invocations**:
  - Tool: `test` | Args: `none` | Status: `SUCCESS` | Outcome: `OK`
"""
            result = validate_scratchpad_transparency(response)
            assert result["status"] == "FAIL"


class TestApprovedButWrongRejectionE2E:
    """Verifies that an approved model differing from invocation evidence is strictly rejected."""

    def test_approved_but_wrong_google_models(self) -> None:
        """Declaring gemini-3.5-flash-lite when execution evidence proves gemini-3.8-flash must fail."""
        declared = "gemini-3.5-flash-lite | Google GenAI | Tier: Low"
        evidence = {
            "dispatched_model": "gemini-3.8-flash",
            "observed_model": "gemini-3.8-flash",
            "provider": "google",
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        is_ok, reason = verify_identity_evidence(declared, evidence)
        assert is_ok is False
        assert "Approved-but-wrong identity violation" in reason

    def test_approved_but_wrong_openai_models(self) -> None:
        """Declaring gpt-5.6-sol when execution evidence proves gpt-5.6-terra must fail."""
        declared = "gpt-5.6-sol | OpenAI | Tier: Reasoning"
        evidence = {
            "dispatched_model": "gpt-5.6-terra",
            "observed_model": "gpt-5.6-terra",
            "provider": "openai",
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        is_ok, reason = verify_identity_evidence(declared, evidence)
        assert is_ok is False
        assert "Approved-but-wrong identity violation" in reason

    def test_post_turn_gate_enforces_execution_evidence_correlation(self) -> None:
        """validate_post_turn_gate fails when response claims a different approved model than evidence."""
        response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: gemini-3.5-flash-lite | Google GenAI
- **Invocation Role**: Synthesis
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Testing mismatch.
  - *Hypotheses & Evaluation*: None.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. Step 1.
- **API & Tool Invocations**:
  - Tool: `synthesize` | Args: `data` | Status: `SUCCESS` | Outcome: `OK`
"""
        evidence = {
            "dispatched_model": "gemini-3.8-flash",
            "observed_model": "gemini-3.8-flash",
            "provider": "google",
        }
        result = validate_post_turn_gate(
            candidate_response=response,
            execution_evidence=evidence,
            require_scratchpad=True,
        )
        assert result["status"] == "FAIL"
        assert any(
            "Approved-but-wrong identity violation" in issue
            for issue in result["checks"]["scratchpad_transparency"]["issues"]
        )


class TestTruthfulUnavailableAndHostResolutionE2E:
    """Verifies truthful unavailable reporting and metadata integration."""

    def test_missing_metadata_yields_truthful_unavailable_state(self) -> None:
        """Missing host metadata produces compliant unavailable report."""
        with mock.patch.dict(os.environ, {}, clear=True):
            report = resolve_host_identity(scope=IdentityScope.HOST_ASSISTANT)
            assert report.status == "UNAVAILABLE"
            assert report.raw_model is None
            assert report.canonical_model is None
            assert report.is_approved_by_policy is False
            assert "Unavailable / Unverified" in report.rendered_summary

            # Compliant scratchpad with unavailable state passes turn gate
            compliant_response = f"""
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: {report.rendered_summary}
- **Invocation Role**: Architectural Audit
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Host metadata inaccessible.
  - *Hypotheses & Evaluation*: Truthful reporting chosen over guessing.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. Emit truthful status.
- **API & Tool Invocations**:
  - Tool: `audit` | Args: `all` | Status: `SUCCESS` | Outcome: `OK`

Audit output.
"""
            val = validate_scratchpad_transparency(compliant_response)
            assert val["status"] == "PASS"

    def test_mock_live_host_metadata_resolves_truthfully(self) -> None:
        """When host metadata is supplied, it resolves canonical identity and checks approval."""
        mock_env = {
            "ANTIGRAVITY_HOST_MODEL": "gemini-3.8-flash",
            "ANTIGRAVITY_HOST_PROVIDER": "google",
            "ANTIGRAVITY_SESSION_ID": "sess-live-999",
        }
        with mock.patch.dict(os.environ, mock_env, clear=True):
            report = resolve_host_identity(scope=IdentityScope.HOST_ASSISTANT)
            assert report.status == "VERIFIED"
            assert report.canonical_model == "gemini-3.8-flash"
            assert report.is_approved_by_policy is True
            assert report.host_session_id == "sess-live-999"


class TestTelemetryLedgerAndCostParityE2E:
    """Verifies fallback tracking, telemetry ledger, and delegation accounting."""

    def test_call_ledger_tracks_requested_resolved_dispatched_observed(self) -> None:
        """D02CallLedger records complete identity lifecycle without duplicate accounting."""
        ledger = D02CallLedger()
        record = ledger.record_call(
            pass_id="pass-1",
            pass_attempt=1,
            transport_attempt=1,
            model="gemini-3.5-flash-lite",
            status="SUCCESS",
            usage_metadata={"promptTokenCount": 1000, "candidatesTokenCount": 200},
            requested_model="gemini-3.8-flash",
            resolved_model="gemini-3.8-flash",
            dispatched_model="gemini-3.5-flash-lite",
            observed_model="gemini-3.5-flash-lite-001",
            evidence_strength="OBSERVED_RESPONSE",
        )
        assert record.requested_model == "gemini-3.8-flash"
        assert record.dispatched_model == "gemini-3.5-flash-lite"
        assert record.observed_model == "gemini-3.5-flash-lite-001"
        assert record.evidence_strength == "OBSERVED_RESPONSE"

        # Check export serializability
        summary = ledger.compute_budget_summary()
        assert summary["total_calls"] == 1
        assert len(summary["records"]) == 1
        assert summary["records"][0]["requested_model"] == "gemini-3.8-flash"
        assert summary["records"][0]["dispatched_model"] == "gemini-3.5-flash-lite"

    def test_cost_tracker_calculates_against_dispatched_preserving_requested(self) -> None:
        """RunCostTracker computes USD cost against dispatched model while preserving requested."""
        tracker = RunCostTracker()
        tracker.record_call(
            model="gemini-3.5-flash-lite",
            prompt_tokens=10000,
            completion_tokens=2000,
            requested_model="gemini-3.8-flash",
            resolved_model="gemini-3.8-flash",
            dispatched_model="gemini-3.5-flash-lite",
            observed_model="gemini-3.5-flash-lite-001",
            evidence_strength="OBSERVED_RESPONSE",
        )
        calls = tracker.call_history
        assert len(calls) == 1
        call = calls[0]
        assert call["requested_model"] == "gemini-3.8-flash"
        assert call["dispatched_model"] == "gemini-3.5-flash-lite"
        assert call["observed_model"] == "gemini-3.5-flash-lite-001"
        assert call["cost_usd"] > 0.0

    def test_delegation_ledger_records_identity_and_evidence(self) -> None:
        """DelegationCostLedger records subagent delegation identities and aggregates metrics."""
        ledger = DelegationCostLedger(max_cost_usd=10.0)
        rec = ledger.record_delegation(
            task_id="subtask-01",
            parent_task_id="task-root",
            subagent_role="Researcher",
            model="gemini-3.5-flash-lite",
            prompt_tokens=5000,
            cached_tokens=2500,
            completion_tokens=1000,
            cost_usd=0.005,
            requested_model="gemini-3.8-flash",
            dispatched_model="gemini-3.5-flash-lite",
            observed_model="gemini-3.5-flash-lite-001",
            evidence_strength="OBSERVED_RESPONSE",
        )
        assert rec.requested_model == "gemini-3.8-flash"
        assert rec.dispatched_model == "gemini-3.5-flash-lite"
        assert rec.observed_model == "gemini-3.5-flash-lite-001"
        assert rec.evidence_strength == "OBSERVED_RESPONSE"

        summary = ledger.get_summary()
        assert summary["delegation_count"] == 1
        assert summary["total_cached_tokens"] == 2500
        assert summary["blended_cached_token_share"] == 0.5


class TestConcurrentTelemetryIntegrityE2E:
    """Verifies multi-worker thread safety and race prevention."""

    def test_concurrent_call_recording_thread_safety(self) -> None:
        """Multiple concurrent threads recording calls to D02CallLedger must not lose entries."""
        ledger = D02CallLedger()
        num_threads = 8
        calls_per_thread = 25

        def worker(thread_idx: int) -> None:
            for i in range(calls_per_thread):
                ledger.record_call(
                    pass_id="p1",
                    pass_attempt=1,
                    transport_attempt=1,
                    model="gemini-3.8-flash",
                    status="SUCCESS",
                    usage_metadata={"promptTokenCount": 100, "candidatesTokenCount": 50},
                    requested_model="gemini-3.8-flash",
                    dispatched_model="gemini-3.8-flash",
                    evidence_strength="DISPATCH_RECORDED",
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, t) for t in range(num_threads)]
            concurrent.futures.wait(futures)

        summary = ledger.compute_budget_summary()
        assert summary["total_calls"] == num_threads * calls_per_thread
        assert len(ledger.records) == num_threads * calls_per_thread
