"""
tests/test_presentation_linter.py

Unit test suite for Wave 5: Decision Service & Presentation Linter (Anti-Scope-Bleed Gate).
Verifies:
1. RuleCompoundWorkstreamHeader flags compound table headers like 'Observed Field Reality (Project LENS / Sherpa)'.
2. Compliant header 'Observed Field Reality (Project LENS — Jen Pan)' passes cleanly with 0 violations.
3. RuleAttributionBleed flags Jen Pan under Project Beacon section or table row.
4. CLI entrypoint exits with code 1 on violations and code 0 when clean.
5. assert_clean_presentation raises PresentationLintError on violations.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.presentation_linter import (
    VIO_ATTRIBUTION_SCOPE_BLEED,
    PresentationLinter,
    PresentationLintError,
    main,
)


@pytest.fixture
def linter():
    return PresentationLinter()


def test_compound_workstream_header_blocked(linter: PresentationLinter):
    """Linter flags 'Observed Field Reality (Project LENS / Sherpa)' as compound header violation."""
    table = (
        "| Capability Area | PartnerAdvisory SOW Expectation | Observed Field Reality (Project LENS / Sherpa) |\n"
        "| :--- | :--- | :--- |\n"
        "| Delivery Lead / PM | Active, structured sprint governance | Present on LENS |\n"
    )
    violations = linter.lint_text(table)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_name == "RuleCompoundWorkstreamHeader"
    assert "Observed Field Reality (Project LENS / Sherpa)" in v.matched_text
    assert v.required_correction == "Observed Field Reality (Project LENS — Jen Pan)"
    assert VIO_ATTRIBUTION_SCOPE_BLEED in v.message

    with pytest.raises(PresentationLintError):
        linter.assert_clean_presentation(table)


def test_approved_workstream_header_passes(linter: PresentationLinter):
    """Linter approves 'Observed Field Reality (Project LENS — Jen Pan)' with 0 violations."""
    table = (
        "| Capability Area | PartnerAdvisory SOW Expectation | Observed Field Reality (Project LENS — Jen Pan) |\n"
        "| :--- | :--- | :--- |\n"
        "| Delivery Lead / PM | Active, structured sprint governance | Engagement PM on LENS |\n"
    )
    violations = linter.lint_text(table)
    assert len(violations) == 0
    # Must not raise
    linter.assert_clean_presentation(table)


def test_attribution_bleed_in_sherpa_section(linter: PresentationLinter):
    """Linter detects Jen Pan incorrectly attributed under Project Beacon section."""
    doc = (
        "### Project Overview\n\n"
        "#### Project Beacon Pod\n"
        "- Clay Palumbo: Former Tech Lead\n"
        "- Jen Pan: Engagement PM\n\n"
        "#### Project LENS Pod\n"
        "- Alex Sanchez: Tech Lead\n"
    )
    violations = linter.lint_text(doc)
    assert len(violations) == 1
    assert violations[0].rule_name == "RuleAttributionBleed"
    assert "Jen Pan" in violations[0].matched_text


def test_cli_exit_code_1_on_violation(tmp_path: Path):
    """CLI exits with code 1 when compound header is present."""
    bad_file = tmp_path / "bad_table.md"
    bad_file.write_text(
        "| Capability Area | Observed Field Reality (Project LENS / Sherpa) |\n|---|---|\n",
        encoding="utf-8",
    )
    exit_code = main([str(bad_file)])
    assert exit_code == 1


def test_cli_exit_code_0_on_clean(tmp_path: Path):
    """CLI exits with code 0 when clean."""
    clean_file = tmp_path / "clean_table.md"
    clean_file.write_text(
        "| Capability Area | Observed Field Reality (Project LENS — Jen Pan) |\n|---|---|\n",
        encoding="utf-8",
    )
    exit_code = main([str(clean_file)])
    assert exit_code == 0
