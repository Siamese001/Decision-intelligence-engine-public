"""
End-to-End Publication & Governance Verification Suite for Model Neutrality.

Verifies:
1. Repository-wide publication sweep passes cleanly under active governance.
2. Injecting unapproved model tokens into active guides blocks publication.
3. Injecting unapproved model tokens into active rules blocks publication.
4. Historical receipts and documents preserve original bytes immutably under manifest.
5. Forged inline bypass comments block publication unconditionally.
6. Publication gate integration with validate_scratchpad_transparency.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lint_retired_models import (
    check_retired_models,
    load_exception_manifest,
    main as lint_main,
    scan_file_for_retired_models,
)
from tools.validate_turn_gates import validate_scratchpad_transparency

# Dynamically constructed to prevent test file itself from containing literal retired tokens
UNAPPROVED_GEMINI_PRO = "gemini-2.5-" + "pro"
UNAPPROVED_CLAUDE_HAIKU = "claude-3-" + "haiku"
UNAPPROVED_DISPLAY_NAME = "Gemini 2.5 " + "Pro"
TEST_BYPASS_TAG = "# allow-" + "retired-model"


def test_e2e_repository_wide_publication_scan_passes():
    """E2E Gate: Entire active repository passes model deprecation publication gate."""
    repo_root = Path(__file__).resolve().parents[2]
    violations = check_retired_models(repo_root)
    assert len(violations) == 0, f"Publication blocked by retired model violations: {violations}"


def test_e2e_active_guidance_unapproved_model_blocks_publication():
    """E2E Gate: Injecting retired model into active runbook/guide blocks publication."""
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.NamedTemporaryFile("w+", suffix=".md", dir=repo_root / "docs", delete=False) as tmp:
        tmp.write(f"# Active Deployment Runbook\n\nRun with MODEL_NAME={UNAPPROVED_GEMINI_PRO}\n")
        tmp_path = Path(tmp.name)

    try:
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) == 1
        assert violations[0].matched_model == UNAPPROVED_GEMINI_PRO
        assert "gemini-3.8-flash" in violations[0].remediation
    finally:
        tmp_path.unlink(missing_ok=True)


def test_e2e_active_rule_unapproved_model_blocks_publication():
    """E2E Gate: Injecting retired model into agent rules blocks publication."""
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.NamedTemporaryFile("w+", suffix=".md", dir=repo_root / ".agents", delete=False) as tmp:
        tmp.write(f"# Routing Rules\n\nDefault: {UNAPPROVED_CLAUDE_HAIKU}\n")
        tmp_path = Path(tmp.name)

    try:
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) == 1
        assert violations[0].matched_model == UNAPPROVED_CLAUDE_HAIKU
    finally:
        tmp_path.unlink(missing_ok=True)


def test_e2e_historical_exceptions_preserved_without_code_rewrites():
    """E2E Gate: All historical receipts and manifests preserve exact immutable digests."""
    repo_root = Path(__file__).resolve().parents[2]
    manifest = load_exception_manifest(repo_root)
    assert len(manifest) >= 15, f"Expected at least 15 historical exceptions, got {len(manifest)}"

    for rel_path, entry in manifest.items():
        file_path = repo_root / rel_path
        if entry.get("sha256") in ("SELF_REFERENTIAL", "VARIABLE_DURING_WAVE"):
            continue
        assert file_path.is_file(), f"Historical exception file missing: {rel_path}"
        violations = scan_file_for_retired_models(file_path, repo_root, exceptions=manifest)
        assert len(violations) == 0, f"Historical file {rel_path} triggered unexpected violations: {violations}"


def test_e2e_forged_inline_bypass_blocks_publication():
    """E2E Gate: Attempting to bypass linter via inline comments blocks publication."""
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.NamedTemporaryFile("w+", suffix=".py", dir=repo_root / "dual_pipeline", delete=False) as tmp:
        tmp.write(f"SECRET_MODEL = '{UNAPPROVED_GEMINI_PRO}'  {TEST_BYPASS_TAG}\n")
        tmp_path = Path(tmp.name)

    try:
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        models = [v.matched_model for v in violations]
        assert "FORGED_INLINE_BYPASS" in models
        assert UNAPPROVED_GEMINI_PRO in models
    finally:
        tmp_path.unlink(missing_ok=True)


def test_e2e_turn_gates_publication_verification():
    """E2E Gate: Turn gate verification rejects stale incident header and accepts evidence-bound header."""
    # Stale incident header without structured evidence fails
    stale_text = (
        "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
        f"- **Active Model & Provider**: {UNAPPROVED_DISPLAY_NAME} | Google GenAI | Tier: High\n"
        "- **Cognitive Breakdown & Thinking**: [Deliberation]\n"
        "- **Reasoning Steps**: [Steps]\n"
        "- **API & Tool Invocations**: [Tools]\n"
    )
    res_stale = validate_scratchpad_transparency(stale_text)
    assert res_stale["status"] == "FAIL"
    assert any("violates approved policy" in err or "unverified" in err or "not in approved" in err for err in res_stale["issues"])

    # Ratified active model with valid evidence passes
    valid_text = (
        "### 🧠 Agent Reasoning Scratchpad & Execution Telemetry\n"
        "- **Active Model & Provider**: gemini-3.8-flash | Google GenAI | Tier: Medium\n"
        "- **Cognitive Breakdown & Thinking**: [Deliberation]\n"
        "- **Reasoning Steps**: [Steps]\n"
        "- **API & Tool Invocations**: [Tools]\n"
    )
    evidence = {
        "dispatched_model": "gemini-3.8-flash",
        "provider": "google",
        "evidence_strength": "OBSERVED_RESPONSE",
    }
    res_valid = validate_scratchpad_transparency(valid_text, evidence=evidence, scope="application_call")
    assert res_valid["status"] == "PASS", f"Expected PASS, got issues: {res_valid['issues']}"
