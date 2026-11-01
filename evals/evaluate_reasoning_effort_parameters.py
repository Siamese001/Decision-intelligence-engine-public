#!/usr/bin/env python3
"""
evals/evaluate_reasoning_effort_parameters.py

Comprehensive evaluation suite for reasoning model parameter governance.
Evaluates model `gpt-5.6-terra` across all seven OpenAI reasoning parameters:
  - none
  - minimal
  - low
  - medium
  - high
  - xhigh
  - max

Validates:
1. Parameter Immunity (stripping forbidden temperature/top_p when reasoning is active,
   allowing temperature when reasoning_effort="none").
2. Wire Parameter Normalization (minimal -> low, max -> xhigh).
3. Live API execution and token usage telemetry (prompt, completion, reasoning tokens).
4. Emits immutable sealed runtime receipt to reports/reasoning_effort_parameter_evaluation_receipt.json.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.credentials import (
    get_openai_client,
    sanitize_model_call_kwargs,
)

logger = logging.getLogger("evals.reasoning_parameters")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

ALL_REASONING_EFFORTS = [
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]


class ReasoningEffortParameterEvaluator:
    """Evaluates a target reasoning model across all OpenAI reasoning effort settings."""

    def __init__(
        self,
        model_name: str = "gpt-5.6-terra",  # noqa
        allow_mock: bool = False,
    ):
        self.model_name = model_name
        self.allow_mock = allow_mock
        self.client, self.creds = get_openai_client(
            allow_mock=allow_mock,
            model=model_name,
        )

    def evaluate_effort(
        self,
        effort: str,
        test_prompt: str = "Calculate 19 * 37 and explain the mathematical steps in one concise sentence.",
    ) -> Dict[str, Any]:
        """Evaluates a single reasoning effort parameter for parameter immunity and live API response."""
        start_t = time.time()

        # Step 1: Craft raw call kwargs with deliberate forbidden parameters to verify immunity
        raw_kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "You are an analytical assistant. Be precise."},
                {"role": "user", "content": test_prompt},
            ],
            "reasoning_effort": effort,
            "temperature": 0.7,
            "top_p": 0.95,
        }

        # Step 2: Apply parameter immunity sanitization
        sanitized = sanitize_model_call_kwargs(self.model_name, raw_kwargs)
        wire_effort = sanitized.get("reasoning_effort")
        temp_permitted = "temperature" in sanitized

        # Verify parameter immunity invariants
        if effort == "none":
            assert temp_permitted, "Temperature should be permitted when reasoning_effort is none"
            assert sanitized["temperature"] == 0.7, "Temperature value should be preserved when reasoning_effort is none"
        else:
            assert not temp_permitted, f"Temperature must be stripped when reasoning_effort is {effort}"
            assert "top_p" not in sanitized, f"top_p must be stripped when reasoning_effort is {effort}"

        # Step 3: Wire parameter mapping assertion
        if effort in ("max", "extreme"):
            assert wire_effort == "xhigh", f"Effort '{effort}' must map to wire 'xhigh', got '{wire_effort}'"
        elif effort == "minimal":
            assert wire_effort == "low", f"Effort 'minimal' must map to wire 'low', got '{wire_effort}'"
        else:
            assert wire_effort == effort, f"Effort '{effort}' must pass through to wire as '{effort}'"

        # Step 4: Dispatch live or mock invocation
        response_content = ""
        prompt_tokens = 0
        completion_tokens = 0
        reasoning_tokens = 0
        cached_tokens = 0
        status = "PASS"
        error_detail: Optional[str] = None

        if not self.allow_mock and self.client is not None and not self.creds.is_mock:
            try:
                resp = self.client.chat.completions.create(**sanitized)
                response_content = resp.choices[0].message.content or ""
                if resp.usage:
                    prompt_tokens = resp.usage.prompt_tokens
                    completion_tokens = resp.usage.completion_tokens
                    details = getattr(resp.usage, "completion_tokens_details", None)
                    if details:
                        reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
                from dual_pipeline.prompt_cache import normalize_cached_tokens

                cached_tokens = normalize_cached_tokens(resp)
            except Exception as exc:
                status = "FAIL"
                error_detail = f"{type(exc).__name__}: {exc}"
                logger.error("API error for effort %s: %s", effort, exc)
        else:
            # Deterministic mock verification
            response_content = f"[MOCK_RESPONSE] Verified {self.model_name} at reasoning_effort='{wire_effort}'."
            prompt_tokens = 30
            completion_tokens = 25
            reasoning_tokens = 0 if effort == "none" else 15
            cached_tokens = 0

        elapsed = round(time.time() - start_t, 3)

        return {
            "input_reasoning_effort": effort,
            "wire_reasoning_effort": wire_effort,
            "parameter_immunity_applied": True,
            "temperature_permitted": temp_permitted,
            "status": status,
            "latency_seconds": elapsed,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cached_tokens": cached_tokens,
            "sample_response": response_content.strip()[:160],
            "error": error_detail,
        }

    def run_all(self) -> Dict[str, Any]:
        """Evaluates all 7 reasoning efforts and seals the audit receipt."""
        suite_start = time.time()
        results: List[Dict[str, Any]] = []

        logger.info("=" * 70)
        logger.info("Evaluating %s across all reasoning efforts", self.model_name)
        logger.info("Active Key: %s | Live Mode: %s", self.creds.masked_key, not self.creds.is_mock)
        logger.info("=" * 70)

        for effort in ALL_REASONING_EFFORTS:
            res = self.evaluate_effort(effort)
            results.append(res)
            logger.info(
                "Effort: %-7s -> Wire: %-5s | Status: %s | Latency: %.2fs | Reasoning Toks: %d",
                effort,
                res["wire_reasoning_effort"],
                res["status"],
                res["latency_seconds"],
                res["reasoning_tokens"],
            )

        total_elapsed = round(time.time() - suite_start, 3)
        passed_count = sum(1 for r in results if r["status"] == "PASS")
        all_passed = (passed_count == len(ALL_REASONING_EFFORTS))

        receipt = {
            "receipt_version": "1.0.0",
            "evaluator": "ReasoningEffortParameterEvaluator",
            "model_under_test": self.model_name,
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "execution_mode": "LIVE" if (self.client and not self.creds.is_mock) else "MOCK",
            "api_key_masked": self.creds.masked_key,
            "total_efforts_evaluated": len(ALL_REASONING_EFFORTS),
            "efforts_passed": passed_count,
            "terminal_outcome": "PASS" if all_passed else "FAIL",
            "total_duration_seconds": total_elapsed,
            "results": results,
            "parameter_immunity_verified": {
                "temperature_allowed_on_none": True,
                "temperature_stripped_on_active_reasoning": True,
                "top_p_stripped_on_active_reasoning": True,
                "minimal_normalized_to_low": True,
                "max_normalized_to_xhigh": True,
            },
        }

        # Write receipt only in non-mock mode so hermetic tests do not dirty tracked receipts
        if not self.allow_mock:
            out_path = ROOT / "reports" / "reasoning_effort_parameter_evaluation_receipt.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            logger.info("Audit receipt written to: %s", out_path)

        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate model reasoning effort parameters")
    parser.add_argument("--model", default="gpt-5.6-terra", help="Target model ID")  # noqa
    parser.add_argument("--mock", action="store_true", default=False, help="Allow hermetic mock execution")
    args = parser.parse_args()

    evaluator = ReasoningEffortParameterEvaluator(
        model_name=args.model,
        allow_mock=args.mock,
    )
    receipt = evaluator.run_all()
    print("\n" + "=" * 70)
    print(f"REASONING EFFORT EVALUATION: {receipt['terminal_outcome']}")
    print(f"Passed: {receipt['efforts_passed']}/{receipt['total_efforts_evaluated']}")
    print(f"Model: {receipt['model_under_test']} | Mode: {receipt['execution_mode']}")
    print("=" * 70)
    return 0 if receipt["terminal_outcome"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
