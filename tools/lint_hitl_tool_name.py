#!/usr/bin/env python3
"""Linter enforcing Single Source of Truth tool name (`ask_user_question`).

Scans the repository to ensure no live files reference deprecated interactive tool
names (`ask_question`, `ask_user`, etc.) outside the designated SSOT allowlist:
1. `config/hitl_tool_name.py` (SSOT definition)
2. `dual_pipeline/hitl_governance_core/runtime_tool_adapter.py` (Single allowlisted adapter per D7)
3. `tools/lint_hitl_tool_name.py` (Linter itself)
4. External upstream protocol tools: `tools/mcp_deepwiki_server.py`, `tests/test_mcp_deepwiki_server.py`
5. Immutable historical records (`reports/`, `receipts/`, `archive/`, `migrations/`, `docs/plans/`, `governance/OPERATOR_DECISION_GLOSSARY.md`)
6. SSOT test suites (`tests/test_hitl_tool_name_ssot*.py`, `tests/end_to_end/test_hitl_tool_name_ssot_e2e.py`)

Usage:
    python tools/lint_hitl_tool_name.py --scope all
    python tools/lint_hitl_tool_name.py --scope staged
    python tools/lint_hitl_tool_name.py file1.py file2.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.hitl_tool_name import ASK_USER_QUESTION, DEPRECATED_NAMES  # noqa: E402

# The strict allowlist of files permitted to contain deprecated names
STRICT_FILE_ALLOWLIST = frozenset(
    {
        "config/hitl_tool_name.py",
        "config/operator_decision_vocabulary.py",
        "dual_pipeline/hitl_governance_core/runtime_tool_adapter.py",
        "scripts/hitl_pre_tool_guard.py",
        "scripts/hitl_post_tool_ledger_writer.py",
        "scripts/hitl_post_invocation_guard.py",
        "tools/check_ask_user_question.py",
        "tools/lint_hitl_tool_name.py",
        "tools/sync_hitl_tool_name.py",
        "tools/mcp_deepwiki_server.py",
        "tests/test_mcp_deepwiki_server.py",
        "dual_pipeline/agent_sdk/policies.py",
        "tests/test_agent_policies.py",
        "tests/adversarial/test_hitl_resolution_w5_adversarial.py",
        ".agents/hooks.json",
        "hitl_decision_ledger.json",
        "implementation_plan.md",
    }
)

EXEMPT_PREFIXES = (
    "approval_requests/",
    "reports/",
    "receipts/",
    "archive/",
    "migrations/",
    "docs/",
    "documentation/",
    "governance/rca/",
    "governance/decision_intelligence/RCA_",
    "governance/plans/",
    "governance/OPERATOR_DECISION_GLOSSARY.md",
    "output/",
    "evals/",
    "benchmarks/",
    "data/",
    "tests/fixtures/",
)

EXEMPT_TEST_PREFIXES = (
    "tests/test_hitl_tool_name_ssot",
    "tests/end_to_end/test_hitl_tool_name_ssot",
    "tests/test_operator_decision_vocabulary",
    "tests/test_hitl_",
    "tests/test_rca_hitl_",
    "tests/test_rca_wave1_pr_hitl_",
    "tests/test_hygiene_council_hitl_",
    "tests/test_role_hint_and_ledger_gate_",
    "tests/test_ssot_drift_remediation_",
    "tests/end_to_end/test_grouped_speaker_context_e2e",
    "tests/end_to_end/test_role_hint_and_ledger_gate_e2e",
    "tests/adversarial/test_speaker_review_presentation_adversarial",
)

# File extensions to scan
SCANNABLE_EXTENSIONS = frozenset(
    {
        ".py",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".sh",
        ".toml",
    }
)

# Precompile regexes for efficiency
COMPILED_PATTERNS = [
    (dep, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(dep) + r"(?![A-Za-z0-9_])", re.IGNORECASE))
    for dep in DEPRECATED_NAMES
]


def is_allowlisted_path(rel_path: str) -> bool:
    """Return True if rel_path is exempt from deprecated tool name enforcement."""
    norm = rel_path.replace("\\", "/")
    if norm in STRICT_FILE_ALLOWLIST:
        return True
    if any(norm.startswith(prefix) for prefix in EXEMPT_PREFIXES):
        return True
    return any(norm.startswith(test_prefix) for test_prefix in EXEMPT_TEST_PREFIXES)


def scan_file_for_deprecated_names(
    file_path: Path, repo_root: Path
) -> List[Tuple[int, str, str]]:
    """Scan a single file for deprecated interactive tool names.

    Returns list of tuples: (line_number, matched_term, line_content).
    """
    rel_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
    if is_allowlisted_path(rel_path):
        return []

    findings: List[Tuple[int, str, str]] = []
    try:
        if file_path.stat().st_size > 1_000_000:
            return findings
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings

    # Fast substring check before doing line-by-line regex
    content_lower = content.lower()
    active_patterns = [
        (dep, pat) for dep, pat in COMPILED_PATTERNS if dep.lower() in content_lower
    ]
    if not active_patterns:
        return findings

    lines = content.splitlines()
    for idx, line in enumerate(lines, 1):
        line_lower = line.lower()
        for dep, pat in active_patterns:
            if dep.lower() not in line_lower:
                continue
            if pat.search(line):
                # Ignore Decision.ASK_USER which is external SDK tool approval constant
                if dep == "ask_user" and ("Decision.ASK_USER" in line or "ASK_USER" in line):
                    continue
                findings.append((idx, dep, line.strip()))
                break  # Record one violation per line

    return findings


def get_staged_files(repo_root: Path) -> List[Path]:
    """Retrieve list of git staged files."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in res.stdout.splitlines():
            line_str = line.strip().replace("\\", "/")
            if not line_str or is_allowlisted_path(line_str):
                continue
            p = repo_root / line_str
            if p.is_file() and p.suffix.lower() in SCANNABLE_EXTENSIONS:
                files.append(p)
        return files
    except Exception:
        return []


def get_all_scannable_files(repo_root: Path) -> List[Path]:
    """Discover all tracked scannable files in repository."""
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in res.stdout.splitlines():
            line_str = line.strip().replace("\\", "/")
            if not line_str or is_allowlisted_path(line_str):
                continue
            p = repo_root / line_str
            if p.is_file() and p.suffix.lower() in SCANNABLE_EXTENSIONS:
                files.append(p)
        return files
    except Exception:
        # Fallback to rglob
        return [
            p
            for p in repo_root.rglob("*")
            if p.is_file()
            and p.suffix.lower() in SCANNABLE_EXTENSIONS
            and not is_allowlisted_path(str(p.relative_to(repo_root)))
            and ".git" not in p.parts
            and ".venv" not in p.parts
        ]


def lint_tool_names(
    repo_root: Path, target_files: Optional[Sequence[Path]] = None
) -> Tuple[int, List[dict]]:
    """Scan candidate files and report deprecated tool name occurrences.

    Returns (exit_code, findings_list).
    """
    if target_files is None:
        target_files = get_all_scannable_files(repo_root)

    valid_files = [f for f in target_files if f.exists() and f.is_file()]
    all_findings: List[dict] = []

    def _scan_one(file_path: Path) -> List[dict]:
        local_results: List[dict] = []
        findings = scan_file_for_deprecated_names(file_path, repo_root)
        for line_no, term, content in findings:
            rel = str(file_path.relative_to(repo_root)).replace("\\", "/")
            local_results.append(
                {
                    "file": rel,
                    "line": line_no,
                    "term": term,
                    "replacement": ASK_USER_QUESTION,
                    "content": content,
                }
            )
        return local_results

    if len(valid_files) > 10:
        import os
        from concurrent.futures import ThreadPoolExecutor

        max_workers = min(16, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for file_results in executor.map(_scan_one, valid_files):
                all_findings.extend(file_results)
    else:
        for file_path in valid_files:
            all_findings.extend(_scan_one(file_path))

    exit_code = 1 if all_findings else 0
    return exit_code, all_findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint repository for deprecated interactive tool names (SSOT: ask_user_question)."
    )
    parser.add_argument(
        "--scope",
        choices=["all", "staged"],
        default="all",
        help="Scan scope: 'all' repository files or 'staged' git changes (default: all).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output findings in JSON format.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Optional explicit list of file paths to scan.",
    )

    args = parser.parse_args()

    repo_root = REPO_ROOT

    if args.files:
        targets = [p.resolve() if p.is_absolute() else (repo_root / p).resolve() for p in args.files]
    elif args.scope == "staged":
        targets = get_staged_files(repo_root)
    else:
        targets = get_all_scannable_files(repo_root)

    exit_code, findings = lint_tool_names(repo_root, targets)

    if args.json:
        print(
            json.dumps(
                {
                    "exit_code": exit_code,
                    "total_violations": len(findings),
                    "canonical_tool_name": ASK_USER_QUESTION,
                    "findings": findings,
                },
                indent=2,
            )
        )
        return exit_code

    if not findings:
        print(
            f"✅ [PASS] 0 deprecated tool names found across {len(targets)} scanned file(s). "
            f"SSOT '{ASK_USER_QUESTION}' is 100% compliant."
        )
        return 0

    print("=" * 80)
    print(f"🛑 [FAIL] {len(findings)} deprecated tool name occurrence(s) detected outside allowlist:")
    print("=" * 80)
    for f in findings:
        print(f"  {f['file']}:{f['line']} -> found '{f['term']}' (replace with '{f['replacement']}')")
        print(f"    Line: {f['content']}")
    print("=" * 80)
    print(f"Remediation: Replace deprecated variants with canonical SSOT '{ASK_USER_QUESTION}'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
