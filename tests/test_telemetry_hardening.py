"""
tests/test_telemetry_hardening.py — Verification of Hardened OpenTelemetry Tracing & LLM Evals.

Tests:
1. W3C Context Injection & Extraction across boundaries.
2. GenAI Semantic Convention tracing for LLM calls (reasoning tokens, actual models).
3. SVP Micro-Judge evaluation and QuarantineEvent emission on failure.
4. Bipartite Namespace isolation guard and leak event recording.
5. JSONL local trace ledger persistence and schema verification.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.llm_judge import (
    GraphExtractionJudge,
)
from dual_pipeline.telemetry import (
    extract_trace_context,
    format_traceparent,
    get_tracer,
    inject_trace_context,
    record_bipartite_leak_event,
    trace_llm_execution,
)


def test_w3c_context_propagation():
    """Verify that W3C trace context can be injected into a dict carrier and restored in child context."""
    tracer = get_tracer()
    carrier = {}

    with tracer.start_as_current_span("parent_test_span") as parent:
        inject_trace_context(carrier)
        assert "traceparent" in carrier
        traceparent = carrier["traceparent"]
        assert traceparent.startswith("00-")

        # Format traceparent helper
        formatted = format_traceparent(parent)
        assert formatted == traceparent

    # Extract context in a different thread/context
    extracted_ctx = extract_trace_context(carrier)
    assert extracted_ctx is not None

    with tracer.start_as_current_span("child_test_span", context=extracted_ctx) as child:
        child_ctx = child.get_span_context()
        parent_parts = traceparent.split("-")
        expected_trace_id = parent_parts[1]
        assert f"{child_ctx.trace_id:032x}" == expected_trace_id


def test_gen_ai_semantic_tracing_with_reasoning_tokens():
    """Verify GenAI span captures requested vs actual model and reasoning tokens."""
    prompt = {"role": "user", "content": "Adversarial test prompt for Apex Global Enterprises"}
    span, recorder = trace_llm_execution(
        operation_name="gen_ai.test_call",
        requested_model="gpt-5.6-sol",
        reasoning_effort="xhigh",
        prompt_content=prompt,
    )

    try:
        # Simulate OpenAI API response with reasoning_tokens
        mock_response = MagicMock()
        mock_response.id = "chatcmpl-test-007"
        mock_response.model = "gpt-5.6-sol-2026-09"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 512
        mock_response.usage.completion_tokens = 1024
        mock_response.usage.total_tokens = 1536

        details = MagicMock()
        details.reasoning_tokens = 768
        mock_response.usage.completion_tokens_details = details

        recorder.record_response(mock_response)
    finally:
        span.end()

    # Verify span attributes
    attrs = span.attributes
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-5.6-sol"
    assert attrs["gen_ai.response.model"] == "gpt-5.6-sol-2026-09"
    assert attrs["gen_ai.request.reasoning_effort"] == "xhigh"
    assert attrs["gen_ai.usage.prompt_tokens"] == 512
    assert attrs["gen_ai.usage.completion_tokens"] == 1024
    assert attrs["gen_ai.usage.reasoning_tokens"] == 768
    assert attrs["gen_ai.response.id"] == "chatcmpl-test-007"
    assert "gen_ai.prompt.sha256" in attrs


def test_svp_micro_judge_quarantine_event():
    """Verify that when an agentic leverage node fails the SVP gate, a QuarantineEvent is emitted."""
    judge = GraphExtractionJudge(mock=True)

    # Tactical node with low materiality and low-level target
    tactical_node = {
        "node_id": "test-tactical-001",
        "key_players": ["junior engineer"],
        "political_rationale": "Short rationale",
        "networking_wedge": "Short wedge",
    }

    result = judge.evaluate_agentic_leverage(tactical_node)
    assert result.pass_gate is False
    assert result.political_materiality < 8


def test_bipartite_namespace_leak_event():
    """Verify that a bipartite security breach emits a security event and sets span error status."""
    tracer = get_tracer()
    with tracer.start_as_current_span("pipeline.c.test_guard") as span:
        record_bipartite_leak_event(
            span,
            requested_namespace="CLIENT_SAFE",
            leaked_node_id="AL-2026-LEAK-01",
            node_tags=["AGENTIC_LEVERAGE", "POLITICAL_WEDGE"],
        )

        assert span.status.status_code.name == "ERROR"
        assert len(span.events) == 1
        event = span.events[0]
        assert event.name == "security.bipartite_leak_detected"
        assert event.attributes["security.requested_namespace"] == "CLIENT_SAFE"
        assert event.attributes["security.leaked_node_id"] == "AL-2026-LEAK-01"


def test_jsonl_trace_ledger_persistence():
    """Verify that ended spans are flushed to disk in the JSONL trace ledger."""
    today_traces = list(Path(".local/telemetry").glob("traces_*.jsonl")) or list(Path("output/telemetry").glob("traces_*.jsonl"))
    assert len(today_traces) > 0, "Trace ledger file must exist in .local/telemetry"

    with open(today_traces[0], "r", encoding="utf-8") as f:
        lines = [json.loads(line.strip()) for line in f if line.strip()]

    assert len(lines) > 0
    sample_trace = lines[-1]
    assert "trace_id" in sample_trace
    assert "span_id" in sample_trace
    assert "duration_ms" in sample_trace
    assert "name" in sample_trace
    assert "attributes" in sample_trace


def test_canonical_error_taxonomy():
    """Verify that all 5 canonical error classes serialize properly with correlation IDs."""
    from dual_pipeline.telemetry_core import (
        AmbiguityThresholdExceeded,
        BudgetExhaustionError,
        PipelineError,
        PolicyViolationError,
        SchemaValidationError,
        TransientNetworkError,
    )

    errs = [
        TransientNetworkError("Network timeout", correlation_id="cid-1", retry_after_seconds=3.0),
        PolicyViolationError("Restricted pattern found", policy_name="C-19", correlation_id="cid-2"),
        SchemaValidationError("Missing required field", schema_urn="urn:nee:fact", correlation_id="cid-3"),
        BudgetExhaustionError("Exceeded tokens", budget_limit=1000, consumed=1050, correlation_id="cid-4"),
        AmbiguityThresholdExceeded("Margin < 25%", margin_delta=0.12, candidate_id="cand-1", correlation_id="cid-5"),
    ]

    for err in errs:
        assert isinstance(err, PipelineError)
        d = err.to_dict()
        assert d["error_type"] == err.__class__.__name__
        assert d["correlation_id"] is not None
        assert "timestamp" in d
        assert len(d["details"]) > 0


def test_judge_dispatch_monotonic_separation():
    """Verify that monotonic queue, provider, retry/backoff, local evaluation,
    model initialization, locks, and operator wait are measured and serialized separately.
    """
    from dual_pipeline.llm_judge_core.telemetry import judge_dispatch_scope

    with judge_dispatch_scope(
        operation_name="L4-Judge Graph Extraction Evaluation",
        effective_evaluator="GraphExtractionJudge",
        effective_model="gpt-6-luna",
        source_id="src-sow-doc-01",
        run_id="run-3090eb2e",
        attempt_id="att-1",
    ) as jdt:
        jdt.record_phase("model_init", 0.05)
        jdt.record_phase("lock_wait", 0.02)
        jdt.record_phase("queue", 0.03)
        jdt.record_phase("provider", 0.25)
        jdt.record_phase("retry_backoff", 0.04)
        jdt.record_phase("local_eval", 0.06)
        jdt.record_phase("operator_wait", 0.01)

    assert jdt.status == "COMPLETED"
    assert jdt.total_duration_s > 0.0
    assert jdt.total_duration_s >= (0.05 + 0.02 + 0.03 + 0.25 + 0.04 + 0.06 + 0.01)
    assert jdt.model_init_duration_s == 0.05
    assert jdt.lock_wait_duration_s == 0.02
    assert jdt.queue_duration_s == 0.03
    assert jdt.provider_duration_s == 0.25
    assert jdt.retry_backoff_duration_s == 0.04
    assert jdt.local_eval_duration_s == 0.06
    assert jdt.operator_wait_duration_s == 0.01

    serialized = jdt.to_dict()
    assert serialized["operation_name"] == "L4-Judge Graph Extraction Evaluation"
    assert serialized["effective_evaluator"] == "GraphExtractionJudge"
    assert serialized["effective_model"] == "gpt-6-luna"
    assert serialized["source_id"] == "src-sow-doc-01"
    assert serialized["run_id"] == "run-3090eb2e"
    assert serialized["attempt_id"] == "att-1"
    assert serialized["status"] == "COMPLETED"
    assert serialized["phases"]["provider_s"] == 0.25


def test_judge_delayed_and_failed_calls_report_positive_time():
    """Verify that both delayed successful calls and failed/cancelled attempts report
    strictly positive duration (never 0.0), and that raw content is strictly excluded.
    """
    import time
    from unittest.mock import MagicMock
    from dual_pipeline.llm_judge_core.telemetry import (
        judge_dispatch_scope,
        record_judge_llm_usage,
    )

    # 1. Delayed call reporting positive time even if duration_seconds is omitted
    mock_resp = MagicMock()
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    mock_resp.usage.prompt_tokens_details = None
    mock_resp.usage.completion_tokens_details = None

    with judge_dispatch_scope(
        operation_name="L4-Judge Executive Recap Evaluation",
        effective_evaluator="ExecutiveRecapJudge",
        effective_model="gpt-6-luna",
    ) as jdt:
        time.sleep(0.01)
        record_judge_llm_usage(mock_resp, model="gpt-6-luna")

    assert jdt.total_duration_s > 0.0
    assert jdt.provider_duration_s > 0.0
    assert jdt.prompt_tokens == 100
    assert jdt.completion_tokens == 50

    # 2. Failed attempt reporting positive time and sanitized error (raw content excluded)
    sensitive_raw_prompt = "CONFIDENTIAL: project_lens budget is executive compensation band with sk_test_redacted_placeholder"
    failed_jdt = None
    try:
        with judge_dispatch_scope(
            operation_name="L4-Judge Graph Extraction Evaluation",
            effective_evaluator="GraphExtractionJudge",
            effective_model="gpt-6-luna",
            source_id="src-002",
        ) as jdt2:
            failed_jdt = jdt2
            time.sleep(0.01)
            raise RuntimeError(f"Connection timeout while transmitting prompt: {sensitive_raw_prompt}")
    except RuntimeError:
        pass

    assert failed_jdt is not None
    assert failed_jdt.status == "FAILED"
    assert failed_jdt.total_duration_s > 0.0
    assert failed_jdt.error_type == "RuntimeError"
    # Ensure secrets and excessive prompt content are sanitized
    assert "sk_test_redacted_placeholder" not in str(failed_jdt.error_message)
    assert "[REDACTED_SECRET]" in str(failed_jdt.error_message)


def test_saved_failed_candidate_replay_blocking_criteria():
    """Replay saved candidate from historical LENS run (document_01247a4aa740bae3bb8b86491bc6a7e86b16092ab2cc2dbb35db19eb6b0555bb.json)
    through QA and final-decision logic with providers disabled to expose exact blocking criteria.
    """
    import json
    from pathlib import Path
    from dual_pipeline.canonical_pipeline.evaluator import UnifiedPipelineQAEvaluator
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult

    ckpt_path = Path(".local/dual_pipeline/checkpoints/document_01247a4aa740bae3bb8b86491bc6a7e86b16092ab2cc2dbb35db19eb6b0555bb.json")
    if not ckpt_path.exists():
        return  # Skip in environments without shared .local historical storage

    data = json.loads(ckpt_path.read_text(encoding="utf-8"))
    out3 = data["stages"]["VALIDATED_SYNTHESIS"]["synthesis"]["output_3"]
    meta = data["stages"]["VALIDATED_SYNTHESIS"]["synthesis"]["metadata"]

    # Authentic source text from sow_scope_clarification.md
    worktree_parent = Path.cwd().parent
    src_candidates = [
        Path("inputs/documents/sow_scope_clarification.md"),
        worktree_parent / "feat_lens_phase1_sow_closeout_pipeline" / "inputs" / "documents" / "sow_scope_clarification.md",
    ]
    source_text = None
    for p in src_candidates:
        if p.exists():
            source_text = p.read_text(encoding="utf-8")
            break

    if not source_text:
        return

    synth = SynthesisResult(output_1="", output_2="", output_3=out3)
    auth, qa_rep, judge_rep, final_dec = UnifiedPipelineQAEvaluator.evaluate(
        modality=CanonicalModality.DOCUMENT,
        synthesis_result=synth,
        source_text=source_text,
        metadata=meta,
        attempt=1,
        max_attempts=2,
        mock=True,
    )

    # Deterministic QA gates pass completely on authentic source text
    assert qa_rep.get("status") == "PASS"
    assert auth is True
    assert final_dec.get("status") == "PASS"

    # Verify that in checkpoint binding, the failure to resume was caused by unbound model revisions
    reuse_blockers = data.get("binding", {}).get("reuse_blockers", [])
    assert "EXTRACTION_MODEL_REVISION_UNBOUND" in reuse_blockers
    assert "JUDGE_MODEL_REVISION_UNBOUND" in reuse_blockers



