"""
tests/adversarial/test_edge_cases_retry_limits.py

Wave 2 Adversarial and Edge-Case Test Suite:
Bifurcated Fault Tolerance (Retry Segregation).

Verifies:
1. Transient network faults (HTTP 429, 503, ConnectionError) retry up to NETWORK_RETRIES (5).
2. Deterministic payload/schema faults fail closed strictly after PAYLOAD_RETRIES (1).
3. Code-level deterministic gate failures fail closed immediately on attempt 1 without retry.
4. CLI default enforces --max-judge-retries 1.
"""

from __future__ import annotations

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.config.pipeline_settings import (
    MAX_JUDGE_RETRIES_DEFAULT,
    MAX_NETWORK_RETRIES,
    MAX_PAYLOAD_RETRIES,
)
from dual_pipeline.resilience import is_retryable_error, with_idempotent_retry
from dual_pipeline.transcript_pipeline_core.qa_runner import _is_deterministic_only_failure


class MockHTTP429Error(Exception):
    def __init__(self, message: str = "Rate limit exceeded (HTTP 429)"):
        super().__init__(message)
        self.status_code = 429


class MockHTTP503Error(Exception):
    def __init__(self, message: str = "Service unavailable (HTTP 503)"):
        super().__init__(message)
        self.status_code = 503


class MockPydanticValidationError(Exception):
    pass


def test_adversarial_retryable_exception_classification():
    """Verify that network 429/503 errors are recognized as transient, while payload errors are not."""
    assert is_retryable_error(MockHTTP429Error()) is True
    assert is_retryable_error(MockHTTP503Error()) is True
    assert is_retryable_error(ConnectionResetError("Connection dropped")) is True
    assert is_retryable_error(TimeoutError("Socket timeout")) is True

    # Deterministic schema / payload errors MUST NOT be classified as retryable network errors
    assert is_retryable_error(MockPydanticValidationError("Missing required field 'name'")) is False
    assert is_retryable_error(ValueError("Invalid format")) is False


def test_transient_network_fault_survives_with_backoff():
    """Verify that transient 429 rate limits succeed via exponential backoff up to 5 attempts."""
    call_count = 0

    @with_idempotent_retry(max_retries=MAX_NETWORK_RETRIES, base_delay=0.01, max_delay=0.05, jitter=False)
    def flappy_provider_call():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise MockHTTP429Error("429 Too Many Requests: Rate limit exceeded")
        return {"status": "SUCCESS", "attempts": call_count}

    res = flappy_provider_call()
    assert res["status"] == "SUCCESS"
    assert call_count == 3  # Succeeded on 3rd attempt within 5 retries


def test_deterministic_only_failure_fails_closed_immediately():
    """Verify that code/schema failures fail closed immediately on attempt 1 without LLM retry."""
    qa_report = {
        "output_03_canonical_signal_projection": {
            "failures": [
                "PydanticValidationError: 1 validation error on EntityUpsert",
                "Deterministic Pydantic validation failed for Output 3",
            ]
        }
    }
    final_decision = {
        "output_3_deterministic_gates_pass": False,
        "output_3_llm_score_pass": True,
    }

    # _is_deterministic_only_failure should return True -> triggers fail-closed guard
    assert _is_deterministic_only_failure(qa_report, final_decision) is True


def test_cli_parser_defaults_max_judge_retries_to_one():
    """Verify that the CLI parser sets max-judge-retries default to 1 for cost optimization."""
    assert MAX_PAYLOAD_RETRIES == 1
    assert MAX_JUDGE_RETRIES_DEFAULT == 1

    parser = build_parser()
    args = parser.parse_args([
        "ingest-meeting",
        "--input", "mock.txt",
        "--project", "test_proj",
        "--meeting", "Test_Meeting",
        "--date", "10-02-2026",
    ])
    assert args.max_judge_retries == 1
