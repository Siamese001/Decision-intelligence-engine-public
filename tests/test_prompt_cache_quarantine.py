"""Unit tests for PromptPrefixQuarantine and forensic/synthesis prompt_cache_key derivation."""

from __future__ import annotations

from dual_pipeline.llm_judge_core.judges.forensic import resolve_forensic_prompt_cache_key
from dual_pipeline.token_optimization.prompt_cache import PromptPrefixQuarantine


def test_prompt_prefix_quarantine_alignment_and_forensic_cache_key() -> None:
    quarantine = PromptPrefixQuarantine()
    static_block = " CONSTITUTIONAL GOVERNANCE RULES " * 300
    messages, components, prefix_sha = quarantine.build_quarantined_messages(
        system_prompt=static_block + "\nExecution Run ID: run-12345678",
        user_content="Evaluate turn 10",
    )
    assert components.is_cacheable_prefix is True
    assert len(prefix_sha) == 64
    assert "run-12345678" not in messages[0]["content"]
    assert "run-12345678" in messages[1]["content"]

    key = resolve_forensic_prompt_cache_key("gpt-6-luna", static_block)
    assert key.startswith("nee:judge_forensic:gpt-6-luna:")
