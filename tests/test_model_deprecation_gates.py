"""
Unit and Adversarial Regression Suite for Model Deprecation Gates.

Verifies:
1. Active repository is 100% clean of retired or decommissioned model strings.
2. Injecting retired model tokens (claude-3-haiku-20240307, gemini-2.5-pro, etc.) fails closed.
3. Case, whitespace, and alias variations (Gemini 2.5 Pro, Claude 3 Haiku) fail closed.
4. Forged inline bypass comments without manifest authorization fail closed.
5. Exception manifest hash tampering fails closed.
6. Modern supported models pass without false positives.
7. CLI --check returns exit code 0 when clean and 1 on violations.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lint_retired_models import (
    RETIRED_MODELS,
    check_retired_models,
    main,
    scan_file_for_retired_models,
)


def test_repository_clean_of_retired_models():
    """Verify repository has 0 references to upstream decommissioned models."""
    repo_root = Path(__file__).resolve().parents[1]
    violations = check_retired_models(repo_root)
    assert len(violations) == 0, f"Found retired model violations: {violations}"


def test_adversarial_retired_claude_haiku3_detected():
    """Adversarial test: verify claude-3-haiku-20240307 is flagged with remediation."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write("MODEL = 'claude-3-haiku-20240307'\n")
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) == 1
        assert violations[0].matched_model == "claude-3-haiku-20240307"
        assert "claude-haiku-4-5-20251001" in violations[0].remediation
    finally:
        tmp_path.unlink(missing_ok=True)


def test_adversarial_retired_gemini25_pro_detected():
    """Adversarial test: verify gemini-2.5-pro is flagged with high reasoning remediation."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write("TARGET_MODEL = 'gemini-2.5-pro'\n")
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) == 1
        assert violations[0].matched_model == "gemini-2.5-pro"
        assert "gemini-3.8-flash" in violations[0].remediation
    finally:
        tmp_path.unlink(missing_ok=True)


def test_adversarial_case_and_whitespace_variations_detected():
    """Adversarial test: verify display and casing variations are intercepted."""
    cases = [
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("gemini 2.5 pro", "gemini-2.5-pro"),
        ("GEMINI-2.5-PRO", "gemini-2.5-pro"),
        ("Gemini 1.5 Pro", "gemini-1.5-pro"),
        ("Claude 3 Haiku", "claude-3-haiku"),
    ]
    for raw_str, expected_id in cases:
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
            tmp.write(f"HEADER = '{raw_str}'\n")
            tmp_path = Path(tmp.name)

        try:
            repo_root = tmp_path.parent
            violations = scan_file_for_retired_models(tmp_path, repo_root)
            assert len(violations) == 1, f"Expected 1 violation for {raw_str}, got {len(violations)}"
            assert violations[0].matched_model == expected_id
        finally:
            tmp_path.unlink(missing_ok=True)


def test_adversarial_forged_inline_bypass_detected():
    """Adversarial test: verify forged bypass comments in unapproved files fail closed."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write("MODEL = 'gemini-2.5-pro'  # allow-retired-model\n")
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        violation_types = [v.matched_model for v in violations]
        assert "FORGED_INLINE_BYPASS" in violation_types
        assert "gemini-2.5-pro" in violation_types
    finally:
        tmp_path.unlink(missing_ok=True)


def test_adversarial_manifest_tamper_detected():
    """Adversarial test: verify file hash mismatch in exception manifest fails closed."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write("MODEL = 'gemini-2.5-pro'\n")
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        tampered_exceptions = {
            tmp_path.name: {
                "path": tmp_path.name,
                "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "allowed_models": ["gemini-2.5-pro"],
            }
        }
        violations = scan_file_for_retired_models(tmp_path, repo_root, exceptions=tampered_exceptions)
        assert len(violations) == 1
        assert violations[0].matched_model == "INTEGRITY_TAMPERED"
    finally:
        tmp_path.unlink(missing_ok=True)


def test_authorized_exception_with_valid_hash_passes():
    """Verify that an authorized file in the manifest with correct hash passes cleanly."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        content = "MODEL = 'gemini-2.5-pro'\n"
        tmp.write(content)
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        valid_exceptions = {
            tmp_path.name: {
                "path": tmp_path.name,
                "sha256": actual_sha,
                "allowed_models": ["gemini-2.5-pro"],
            }
        }
        violations = scan_file_for_retired_models(tmp_path, repo_root, exceptions=valid_exceptions)
        assert len(violations) == 0
    finally:
        tmp_path.unlink(missing_ok=True)


def test_adversarial_retired_all_catalog_models_detected():
    """Adversarial test: verify every retired model identifier is caught."""
    for model_id in RETIRED_MODELS:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
            tmp.write(f'{{"target_model": "{model_id}"}}\n')
            tmp_path = Path(tmp.name)

        try:
            repo_root = tmp_path.parent
            violations = scan_file_for_retired_models(tmp_path, repo_root)
            assert len(violations) == 1
            assert violations[0].matched_model == model_id
        finally:
            tmp_path.unlink(missing_ok=True)


def test_modern_frontier_models_pass_cleanly():
    """Verify modern 2026 frontier models pass without false positives."""
    valid_content = """
    MODEL_SONNET = "claude-sonnet-5"
    MODEL_HAIKU = "claude-haiku-4-5-20251001"
    MODEL_GPT = "gpt-5.6-luna"
    MODEL_GEMINI = "gemini-3.8-flash"
    """
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write(valid_content)
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) == 0
    finally:
        tmp_path.unlink(missing_ok=True)


def test_cli_check_clean(capsys):
    """Verify CLI --check exits 0 on clean runs."""
    exit_code = main(["--check"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PASS" in captured.out


def test_cli_check_fails_on_violation(capsys):
    """Verify CLI returns exit code 1 when target file contains retired model."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write("FALLBACK_MODEL = 'gpt-3.5-turbo-0613'\n")
        tmp_path = Path(tmp.name)

    try:
        exit_code = main([str(tmp_path)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "gpt-3.5-turbo-0613" in captured.out
        assert "FAILED" in captured.out
    finally:
        tmp_path.unlink(missing_ok=True)
