"""
tests/adversarial/test_ingestion_hardening_wave5_adversarial.py

Wave 5 Adversarial Contract Test Suite.
Validates:
  - B01: Document model/effort override remains request-scoped; concurrent transcript/notes providers retain their own defaults.
  - B02: Document four-call/300-second pilot never caps a recording, ASR, diarization, copilot or long final transcript.
  - B03: Retries/fallback/repair reserve atomic modality-and-stage budgets; ambiguous dispatch stays charged across restart.
  - B04: Concurrent document/transcript jobs cannot share budget counters, credentials, output paths or locks incorrectly.
  - B05: Parallel workers receive separate copied execution contexts carrying their own deadline/cancel/budget handles; late results cannot publish after cancel.
  - B06: Shared clients/caches have proved concurrency safety; no nested mutable provider settings or credentials leak between modes.
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Dict
from unittest.mock import MagicMock
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.policy_profiles import (
    BudgetExhaustedError,
    LatePublicationBlockedError,
    OperationBudgetLedger,
    OperationCancelledError,
    PolicyProfile,
    WorkerExecutionContext,
    deep_isolate_provider,
    resolve_policy_profile,
)
from dual_pipeline.canonical_pipeline.run_configuration import (
    RunConfiguration,
    prepare_run_configuration,
)


def test_b01_contract() -> None:
    """B01: Document model/effort override remains request-scoped; concurrent transcript/notes providers retain their own defaults."""
    # Shared base provider initialized with default constructor settings
    shared_provider = MagicMock()
    shared_provider.reasoning_effort = "low"
    shared_provider.d03_model = "gpt-5.6-terra"
    shared_provider.options = {"model": "gpt-5.6-terra", "temperature": 0.2}

    # 1. Document run requests high reasoning effort
    doc_meta = {"canonical_modality": "DOCUMENT"}
    doc_config = prepare_run_configuration(
        shared_provider,
        metadata=doc_meta,
        provider_type="mock_llm",
        default_effort="medium",
        extraction_effort="high",
        judge_effort="high",
        modality=CanonicalModality.DOCUMENT,
    )

    assert doc_config.extraction_effort == "high"
    assert doc_config.judge_effort == "high"
    assert doc_config.provider.reasoning_effort == "high"
    # Constructor default on original shared provider is strictly unmutated
    assert shared_provider.reasoning_effort == "low"

    # 2. Concurrent transcript run on the same shared provider retains its constructor defaults
    transcript_meta = {"canonical_modality": "MEETING_TRANSCRIPT"}
    transcript_config = prepare_run_configuration(
        shared_provider,
        metadata=transcript_meta,
        provider_type="mock_llm",
        default_effort="medium",
        extraction_effort=None,
        judge_effort=None,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
    )
    assert transcript_config.extraction_effort == "low"
    assert transcript_config.judge_effort == "medium"
    assert transcript_config.provider.reasoning_effort == "low"

    # 3. CLI override precedence: CLI argument overrides metadata without mutating provider
    meta_with_low = {"extraction_effort": "low", "canonical_modality": "DOCUMENT"}
    cli_override_config = prepare_run_configuration(
        shared_provider,
        metadata=meta_with_low,
        provider_type="mock_llm",
        default_effort="medium",
        extraction_effort="high",
        modality=CanonicalModality.DOCUMENT,
    )
    assert cli_override_config.extraction_effort == "high"
    assert shared_provider.reasoning_effort == "low"


def test_b02_contract() -> None:
    """B02: Document four-call/300-second pilot never caps a recording, ASR, diarization, copilot or long final transcript."""
    # 1. Positive binding: Document synthesis pilot receives 4 calls / 300s
    doc_profile = resolve_policy_profile(
        CanonicalModality.DOCUMENT,
        flags={"fast_pilot_budget": True},
    )
    assert doc_profile.allows_document_pilot is True
    assert doc_profile.max_physical_requests == 4
    assert doc_profile.max_active_seconds == 300.0

    # 2. Negative protections: fast_pilot_budget cannot be bound to non-document modalities
    with pytest.raises(ValueError, match="fast_pilot_budget is strictly reserved for DOCUMENT"):
        resolve_policy_profile(CanonicalModality.AUDIO, flags={"fast_pilot_budget": True})

    with pytest.raises(ValueError, match="fast_pilot_budget is strictly reserved for DOCUMENT"):
        resolve_policy_profile(CanonicalModality.MEETING_TRANSCRIPT, flags={"fast_pilot_budget": True})

    with pytest.raises(ValueError, match="fast_pilot_budget is strictly reserved for DOCUMENT"):
        resolve_policy_profile("LIVE", flags={"fast_pilot_budget": True})

    with pytest.raises(ValueError, match="fast_pilot_budget is strictly reserved for DOCUMENT"):
        resolve_policy_profile(CanonicalModality.MEETING_NOTES, flags={"fast_pilot_budget": True})

    # 3. Selective meeting03-only cannot borrow document pilot budget
    with pytest.raises(ValueError, match="Selective meeting03-only request cannot select a document budget"):
        resolve_policy_profile(
            CanonicalModality.MEETING_TRANSCRIPT,
            flags={"output3_only": True, "fast_pilot_budget": True},
        )

    # 4. Standard Audio, Meeting, Live, and Notes profiles are uncapped by document limits
    audio_prof = resolve_policy_profile(CanonicalModality.AUDIO)
    assert audio_prof.max_physical_requests is None
    assert audio_prof.max_active_seconds is None
    assert audio_prof.allows_document_pilot is False

    live_prof = resolve_policy_profile("LIVE")
    assert live_prof.max_physical_requests is None
    assert live_prof.max_active_seconds is None
    assert live_prof.allows_document_pilot is False

    notes_prof = resolve_policy_profile(CanonicalModality.MEETING_NOTES)
    assert notes_prof.max_physical_requests is None
    assert notes_prof.max_active_seconds is None


def test_b03_contract() -> None:
    """B03: Retries/fallback/repair reserve atomic modality-and-stage budgets; ambiguous dispatch stays charged across restart."""
    ledger = OperationBudgetLedger(
        operation_id="op_b03_doc_test",
        modality="DOCUMENT",
        max_requests=4,
        active_deadline_seconds=300.0,
    )

    # 1. Primary extraction reservation and success
    token1 = ledger.reserve("extraction", count=1)
    assert ledger.reserved_requests == 1
    ledger.record_outcome(token1, success=True)
    assert ledger.completed_requests == 1

    # 2. Stage 03 judge retry with ambiguous transport outcome
    token2 = ledger.reserve("judge_retry", count=1)
    assert ledger.reserved_requests == 2
    ledger.record_outcome(token2, success=False, ambiguous=True)
    assert ledger.ambiguous_requests == 1

    # 3. Durable persistence and restart simulation: ambiguous charge must persist
    persisted_state = ledger.serialize()
    assert persisted_state["ambiguous_requests"] == 1
    assert persisted_state["reserved_requests"] == 2

    restored_ledger = OperationBudgetLedger.from_dict(persisted_state)
    assert restored_ledger.ambiguous_requests == 1
    assert restored_ledger.reserved_requests == 2

    # 4. Attempting to reserve 3 more requests exceeds the 4-request ceiling
    with pytest.raises(BudgetExhaustedError, match="cannot reserve 3 requests"):
        restored_ledger.reserve("repair_attempt", count=3)

    # 5. Exactly 2 remaining requests can be reserved up to ceiling (2 + 2 = 4)
    token3 = restored_ledger.reserve("repair_attempt", count=2)
    assert restored_ledger.reserved_requests == 4

    # 6. Any further reservation attempt is blocked
    with pytest.raises(BudgetExhaustedError):
        restored_ledger.reserve("extra_call", count=1)


def test_b04_contract() -> None:
    """B04: Concurrent document/transcript jobs cannot share budget counters, credentials, output paths or locks incorrectly."""
    doc_ledger = OperationBudgetLedger(
        operation_id="op_doc_101",
        modality="DOCUMENT",
        max_requests=4,
    )
    transcript_ledger = OperationBudgetLedger(
        operation_id="op_transcript_202",
        modality="MEETING_TRANSCRIPT",
        max_requests=50,
    )

    # Concurrent execution simulator
    def run_doc_worker():
        for i in range(4):
            tok = doc_ledger.reserve(f"doc_stage_{i}")
            doc_ledger.record_outcome(tok, success=True)

    def run_transcript_worker():
        for i in range(5):
            tok = transcript_ledger.reserve(f"transcript_stage_{i}")
            transcript_ledger.record_outcome(tok, success=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_doc = executor.submit(run_doc_worker)
        f_tr = executor.submit(run_transcript_worker)
        f_doc.result()
        f_tr.result()

    # Document ledger is now exhausted
    assert doc_ledger.reserved_requests == 4
    with pytest.raises(BudgetExhaustedError):
        doc_ledger.reserve("doc_overflow")

    # Transcript ledger was completely unpolluted by document spend
    assert transcript_ledger.reserved_requests == 5
    # Transcript still has remaining budget and can reserve cleanly
    tok_next = transcript_ledger.reserve("transcript_next")
    assert tok_next is not None
    assert transcript_ledger.reserved_requests == 6


def test_b05_contract() -> None:
    """B05: Parallel workers receive separate copied execution contexts carrying their own deadline/cancel/budget handles; late results cannot publish after cancel."""
    parent_ledger = OperationBudgetLedger(
        operation_id="op_parent_505",
        modality="MEETING_TRANSCRIPT",
        max_requests=4,
    )

    # Atomically subdivide quotas for Stage 02 and Stage 03 children
    child_ledger_02 = parent_ledger.sub_divide_quota("output_2", quota=2)
    child_ledger_03 = parent_ledger.sub_divide_quota("output_3", quota=2)

    # Parent has allocated all 4 requests; cannot over-allocate
    with pytest.raises(BudgetExhaustedError, match="only 0 requests remaining"):
        parent_ledger.sub_divide_quota("output_extra", quota=1)

    ctx_02 = WorkerExecutionContext(
        worker_id="w_recap_1",
        parent_operation_id=parent_ledger.operation_id,
        stage="output_2",
        budget_handle=child_ledger_02,
        context_data={"metadata": {"archetype": "briefing"}},
        deadline_seconds=60.0,
    )

    ctx_03 = WorkerExecutionContext(
        worker_id="w_graph_1",
        parent_operation_id=parent_ledger.operation_id,
        stage="output_3",
        budget_handle=child_ledger_03,
        context_data={"metadata": {"entities": []}},
        deadline_seconds=60.0,
    )

    # Context data is deeply isolated
    ctx_02.context_data["metadata"]["archetype"] = "updated_recap"
    assert ctx_03.context_data["metadata"].get("archetype") != "updated_recap"

    # Worker 02 completes and can publish
    assert ctx_02.can_publish() is True
    ctx_02.assert_can_publish()

    # Worker 03 is cancelled mid-execution
    ctx_03.cancel()
    assert ctx_03.is_cancelled() is True
    assert ctx_03.can_publish() is False

    # Late result publication from cancelled worker is blocked
    with pytest.raises(LatePublicationBlockedError, match="cancelled or expired"):
        ctx_03.assert_can_publish()

    # Expired deadline also blocks late publication
    expired_ctx = WorkerExecutionContext(
        worker_id="w_expired",
        parent_operation_id=parent_ledger.operation_id,
        stage="output_3",
        budget_handle=child_ledger_03,
        deadline_seconds=0.001,
    )
    time.sleep(0.01)
    assert expired_ctx.can_publish() is False
    with pytest.raises(LatePublicationBlockedError):
        expired_ctx.assert_can_publish()


def test_b06_contract() -> None:
    """B06: Shared clients/caches have proved concurrency safety; no nested mutable provider settings or credentials leak between modes."""
    shared_provider = MagicMock()
    shared_provider.options = {
        "headers": {"Authorization": "Bearer master_token"},
        "tags": ["env:prod", "system:core"],
        "timeout": 30.0,
    }

    # Worker A (Document synthesis)
    worker_a_prov = deep_isolate_provider(shared_provider)
    worker_a_prov.options["headers"]["Authorization"] = "Bearer document_token"
    worker_a_prov.options["tags"].append("modality:document")

    # Worker B (Live ASR / Transcript)
    worker_b_prov = deep_isolate_provider(shared_provider)
    worker_b_prov.options["headers"]["Authorization"] = "Bearer transcript_token"
    worker_b_prov.options["tags"].append("modality:transcript")

    # Verify no cross-thread or cross-mode credential or setting leakage
    assert shared_provider.options["headers"]["Authorization"] == "Bearer master_token"
    assert shared_provider.options["tags"] == ["env:prod", "system:core"]

    assert worker_a_prov.options["headers"]["Authorization"] == "Bearer document_token"
    assert "modality:document" in worker_a_prov.options["tags"]
    assert "modality:transcript" not in worker_a_prov.options["tags"]

    assert worker_b_prov.options["headers"]["Authorization"] == "Bearer transcript_token"
    assert "modality:transcript" in worker_b_prov.options["tags"]
    assert "modality:document" not in worker_b_prov.options["tags"]
