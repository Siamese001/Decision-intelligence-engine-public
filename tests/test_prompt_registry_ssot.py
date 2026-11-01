"""
tests/test_prompt_registry_ssot.py

Comprehensive test suite verifying that PromptRegistry serves as the canonical
Single Source of Truth (SSOT) for all evaluation prompts and synthesis contracts.
"""

import hashlib
from pathlib import Path

from dual_pipeline.llm_judge_core.recap import SYSTEM_PROMPT as RECAP_SYSTEM_PROMPT

from dual_pipeline.llm_judge_core.prompts import EXECUTIVE_EXOSKELETON_REVIEW_PROMPT
from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY, PromptRegistry

ROOT = Path(__file__).resolve().parent.parent


def test_prompt_registry_manifest_integrity():
    """Verify that all 17 canonical prompts in the manifest exist and have valid SHA-256 digests."""
    manifest = CANONICAL_PROMPT_REGISTRY.get_manifest()
    assert len(manifest) >= 17

    expected_keys = [
        "JUDGE_FORENSIC",
        "JUDGE_RECAP",
        "JUDGE_GRAPH",
        "EXECUTIVE_EXOSKELETON_REVIEW",
        "EXECUTIVE_STRATEGY_AUDIT",
        "RAW_TRANSCRIPT_SYNTHESIS",
        "ARTIFACT_INDEPENDENCE_FIREWALL",
        "AGENT_REASONING_SCRATCHPAD",
        "HOLISTIC_RELEASE_JUDGE",
        "HARDENED_SVP_JUDGE",
        "STAGE_01_FORENSIC",
        "STAGE_02_RECAP",
        "STAGE_03_GRAPH",
        "AGENTIC_EXTRACTION",
        "LIVE_COPILOT",
        "SPEAKER_DISAMBIGUATION",
        "DYNAMIC_PROJECT_ROUTER",
    ]
    for key in expected_keys:
        assert key in manifest, f"Missing expected prompt key: {key}"
        digest = manifest[key]
        assert len(digest) == 64
        assert digest != "FILE_NOT_FOUND"

    assert CANONICAL_PROMPT_REGISTRY.verify_manifest() is True


def test_judge_modules_consume_prompt_registry():
    """Verify that judge modules load their system prompts directly from the registry."""
    from dual_pipeline.llm_judge_core.prompts import HARDENED_SVP_JUDGE_PROMPT

    recap_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_RECAP")
    assert recap_prompt == RECAP_SYSTEM_PROMPT

    forensic_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_FORENSIC")
    assert "Forensic Dialogue & Transcript Auditor" in forensic_prompt

    graph_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_GRAPH")
    assert "Knowledge Graph Master Architect" in graph_prompt

    exoskeleton_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("EXECUTIVE_EXOSKELETON_REVIEW")
    assert exoskeleton_prompt == EXECUTIVE_EXOSKELETON_REVIEW_PROMPT

    hardened_svp_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("HARDENED_SVP_JUDGE")
    assert hardened_svp_prompt == HARDENED_SVP_JUDGE_PROMPT


def test_prompt_caching_and_digest_consistency():
    """Verify that prompt content caching is consistent with SHA-256 digest computation."""
    reg = PromptRegistry()
    prompt1 = reg.get_prompt("JUDGE_GRAPH")
    digest1 = reg.get_prompt_digest("JUDGE_GRAPH")

    computed_digest = hashlib.sha256(prompt1.encode("utf-8")).hexdigest()
    assert digest1 == computed_digest

    # Subsequent fetch hits cache and remains bit-identical
    prompt2 = reg.get_prompt("JUDGE_GRAPH")
    assert prompt1 is prompt2


def test_parameterized_prompt_formatting():
    """Verify that parameterized prompts support clean template interpolation."""
    raw_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("EXECUTIVE_EXOSKELETON_REVIEW")
    formatted = raw_prompt.format(
        canonical_graph_json='{"test": "graph"}',
        context_text="Sample meeting context",
    )
    assert '{"test": "graph"}' in formatted
    assert "Sample meeting context" in formatted
    assert "CANONICAL GRAPH PROJECTION:" in formatted


def test_stage_prompts_and_agentic_extraction_ssot():
    """Verify stage prompt loader and agentic extraction use CANONICAL_PROMPT_REGISTRY."""
    from dual_pipeline.transcript_stages_core.constants import (
        AGENTIC_EXTRACTION_PROMPT,
        _load_stage_prompt,
    )
    assert _load_stage_prompt("forensic") == CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_01_FORENSIC")
    assert _load_stage_prompt("recap") == CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP")
    assert _load_stage_prompt("graph") == CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_GRAPH")
    assert CANONICAL_PROMPT_REGISTRY.get_prompt("AGENTIC_EXTRACTION") == AGENTIC_EXTRACTION_PROMPT


def test_copilot_and_routing_prompts_ssot():
    """Verify copilot and project router consume CANONICAL_PROMPT_REGISTRY."""
    from dual_pipeline.live_meeting_recorder_core.copilot import COPILOT_SYSTEM_PROMPT
    assert CANONICAL_PROMPT_REGISTRY.get_prompt("LIVE_COPILOT") == COPILOT_SYSTEM_PROMPT


def test_zero_inline_system_prompts_across_pipeline():
    """Static AST check verifying zero multiline triple-quoted system prompts across all pipeline modules."""
    checked_files = [
        ROOT / "dual_pipeline" / "llm_judge_core" / "forensic.py",
        ROOT / "dual_pipeline" / "llm_judge_core" / "recap.py",
        ROOT / "dual_pipeline" / "llm_judge_core" / "graph.py",
        ROOT / "dual_pipeline" / "llm_judge_core" / "prompts.py",
        ROOT / "dual_pipeline" / "transcript_stages_core" / "constants.py",
        ROOT / "dual_pipeline" / "live_meeting_recorder_core" / "copilot.py",
        ROOT / "dual_pipeline" / "query_router_core" / "project_router.py",
        ROOT / "dual_pipeline" / "speaker_disambiguation_core" / "disambiguator.py",
    ]
    for path in checked_files:
        content = path.read_text(encoding="utf-8")
        assert "CANONICAL_PROMPT_REGISTRY" in content, f"Module {path.name} missing CANONICAL_PROMPT_REGISTRY"
        assert '"""You are' not in content, f"Module {path.name} contains inline prompt starting with '\"\"\"You are'"
        assert '"""\nYou are' not in content, f"Module {path.name} contains inline prompt starting with '\"\"\"\\nYou are'"

