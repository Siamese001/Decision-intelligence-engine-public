"""
Chaos and Fault Injection Engine for Agentic Pipelines.

Simulates environmental failures (timeouts, rate limits, server errors,
truncated responses, and malformed tool outputs) to evaluate error recovery.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Callable


class ChaosSimulationError(RuntimeError):
    """Base error raised during simulated chaos conditions."""


@dataclass
class ChaosMetrics:
    """Telemetry tracking simulated faults and executions."""

    total_calls: int = 0
    injected_faults: int = 0
    recovered_calls: int = 0


class ChaosInjector:
    """Injects configurable transient or permanent faults into tool and provider calls."""

    def __init__(self):
        self.metrics = ChaosMetrics()

    def wrap_callable(
        self,
        target_fn: Callable[..., Any],
        failure_mode: str = "rate_limit",
        trigger_count: int = 1,
    ) -> Callable[..., Any]:
        """
        Wrap a target callable with simulated chaos behavior.
        
        failure_mode options:
            - 'timeout': raises TimeoutError
            - 'rate_limit': raises ChaosSimulationError('HTTP 429: Rate Limit Exceeded')
            - 'server_error': raises ChaosSimulationError('HTTP 500: Internal Server Error')
            - 'truncated_payload': returns incomplete/truncated JSON string
            - 'malformed_output': returns corrupt unparseable data
        """
        remaining_faults = trigger_count

        @functools.wraps(target_fn)
        def wrapper(*args, **kwargs) -> Any:
            nonlocal remaining_faults
            self.metrics.total_calls += 1

            if remaining_faults > 0:
                remaining_faults -= 1
                self.metrics.injected_faults += 1

                if failure_mode == "timeout":
                    raise TimeoutError("Simulated execution timeout after 60000ms")
                elif failure_mode == "rate_limit":
                    raise ChaosSimulationError("HTTP 429: Rate Limit Exceeded (Simulated)")
                elif failure_mode == "server_error":
                    raise ChaosSimulationError("HTTP 500: Internal Server Error (Simulated)")
                elif failure_mode == "truncated_payload":
                    return '{"status": "incomp'
                elif failure_mode == "malformed_output":
                    return '<<<MALFORMED_BINARY_STREAM\x00\xff>>>'
                else:
                    raise ChaosSimulationError(f"Simulated fault: {failure_mode}")

            # Once remaining_faults is exhausted, execution recovers
            self.metrics.recovered_calls += 1
            return target_fn(*args, **kwargs)

        return wrapper
