"""
dual_pipeline/integrations/ci_cd.py

CI/CD integration utilities for GitHub Actions and pipeline gating.
Formats GitHub workflow commands (annotations, step summaries) and
exports test execution results for CI visibility.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


def format_github_annotation(
    severity: str,  # 'error', 'warning', 'notice'
    message: str,
    file_path: Optional[str] = None,
    line: Optional[int] = None,
    col: Optional[int] = None,
    title: Optional[str] = None,
) -> str:
    """
    Formats a GitHub Actions workflow annotation command.
    Reference: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions
    Syntax: ::error file={name},line={line},col={col},title={title}::{message}
    """
    params: List[str] = []
    if file_path:
        params.append(f"file={file_path}")
    if line is not None:
        params.append(f"line={line}")
    if col is not None:
        params.append(f"col={col}")
    if title:
        params.append(f"title={title}")

    param_str = f" {','.join(params)}" if params else ""
    clean_msg = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{severity}{param_str}::{clean_msg}"


def generate_github_step_summary(
    title: str,
    wave_table_rows: List[Dict[str, str]],
    gate_results: Dict[str, bool],
) -> str:
    """
    Generates GitHub Flavored Markdown for $GITHUB_STEP_SUMMARY.
    """
    lines: List[str] = [
        f"## {title}",
        "",
        "### Wave Implementation & Completion Status",
        "| Wave Number | Description / Scope | Completion Status | Runtime Receipt & Verification Gate |",
        "|:---|:---|:---|:---|",
    ]
    for row in wave_table_rows:
        num = row.get("wave", "")
        desc = row.get("description", "")
        status = row.get("status", "")
        receipt = row.get("receipt", "")
        lines.append(f"| {num} | {desc} | {status} | {receipt} |")

    lines.extend([
        "",
        "### Pre-Commit / Release Gates",
        "| Gate | Outcome |",
        "|:---|:---|",
    ])
    for gate, passed in gate_results.items():
        emoji = "✅ PASS" if passed else "❌ FAIL"
        lines.append(f"| {gate} | {emoji} |")

    lines.append("")
    return "\n".join(lines)


def write_to_github_summary(markdown_content: str) -> bool:
    """Appends markdown content to $GITHUB_STEP_SUMMARY if running in GitHub Actions."""
    from storage.credentials import CredentialManager
    summary_file = CredentialManager.get_env("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return False
    try:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write("\n" + markdown_content + "\n")
        return True
    except Exception:
        return False
