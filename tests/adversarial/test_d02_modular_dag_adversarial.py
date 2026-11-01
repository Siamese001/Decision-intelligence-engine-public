"""Adversarial and boundary stress tests for D02 3-Pass Parallel Modular DAG.

Covers:
1. Boundary payloads (empty content, sub-floor outputs, boundary floor match).
2. Malformed input (code fences, duplicate section titles, invalid markdown tables).
3. Invariant bypass attempts (monolithic bypass, zero-word floors).
4. Concurrency races (multi-threaded concurrent DAG executions).
5. Rollback and fault recovery (provider API exceptions in worker threads).
"""
from __future__ import annotations

import concurrent.futures
from unittest.mock import patch

import pytest

from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    MonolithicRecapBlockedError,
    WordFloorError,
    execute_d02_3pass_dag,
    validate_word_floor,
)


def test_adversarial_word_floor_boundary_conditions():
    """Category 1: Boundary payloads - exact floor boundary and severe truncation."""
    # Exact boundary match
    exact_text = "word " * 800
    assert validate_word_floor(exact_text, 800, "Pass 2A") == 800

    # 1 word under floor
    sub_text = "word " * 799
    with pytest.raises(WordFloorError, match="failing required floor of 800"):
        validate_word_floor(sub_text, 800, "Pass 2A")


def test_adversarial_malformed_fenced_subsections():
    """Category 2: Malformed input - passes returning redundant headers or markdown code fences."""
    narrative = "# Header\n\nDiscussion..." + (" context" * 20)
    # Ledger with redundant header and code fence
    ledger = "```markdown\n## Action & Governance Ledger\n| Item # | Action |\n| 1 | Test |\n```"
    perspectives = "## Appendix: Detailed Stakeholder Perspectives\n# Alex Brennan\n- **Theme**: Analysis"

    with patch(
        "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2a",
        return_value=narrative,
    ), patch(
        "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2b",
        return_value=ledger,
    ), patch(
        "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2c",
        return_value=perspectives,
    ):
        stitched = execute_d02_3pass_dag(
            model="gemini-3.8-flash",
            system_prompt="prompt",
            user_content="content",
            min_words_2a=10,
            min_words_2c=5,
        )
        assert stitched.count("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown") == 1


def test_adversarial_invariant_bypass_attempts():
    """Category 3: Invariant bypass attempts - bypass flag rejected for non-frontier models."""
    with pytest.raises(MonolithicRecapBlockedError):
        execute_d02_3pass_dag(
            model="gemini-3.8-flash",
            system_prompt="sys",
            user_content="user",
            allow_monolithic=True,
        )


def test_adversarial_concurrency_race():
    """Category 4: Concurrency races - multi-threaded simultaneous 3-pass DAG runs."""
    narrative = "Strategic narrative content and technical discussion context " * 25
    ledger = "| Item # | Action | Owner | Target Date | Next Step |\n| 1 | Action 1 | Alex | Today | Done |"
    perspectives = "Stakeholder perspectives breakdown and forensic analysis " * 20

    def fake_gemini(*args, **kwargs):
        prompt = kwargs.get("system_prompt", "") or (args[1] if len(args) > 1 else "")
        if "Governance" in prompt:
            return ledger
        elif "Perspectives" in prompt:
            return perspectives
        return narrative

    def run_worker(idx: int):
        return execute_d02_3pass_dag(
            model="gemini-3.8-flash",
            system_prompt=f"prompt {idx}",
            user_content=f"user content {idx}",
            min_words_2a=20,
            min_words_2c=15,
        )

    with patch(
        "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
        side_effect=fake_gemini,
    ), concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run_worker, i) for i in range(12)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 12
    for r in results:
        assert "## Action & Governance Ledger" in r


def test_adversarial_fault_recovery_and_propagation():
    """Category 5: Rollback and fault recovery when worker thread encounters fatal API error."""
    with patch(
        "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
        side_effect=RuntimeError("Google API 503 Service Unavailable"),
    ), pytest.raises(RuntimeError, match="503 Service Unavailable"):
        execute_d02_3pass_dag(
            model="gemini-3.8-flash",
            system_prompt="prompt",
            user_content="content",
            min_words_2a=10,
            min_words_2c=10,
        )


def test_adversarial_banned_model_injection_attempt():
    """Category 6: Invariant bypass attempt - injecting banned model raises ValueError."""
    from dual_pipeline.transcript_stages_core.external_synthesizer import (
        call_gemini_generate_content,
    )
    with pytest.raises(ValueError, match="strictly banned"):
        call_gemini_generate_content(
            model="gemini-2.5-flash",  # allow-retired-model
            system_prompt="exploit",
            user_content="attack",
        )
