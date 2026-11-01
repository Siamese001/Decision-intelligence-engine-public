#!/usr/bin/env python3
"""
Credential Isolation & Secret Leak Scanner.

Enforces the AGENTS.md invariant and credential-isolation-and-egress-sanitization.md rule:
- Prohibits direct access to sensitive environment variables (OPENAI_API_KEY, AWS_*, etc.)
  outside designated credential SSOT modules.
- Scans for raw API key tokens or credential strings.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple

# Allowed SSOT modules that may read environment variables directly
ALLOWED_SSOT_FILES = {
    "storage/credentials.py",
    "storage/connection.py",
    "config/credentials.py",
    "config/secret_providers.py",
    "config/paths.py",
    "dual_pipeline/credentials.py",
}

# Sensitive environment variable names that must not be accessed directly
SENSITIVE_ENV_VARS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "HF_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
}

# Regex patterns for raw tokens
RAW_KEY_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

# Rule C-19 Career Leverage & Personal Advancement Firewall tokens prohibited in team deliverables
CAREER_LEVERAGE_PATTERNS = [
    re.compile(r"(?i)\$executive_compensation_band"),
    re.compile(r"(?i)private\s+political\s+power\s+map"),
    re.compile(r"(?i)confidential\s+career\s+notes"),
    re.compile(r"(?i)personal\s+advancement\s+vector"),
]

TEAM_FACING_PREFIXES = ("output/", "reports/", "docs/", "documentation/")


class LeakViolation(NamedTuple):
    file_path: str
    line_number: int
    leak_type: str
    detail: str


class CredentialLeakVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.violations: List[LeakViolation] = []

    def visit_Call(self, node: ast.Call):
        # Detect os.environ.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            # e.g., os.environ.get(...)
            if isinstance(node.func.value, ast.Attribute):
                if node.func.value.attr == "environ" and node.func.attr == "get":
                    func_name = "os.environ.get"
            elif isinstance(node.func.value, ast.Name):
                if node.func.value.id == "os" and node.func.attr == "getenv":
                    func_name = "os.getenv"
        elif isinstance(node.func, ast.Name) and node.func.id == "getenv":
            func_name = "getenv"

        if func_name and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                var_name = first_arg.value
                if var_name in SENSITIVE_ENV_VARS:
                    self.violations.append(
                        LeakViolation(
                            file_path=self.rel_path,
                            line_number=node.lineno,
                            leak_type="DIRECT_ENV_SECRET_READ",
                            detail=f"Direct call {func_name}('{var_name}') prohibited. Use storage.credentials.CredentialManager.",
                        )
                    )

        self.generic_visit(node)


def scan_file(file_path: Path, repo_root: Path) -> List[LeakViolation]:
    try:
        rel_path = str(file_path.relative_to(repo_root))
    except ValueError:
        rel_path = str(file_path)

    # Exclude tests, evals, and explicit SSOT files
    if "tests/" in rel_path or "test_" in rel_path or "evals/" in rel_path or rel_path in ALLOWED_SSOT_FILES:
        return []

    violations: List[LeakViolation] = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return []

    # Check raw key regex patterns
    for line_no, line in enumerate(content.splitlines(), start=1):
        for pat in RAW_KEY_PATTERNS:
            if pat.search(line) and not line.strip().startswith("#"):
                violations.append(
                    LeakViolation(
                        file_path=rel_path,
                        line_number=line_no,
                        leak_type="HARDCODED_SECRET_TOKEN",
                        detail=f"Potential hardcoded credential token detected matching {pat.pattern}",
                    )
                )

        # Rule C-19: Check career leverage tokens in team-facing directories
        if any(rel_path.startswith(prefix) for prefix in TEAM_FACING_PREFIXES) and "INTERNAL_ADVISORY" not in rel_path:
            for c_pat in CAREER_LEVERAGE_PATTERNS:
                if c_pat.search(line):
                    violations.append(
                        LeakViolation(
                            file_path=rel_path,
                            line_number=line_no,
                            leak_type="CAREER_FIREWALL_LEAK",
                            detail=f"Rule C-19 violation: Personal career leverage token detected in team deliverable: '{c_pat.pattern}'",
                        )
                    )

    # AST check for direct env reads (only Python files)
    if file_path.suffix == ".py":
        try:
            tree = ast.parse(content, filename=str(file_path))
            visitor = CredentialLeakVisitor(rel_path)
            visitor.visit(tree)
            violations.extend(visitor.violations)
        except Exception:
            pass

    return violations


def get_staged_files(repo_root: Path) -> List[Path]:
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
            p = (repo_root / line.strip()).resolve()
            if p.suffix in (".py", ".json", ".md") and p.exists():
                files.append(p)
        return files
    except Exception:
        return []


def scan_repo(repo_root: Path) -> List[LeakViolation]:
    target_files: List[Path] = []
    for gdir in ["pipeline_a_intake", "pipeline_b_extraction", "pipeline_c_retrieval", "dual_pipeline", "storage"]:
        dp = repo_root / gdir
        if dp.exists():
            for p in dp.rglob("*.py"):
                target_files.append(p.resolve())
    all_violations: List[LeakViolation] = []
    for fp in target_files:
        all_violations.extend(scan_file(fp, repo_root))
    return all_violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for raw credentials and unmanaged os.environ reads.")
    parser.add_argument("--staged", action="store_true", help="Only check staged git files")
    parser.add_argument("files", nargs="*", help="Specific files to check")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    target_files: List[Path] = []
    if args.staged:
        target_files = get_staged_files(repo_root)
    elif args.files:
        target_files = [Path(f).resolve() for f in args.files if Path(f).suffix == ".py"]
    else:
        for gdir in ["pipeline_a_intake", "pipeline_b_extraction", "pipeline_c_retrieval", "dual_pipeline", "storage"]:
            dp = repo_root / gdir
            if dp.exists():
                for p in dp.rglob("*.py"):
                    target_files.append(p.resolve())

    all_violations: List[LeakViolation] = []
    for fp in target_files:
        all_violations.extend(scan_file(fp, repo_root))

    print("=" * 80)
    print("  CREDENTIAL ISOLATION & LEAK SCANNER (SSOT Governance)")
    print("=" * 80)
    print(f"Scanned {len(target_files)} module(s).")

    if all_violations:
        print(f"\n❌ FAILED: Found {len(all_violations)} credential violation(s):\n")
        for v in all_violations:
            print(f"  - [{v.leak_type}] {v.file_path}:{v.line_number}")
            print(f"    {v.detail}\n")
        print("Remediation: Resolve all secrets through storage.credentials.CredentialManager.")
        print("=" * 80)
        return 1

    print("✅ PASSED: Zero credential leaks or unmanaged secret reads detected.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
