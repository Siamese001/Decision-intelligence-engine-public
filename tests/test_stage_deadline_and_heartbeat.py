"""
tests/test_stage_deadline_and_heartbeat.py

Unit tests for execute_with_deadline_and_heartbeat:
1. Fast successful execution returns immediately.
2. Arguments (*args, **kwargs) pass cleanly.
3. Target exceptions are propagated directly.
4. Hard deadline expiry raises TimeoutError with operation name.
5. Heartbeat telemetry logs are emitted periodically.
6. Module re-export from transcript_stages_core.deadline_executor works cleanly.
"""
from __future__ import annotations

import logging
import time

import pytest
from unittest.mock import MagicMock, patch

from config.transport import execute_with_deadline_and_heartbeat
from dual_pipeline.transcript_stages_core.deadline_executor import (
    execute_with_deadline_and_heartbeat as reexported_executor,
)


def test_reexported_executor_identity():
    assert reexported_executor is execute_with_deadline_and_heartbeat


def test_fast_execution_returns_immediately():
    def fast_fn(x: int, y: int, multiplier: int = 1) -> int:
        return (x + y) * multiplier

    start = time.monotonic()
    result = execute_with_deadline_and_heartbeat(
        fast_fn,
        10,
        20,
        multiplier=3,
        deadline_seconds=5.0,
        heartbeat_interval=1.0,
        operation_name="Test Fast",
    )
    elapsed = time.monotonic() - start

    assert result == 90
    assert elapsed < 0.5


def test_target_exception_propagates_directly():
    def failing_fn():
        raise ValueError("Invalid payload format")

    with pytest.raises(ValueError, match="Invalid payload format"):
        execute_with_deadline_and_heartbeat(
            failing_fn,
            deadline_seconds=5.0,
            heartbeat_interval=1.0,
            operation_name="Test Error",
        )


def test_deadline_timeout_raises_promptly():
    def slow_hung_fn():
        time.sleep(5.0)
        return "never reached"

    start = time.monotonic()
    with pytest.raises(TimeoutError) as exc_info:
        execute_with_deadline_and_heartbeat(
            slow_hung_fn,
            deadline_seconds=0.2,
            heartbeat_interval=0.05,
            operation_name="L2-Stage03 Hung Test",
        )
    elapsed = time.monotonic() - start

    # Hard timeout should fire around 0.2s, definitely before 1.5s
    assert elapsed < 1.5
    assert "L2-Stage03 Hung Test exceeded hard deadline" in str(exc_info.value)


def test_heartbeat_telemetry_logging(caplog):
    def moderately_slow_fn():
        time.sleep(0.25)
        return "completed"

    with caplog.at_level(logging.INFO):
        result = execute_with_deadline_and_heartbeat(
            moderately_slow_fn,
            deadline_seconds=2.0,
            heartbeat_interval=0.08,
            operation_name="Stage Heartbeat Probe",
        )

    assert result == "completed"
    heartbeat_records = [
        r.message for r in caplog.records
        if "Stage Heartbeat Probe" in r.message and "In progress..." in r.message
    ]
    assert len(heartbeat_records) >= 1


def test_recap_transport_transient_error_classification():
    from dual_pipeline.llm_judge_core.judges.recap_transport import is_transient_recap_error

    assert is_transient_recap_error(TimeoutError("request timed out"))
    assert is_transient_recap_error(ConnectionError("network reset"))
    assert is_transient_recap_error(RuntimeError("HTTP 429 Too Many Requests"))
    assert is_transient_recap_error(RuntimeError("HTTP 503 Service Unavailable"))
    assert not is_transient_recap_error(ValueError("Invalid JSON schema"))
    assert not is_transient_recap_error(PermissionError("403 Forbidden"))


def test_recap_transport_single_retry_owner_and_timeout():
    from unittest.mock import MagicMock
    from dual_pipeline.llm_judge_core.judges.recap_transport import generate_client_recap

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"composite_score": 96, "verdict": "PASS"}'
    mock_resp.choices = [mock_choice]

    bounded_client = MagicMock()
    bounded_client.chat.completions.create.return_value = mock_resp

    mock_recorder = MagicMock()
    with patch("dual_pipeline.llm_judge_core.judges.recap_transport.without_sdk_retries", return_value=bounded_client) as mock_strip:
        res = generate_client_recap(
            mock_client,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            system_prompt="system",
            user_content="user",
            recorder=mock_recorder,
        )

    assert res["composite_score"] == 96
    mock_strip.assert_called_once_with(mock_client, "gpt-5.6-luna")
    assert bounded_client.chat.completions.create.called
    kwargs = bounded_client.chat.completions.create.call_args.kwargs
    assert "timeout" in kwargs
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG
    assert kwargs["timeout"] <= CANONICAL_PROMPT_CONFIG.judge.deadline_seconds


def test_recap_transport_failover_shares_cumulative_deadline():
    from unittest.mock import MagicMock
    from dual_pipeline.llm_judge_core.judges.recap_transport import generate_client_recap
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG

    mock_client = MagicMock()
    bounded_client = MagicMock()
    bounded_client.chat.completions.create.side_effect = RuntimeError("429 Rate limit reached")

    mock_recorder = MagicMock()
    with patch("dual_pipeline.llm_judge_core.judges.recap_transport.without_sdk_retries", return_value=bounded_client), \
         patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content", return_value='{"composite_score": 90, "verdict": "PASS"}') as mock_gemini:
        res = generate_client_recap(
            mock_client,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            system_prompt="system",
            user_content="user",
            recorder=mock_recorder,
        )

    assert res["composite_score"] == 90
    mock_gemini.assert_called_once()
    gemini_deadline = mock_gemini.call_args.kwargs["deadline_seconds"]
    # Failover deadline must not exceed remaining time
    assert gemini_deadline <= CANONICAL_PROMPT_CONFIG.judge.deadline_seconds


def test_timed_out_provider_socket_aborted_promptly():
    import socket
    import threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def srv_loop():
        conn, _ = srv.accept()
        time.sleep(2.0)
        try:
            conn.close()
        except Exception:
            pass

    th = threading.Thread(target=srv_loop, daemon=True)
    th.start()

    active_workers = 0
    worker_lock = threading.Lock()

    def hanging_provider_call():
        nonlocal active_workers
        with worker_lock:
            active_workers += 1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.recv(1024)
        finally:
            with worker_lock:
                active_workers -= 1

    try:
        execute_with_deadline_and_heartbeat(
            hanging_provider_call,
            deadline_seconds=0.2,
            heartbeat_interval=0.05,
            operation_name="Provider Socket Abort Test",
        )
    except TimeoutError:
        pass

    with worker_lock:
        remaining_workers = active_workers

    srv.close()
    assert remaining_workers == 0


def test_zero_overlap_between_timed_out_call_and_fallback():
    import socket
    import threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def srv_loop():
        while True:
            try:
                conn, _ = srv.accept()
                time.sleep(2.0)
                conn.close()
            except Exception:
                break

    th = threading.Thread(target=srv_loop, daemon=True)
    th.start()

    running_primary = 0
    running_fallback = 0
    lock = threading.Lock()
    overlap_detected = False

    def primary_provider():
        nonlocal running_primary, overlap_detected
        with lock:
            running_primary += 1
            if running_fallback > 0:
                overlap_detected = True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.recv(1024)
        finally:
            with lock:
                running_primary -= 1

    def fallback_provider():
        nonlocal running_fallback, overlap_detected
        with lock:
            running_fallback += 1
            if running_primary > 0:
                overlap_detected = True
        try:
            time.sleep(0.05)
            return "FALLBACK_SUCCESS"
        finally:
            with lock:
                running_fallback -= 1

    result = None
    try:
        execute_with_deadline_and_heartbeat(
            primary_provider,
            deadline_seconds=0.2,
            heartbeat_interval=0.05,
            operation_name="Primary Provider Hang",
        )
    except TimeoutError:
        result = execute_with_deadline_and_heartbeat(
            fallback_provider,
            deadline_seconds=1.0,
            heartbeat_interval=0.1,
            operation_name="Fallback Provider",
        )

    srv.close()
    assert result == "FALLBACK_SUCCESS"
    assert not overlap_detected


def test_gemini_semaphore_released_on_timeout():
    import socket
    import threading
    from dual_pipeline.transcript_stages_core.external_synthesizer import _GEMINI_CONCURRENCY_SEMAPHORE

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def srv_loop():
        conn, _ = srv.accept()
        time.sleep(2.0)
        try:
            conn.close()
        except Exception:
            pass

    th = threading.Thread(target=srv_loop, daemon=True)
    th.start()

    initial_sem_val = _GEMINI_CONCURRENCY_SEMAPHORE._value

    def sem_guarded_call():
        with _GEMINI_CONCURRENCY_SEMAPHORE:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.recv(1024)

    try:
        execute_with_deadline_and_heartbeat(
            sem_guarded_call,
            deadline_seconds=0.2,
            heartbeat_interval=0.05,
            operation_name="Gemini Sem Test",
        )
    except TimeoutError:
        pass

    post_sem_val = _GEMINI_CONCURRENCY_SEMAPHORE._value
    srv.close()

    assert initial_sem_val == post_sem_val


def test_fallback_policy_and_classification_preserved():
    from dual_pipeline.llm_judge_core.judges.recap_transport import is_transient_recap_error
    from dual_pipeline.query_router_core.escalation_shield import _is_rate_limit, _is_recoverable

    # Verify recap transient classifications
    assert is_transient_recap_error(TimeoutError("request timed out"))
    assert is_transient_recap_error(ConnectionError("network reset"))
    assert is_transient_recap_error(RuntimeError("HTTP 429 Too Many Requests"))
    assert is_transient_recap_error(RuntimeError("HTTP 503 Service Unavailable"))
    assert not is_transient_recap_error(ValueError("Invalid JSON"))

    # Verify escalation shield classifications
    timeout_err = TimeoutError("socket timeout")
    assert _is_recoverable(timeout_err, _is_rate_limit(timeout_err))

    rate_limit_err = RuntimeError("rate_limit exceeded")
    assert _is_rate_limit(rate_limit_err)
    assert _is_recoverable(rate_limit_err, True)

    auth_err = PermissionError("401 unauthorized")
    assert not _is_rate_limit(auth_err)
    assert not _is_recoverable(auth_err, False)



