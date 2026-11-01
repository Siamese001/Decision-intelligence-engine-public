"""
Hypothesis Property-Based Verification for Full Model Identity Lifecycle.

Verifies:
1. End-to-end configuration -> dispatch -> call ledger -> cost tracker telemetry consistency.
2. Arbitrary unapproved models fail closed before transport execution.
3. Concurrent multi-worker lifecycles maintain complete isolation without crosstalk.
4. Deterministic tools strictly refuse LLM model declarations.
5. Approved-but-wrong identity declarations strictly fail closed across arbitrary model pairs.
"""
from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.host_metadata_adapter import (
    EvidenceTier,
    IdentityScope,
    verify_identity_evidence,
)
from dual_pipeline.model_registry import (
    get_approved_models,
    is_model_approved,
)
from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger, CallTelemetryRecord
from tools.validate_turn_gates import validate_scratchpad_transparency

APPROVED_MODELS = sorted(list(get_approved_models()))


@settings(max_examples=40, deadline=None)
@given(
    model_name=st.sampled_from(APPROVED_MODELS),
    prompt_tokens=st.integers(min_value=10, max_value=2000),
    completion_tokens=st.integers(min_value=10, max_value=2000),
)
def test_property_lifecycle_identity_consistency(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Property: Dispatched model propagates immutably through call ledger and cost tracker."""
    ledger = D02CallLedger(operation_id="lifecycle_tester")
    tracker = RunCostTracker()

    rec = ledger.record_call(
        pass_id="2A",
        pass_attempt=1,
        transport_attempt=1,
        model=model_name,
        status="SUCCESS",
        requested_model=model_name,
        resolved_model=model_name,
        dispatched_model=model_name,
        observed_model=model_name,
        evidence_strength=EvidenceTier.OBSERVED_RESPONSE.value,
    )

    assert rec is not None
    assert rec.dispatched_model == model_name
    assert rec.observed_model == model_name

    cost = tracker.record_call(
        model=rec.dispatched_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        requested_model=rec.requested_model,
    )
    assert cost >= 0.0

    # Verification against turn gate scratchpad validator
    evidence = {
        "dispatched_model": model_name,
        "evidence_strength": "OBSERVED_RESPONSE",
    }
    scratchpad = (
        "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
        f"- **Active Model & Provider**: {model_name} | Verified Provider | Tier: Medium\n"
        "- **Cognitive Breakdown & Thinking**: [Thinking]\n"
        "- **Reasoning Steps**: [Steps]\n"
        "- **API & Tool Invocations**: [Tools]\n"
    )
    eval_res = validate_scratchpad_transparency(scratchpad, evidence=evidence, scope="application_call")
    assert eval_res["status"] == "PASS", f"Failed for {model_name}: {eval_res['issues']}"


@settings(max_examples=40, deadline=None)
@given(
    arbitrary_model=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.",
        min_size=1,
        max_size=30,
    ).filter(lambda m: m not in APPROVED_MODELS),
)
def test_property_unapproved_models_fail_closed(arbitrary_model: str) -> None:
    """Property: Any non-approved model string strictly fails approval check."""
    assert not is_model_approved(arbitrary_model)

    # Scratchpad validation with unapproved model must fail
    scratchpad = (
        "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
        f"- **Active Model & Provider**: {arbitrary_model} | Provider | Tier: High\n"
        "- **Cognitive Breakdown & Thinking**: [Thinking]\n"
        "- **Reasoning Steps**: [Steps]\n"
        "- **API & Tool Invocations**: [Tools]\n"
    )
    eval_res = validate_scratchpad_transparency(scratchpad, scope="application_call")
    assert eval_res["status"] == "FAIL"


@settings(max_examples=20, deadline=None)
@given(
    model_a=st.sampled_from(APPROVED_MODELS),
    model_b=st.sampled_from(APPROVED_MODELS),
)
def test_property_approved_but_wrong_identity_rejection(model_a: str, model_b: str) -> None:
    """Property: Declaring model A when evidence proves model B was dispatched strictly fails."""
    if model_a == model_b:
        return

    evidence = {
        "dispatched_model": model_b,
        "evidence_strength": "OBSERVED_RESPONSE",
    }
    is_valid, reason = verify_identity_evidence(
        declared_model=model_a,
        evidence=evidence,
        scope=IdentityScope.APPLICATION_CALL,
    )
    assert not is_valid
    assert "Approved-but-wrong" in reason or "does not match" in reason


def test_property_concurrent_lifecycle_isolation() -> None:
    """Property: Concurrent multi-worker lifecycles maintain strict evidence isolation."""
    ledger = D02CallLedger(operation_id="concurrent_test")
    tracker = RunCostTracker()

    def worker_lifecycle(idx: int, model: str) -> tuple[str, str, float]:
        rec = ledger.record_call(
            pass_id="2A",
            pass_attempt=1,
            transport_attempt=1,
            model=model,
            status="SUCCESS",
            requested_model=model,
            resolved_model=model,
            dispatched_model=model,
            observed_model=model,
            evidence_strength=EvidenceTier.OBSERVED_RESPONSE.value,
        )
        assert rec is not None
        cost = tracker.record_call(
            model=rec.dispatched_model,
            prompt_tokens=100 + idx,
            completion_tokens=50 + idx,
            requested_model=rec.requested_model,
        )
        return rec.call_id, rec.dispatched_model, cost

    tasks = [(i, APPROVED_MODELS[i % len(APPROVED_MODELS)]) for i in range(16)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker_lifecycle, idx, model) for idx, model in tasks]
        results = [f.result() for f in futures]

    assert len(results) == 16
    for idx, (cid, dispatched, cost) in enumerate(results):
        expected_model = tasks[idx][1]
        assert dispatched == expected_model
        assert cost >= 0.0


@settings(max_examples=20, deadline=None)
@given(model_name=st.sampled_from(APPROVED_MODELS))
def test_property_deterministic_tools_strictly_reject_llm(model_name: str) -> None:
    """Property: Deterministic tools must report Provider-Free and reject LLM model identities."""
    is_valid, reason = verify_identity_evidence(
        declared_model=model_name,
        evidence={},
        scope=IdentityScope.DETERMINISTIC_TOOL,
    )
    assert not is_valid
    assert "Deterministic tool cannot declare an LLM model identity" in reason

    is_valid_free, _ = verify_identity_evidence(
        declared_model="Provider: None (Provider-Free)",
        evidence={},
        scope=IdentityScope.DETERMINISTIC_TOOL,
    )
    assert is_valid_free
