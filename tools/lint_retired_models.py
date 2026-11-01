#!/usr/bin/env python3
"""
Model Deprecation & Upstream Retirement Gatekeeper.

Detects and fails closed on references to retired or decommissioned LLM model
identifiers across the repository codebase, configuration files, and test suites.
Ensures no component dispatches unroutable API requests that fail upstream.
Enforces typed historical exception manifest to prevent blanket directory bypasses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Sequence, Set

# Upstream retired model identifiers with explicit migration targets
RETIRED_MODELS: Dict[str, str] = {
    "claude-3-haiku-20240307": "Retired on 2026-04-20. Recommended migration target: claude-haiku-4-5-20251001",  # noqa: SSOT_EXEMPT
    "claude-3-haiku": "Retired on 2026-04-20. Recommended migration target: claude-haiku-4-5",  # noqa: SSOT_EXEMPT
    "claude-3-sonnet-20240229": "Retired. Recommended migration target: claude-sonnet-5 or claude-sonnet-4-5",  # noqa: SSOT_EXEMPT
    "claude-2.0": "Retired. Recommended migration target: claude-sonnet-5",
    "claude-2.1": "Retired. Recommended migration target: claude-sonnet-5",
    "claude-instant-1.2": "Retired. Recommended migration target: claude-haiku-4-5",
    "gpt-4-0314": "Retired. Recommended migration target: gpt-5.6-luna",  # noqa: SSOT_EXEMPT
    "gpt-4-0613": "Retired. Recommended migration target: gpt-5.6-luna",  # noqa: SSOT_EXEMPT
    "gpt-3.5-turbo-0301": "Retired. Recommended migration target: gpt-5.6-luna",  # noqa: SSOT_EXEMPT
    "gpt-3.5-turbo-0613": "Retired. Recommended migration target: gpt-5.6-luna",  # noqa: SSOT_EXEMPT
    "gemini-1.0-pro": "Retired. Recommended migration target: gemini-3.8-flash",  # noqa: SSOT_EXEMPT
    "gemini-1.5-pro": "Retired/Banned. Recommended migration target: gemini-3.8-flash",  # noqa: SSOT_EXEMPT
    "gemini-2.5-flash": "Retired/Banned. Recommended migration target: gemini-3.8-flash (high reasoning)",  # noqa: SSOT_EXEMPT
    "gemini-2.5-pro": "Retired/Banned. Recommended migration target: gemini-3.8-flash (high reasoning)",  # noqa: SSOT_EXEMPT
}

# Single pattern for backwards compatibility
RETIRED_MODEL_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in RETIRED_MODELS) + r")\b"
)

# Build case-insensitive patterns supporting whitespace and punctuation variations
def _build_retired_patterns() -> tuple[re.Pattern[str], List[tuple[str, re.Pattern[str]]]]:
    patterns: List[str] = []
    pattern_to_id: List[tuple[str, re.Pattern[str]]] = []
    for model_id in sorted(RETIRED_MODELS.keys(), key=len, reverse=True):
        parts = re.split(r"[-_]", model_id)
        pat_str = r"[-_\s]*".join(re.escape(p) for p in parts)
        patterns.append(pat_str)
        pattern_to_id.append((model_id, re.compile(r"\b" + pat_str + r"\b", re.IGNORECASE)))
    combined = re.compile(r"\b(" + "|".join(patterns) + r")\b", re.IGNORECASE)
    return combined, pattern_to_id

RETIRED_MODEL_COMBINED_PATTERN, RETIRED_MODEL_PATTERNS = _build_retired_patterns()

# Relative path to canonical historical exception manifest
EXCEPTION_MANIFEST_REL_PATH = Path("reports/model_identity_ssot/historical_exception_manifest.json")

IGNORED_DIRECTORIES = (
    ".git/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".worktrees/",
)

BYPASS_TAGS = (
    "# allow-retired-model",
    "# allow-banned-model",
    "# noqa: RETIRED_MODEL",
    "# noqa: BANNED_MODEL",
)


class DeprecationViolation(NamedTuple):
    file_path: Path
    line_number: int
    matched_model: str
    remediation: str
    line_content: str


def compute_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 digest of file content."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_exception_manifest(repo_root: Path) -> Dict[str, dict]:
    """Load and index exceptions by normalized relative path."""
    manifest_path = repo_root / EXCEPTION_MANIFEST_REL_PATH
    if not manifest_path.is_file():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        exceptions_map: Dict[str, dict] = {}
        for entry in data.get("exceptions", []):
            path_str = str(entry.get("path", "")).replace("\\", "/")
            if path_str:
                exceptions_map[path_str] = entry
        return exceptions_map
    except Exception:
        return {}


def get_file_exception(
    file_path: Path,
    repo_root: Path,
    exceptions: Dict[str, dict] | None = None,
) -> tuple[bool, Sequence[str], str]:
    """
    Check if a file has an authorized exception in the manifest.
    Returns: (is_valid_exception, allowed_models, error_reason)
    """
    if file_path.is_relative_to(repo_root):
        rel_str = str(file_path.relative_to(repo_root)).replace("\\", "/")
    else:
        try:
            rel_str = str(file_path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_str = str(file_path).replace("\\", "/")

    if any(ignored in rel_str or rel_str.startswith(ignored) for ignored in IGNORED_DIRECTORIES):
        return True, ["ALL_RETIRED_MODELS"], ""

    if exceptions is None:
        exceptions = load_exception_manifest(repo_root)

    entry = exceptions.get(rel_str)
    if entry is None:
        return False, [], "NO_EXCEPTION_ENTRY"

    expected_sha = entry.get("sha256", "")
    allowed_models = entry.get("allowed_models", [])

    if expected_sha in ("SELF_REFERENTIAL", "VARIABLE_DURING_WAVE"):
        return True, allowed_models, ""

    if not file_path.is_file():
        return False, [], "FILE_NOT_FOUND"

    actual_sha = compute_sha256(file_path)
    if actual_sha != expected_sha:
        return False, [], f"SHA256_MISMATCH: expected {expected_sha}, got {actual_sha}"

    return True, allowed_models, ""


def is_path_exempt(path: Path, repo_root: Path) -> bool:
    """Backwards compatibility check for full exemption."""
    is_valid, allowed_models, _ = get_file_exception(path, repo_root)
    return is_valid and ("ALL_RETIRED_MODELS" in allowed_models)


def _resolve_matched_canonical_model(matched_str: str, line: str) -> str:
    norm = re.sub(r"[\s_]+", "-", matched_str.strip().lower())
    if norm in RETIRED_MODELS:
        return norm
    for mid, pat in RETIRED_MODEL_PATTERNS:
        if pat.search(line):
            return mid
    return matched_str


def _check_line_for_violations(
    line: str,
    idx: int,
    file_path: Path,
    is_valid_exception: bool,
    allowed_models: Set[str],
    check_bypass: bool,
    check_model: bool,
) -> List[DeprecationViolation]:
    found: List[DeprecationViolation] = []
    if check_bypass and any(tag in line for tag in BYPASS_TAGS):
        found.append(
            DeprecationViolation(
                file_path=file_path,
                line_number=idx,
                matched_model="FORGED_INLINE_BYPASS",
                remediation="Inline bypass comments are barred on active surfaces. Register in historical_exception_manifest.json.",
                line_content=line.strip(),
            )
        )
    if check_model:
        match = RETIRED_MODEL_COMBINED_PATTERN.search(line)
        if match:
            canonical_id = _resolve_matched_canonical_model(match.group(0), line)
            if not (is_valid_exception and canonical_id in allowed_models):
                remediation = RETIRED_MODELS.get(
                    canonical_id, "Model is retired. Migrate to a supported model."
                )
                found.append(
                    DeprecationViolation(
                        file_path=file_path,
                        line_number=idx,
                        matched_model=canonical_id,
                        remediation=remediation,
                        line_content=line.strip(),
                    )
                )
    return found


def scan_file_for_retired_models(
    file_path: Path,
    repo_root: Path,
    exceptions: Dict[str, dict] | None = None,
) -> List[DeprecationViolation]:
    """Scan a single file for retired model strings and unauthorized bypasses."""
    if file_path.is_relative_to(repo_root):
        rel_str = str(file_path.relative_to(repo_root)).replace("\\", "/")
    else:
        try:
            rel_str = str(file_path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_str = str(file_path).replace("\\", "/")

    if any(ignored in rel_str or rel_str.startswith(ignored) for ignored in IGNORED_DIRECTORIES):
        return []

    is_valid_exception, allowed_models, error_reason = get_file_exception(file_path, repo_root, exceptions)
    violations: List[DeprecationViolation] = []

    if error_reason.startswith("SHA256_MISMATCH"):
        violations.append(
            DeprecationViolation(
                file_path=file_path,
                line_number=1,
                matched_model="INTEGRITY_TAMPERED",
                remediation=f"Historical exception manifest hash mismatch: {error_reason}",
                line_content="",
            )
        )
        return violations

    if is_valid_exception and "ALL_RETIRED_MODELS" in allowed_models:
        return []

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    has_bypass_in_file = any(tag in content for tag in BYPASS_TAGS)
    has_model_match = bool(RETIRED_MODEL_COMBINED_PATTERN.search(content))

    if not has_bypass_in_file and not has_model_match:
        return []

    check_bypass = has_bypass_in_file and not is_valid_exception
    for idx, line in enumerate(content.splitlines(), start=1):
        line_vs = _check_line_for_violations(
            line, idx, file_path, is_valid_exception, allowed_models, check_bypass, has_model_match
        )
        violations.extend(line_vs)
    return violations


def get_tracked_files(repo_root: Path) -> List[Path]:
    """Retrieve list of git tracked and untracked repository files."""
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        tracked = [repo_root / line.strip() for line in res.stdout.splitlines() if line.strip()]
        res_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        untracked = [repo_root / line.strip() for line in res_untracked.stdout.splitlines() if line.strip()]
        return tracked + untracked
    except Exception:
        found: List[Path] = []
        for root, _, files in os.walk(repo_root):
            for file in files:
                p = Path(root) / file
                found.append(p)
        return found


def check_retired_models(repo_root: Path, target_files: Sequence[Path] | None = None) -> List[DeprecationViolation]:
    """Main verification routine returning all detected model deprecation violations."""
    exceptions = load_exception_manifest(repo_root)
    all_violations: List[DeprecationViolation] = []

    if target_files is not None:
        files = list(target_files)
    else:
        try:
            grep_pattern = "|".join(re.escape(k) for k in RETIRED_MODELS) + "|allow-retired-model|allow-banned-model"
            res = subprocess.run(
                ["git", "grep", "-l", "-E", "-I", "-i", grep_pattern],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            candidate_set = {repo_root / p.strip() for p in res.stdout.splitlines() if p.strip()}
            for rel_path in exceptions:
                candidate_set.add(repo_root / rel_path)
            res_untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for p in res_untracked.stdout.splitlines():
                candidate_set.add(repo_root / p.strip())
            files = list(candidate_set)
        except Exception:
            files = get_tracked_files(repo_root)

    for file_path in files:
        if file_path.is_file():
            all_violations.extend(scan_file_for_retired_models(file_path, repo_root, exceptions))
    return all_violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model Deprecation & Upstream Retirement Gatekeeper")
    parser.add_argument("--check", action="store_true", help="Perform full repository scan and return exit code 1 on violations")
    parser.add_argument("files", nargs="*", type=Path, help="Specific files to lint")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    target_files = [f.resolve() for f in args.files] if args.files else None

    violations = check_retired_models(repo_root, target_files)

    if not violations:
        print("[MODEL-DEPRECATION-GATE] PASS: Zero retired or decommissioned model strings detected.")
        return 0

    print("=" * 80)
    print(f"[MODEL-DEPRECATION-GATE] FAILED: {len(violations)} retired model references detected!")
    print("=" * 80)
    for v in violations:
        rel = str(v.file_path.relative_to(repo_root) if v.file_path.is_relative_to(repo_root) else v.file_path)
        print(f"\n❌ Violation in {rel}:{v.line_number}")
        print(f"   Retired Model: {v.matched_model}")
        print(f"   Remediation:   {v.remediation}")
        print(f"   Line Content:  {v.line_content}")
    print("\n" + "=" * 80)
    return 1


if __name__ == "__main__":
    sys.exit(main())
