#!/usr/bin/env python3
"""Mechanical enforcement validator for pytest execution commands.

Enforces governance rules defined in .agents/rules/wave-progression.md:
1. Class A (Debug): strictly single test file or single test method.
2. Class B (Receipt): declared wave-scoped test files only.
3. Prohibits unbounded full-suite runs ('pytest', 'pytest tests/') without explicit authorization flag.
4. Requires timeout enforcement (either via command argument or pyproject.toml configuration).
5. Enforces Class A unit test duration limits (<= 3.0s).
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_CLASS_A_DURATION: float = 3.0

FLAGS_WITH_ARGS = {
    "-k",
    "-m",
    "-o",
    "-c",
    "--ignore",
    "--override-ini",
    "--rootdir",
    "-n",
    "--numprocesses",
    "--dist",
    "--maxprocesses",
    "--max-worker-restart",
    "-j",
    "-p",
    "--tb",
    "--durations",
    "-W",
}


def _parse_pyproject_timeout(content: str) -> tuple[bool, int | None]:
    in_pytest_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[tool.pytest.ini_options]":
            in_pytest_section = True
            continue
        if not in_pytest_section:
            continue
        if stripped.startswith("["):
            break
        if stripped.startswith("timeout") and "=" in stripped:
            val = stripped.split("=", 1)[1].split("#")[0].strip()
            try:
                return True, int(val)
            except ValueError:
                return True, None
    return False, None


def check_pyproject_timeout_configured(repo_root: Path) -> tuple[bool, int | None]:
    """Check if pyproject.toml configures tool.pytest.ini_options timeout."""
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return False, None
    try:
        content = pyproject_path.read_text(encoding="utf-8")
        return _parse_pyproject_timeout(content)
    except OSError:
        return False, None


def _clean_cmd_tokens(cmd: str | Sequence[str]) -> list[str]:
    if isinstance(cmd, str):
        cmd_clean = cmd.strip()
        if cmd_clean.startswith(('cmd /c "', 'cmd /c ')):
            if cmd_clean.startswith('cmd /c "') and cmd_clean.endswith('"'):
                cmd_clean = cmd_clean[8:-1]
            else:
                cmd_clean = cmd_clean[7:]
        tokens = shlex.split(cmd_clean, posix=False)
    else:
        tokens = list(cmd)
    return [tok.strip("\"'") for tok in tokens]


def _find_pytest_token_index(clean_tokens: list[str]) -> int:
    for idx, tok in enumerate(clean_tokens):
        tok_lower = tok.lower().replace("\\", "/")
        if tok_lower in ("pytest", "pytest.exe") or tok_lower.endswith("/pytest") or tok_lower.endswith("/pytest.exe"):
            return idx
        if tok_lower == "-m" and idx + 1 < len(clean_tokens) and clean_tokens[idx + 1].lower() == "pytest":
            return idx + 1
    return -1


def _handle_timeout_token(arg: str, next_arg: str | None) -> tuple[bool, int]:
    """Check for timeout argument and return (is_timeout, tokens_consumed)."""
    if arg.startswith("--timeout="):
        return True, 1
    if arg == "--timeout":
        if next_arg and not next_arg.startswith("-"):
            return True, 2
        return True, 1
    return False, 0


def _handle_flag_with_args(arg: str, next_arg: str | None) -> tuple[bool, int]:
    """Check if token is a flag with arguments and return (is_flag, tokens_consumed)."""
    if arg in FLAGS_WITH_ARGS:
        if next_arg and not next_arg.startswith("-"):
            return True, 2
        return True, 1
    if any(arg.startswith(f"{flag}=") for flag in FLAGS_WITH_ARGS):
        return True, 1
    return False, 0


def _extract_targets_and_flags(args: list[str]) -> tuple[list[str], bool, bool]:
    """Returns (target_items, has_timeout_flag, is_receipt_run)."""
    target_items: list[str] = []
    has_timeout_flag = False
    is_receipt_run = False
    i = 0
    while i < len(args):
        arg = args[i]
        next_arg = args[i + 1] if i + 1 < len(args) else None

        is_to, consumed = _handle_timeout_token(arg, next_arg)
        if is_to:
            has_timeout_flag = True
            i += consumed
            continue

        if arg == "--receipt-run":
            is_receipt_run = True
            i += 1
            continue

        is_flag, consumed = _handle_flag_with_args(arg, next_arg)
        if is_flag:
            i += consumed
            continue

        if not arg.startswith("-"):
            target_items.append(arg)
        i += 1
    return target_items, has_timeout_flag, is_receipt_run


def _validate_targets_scope(
    target_items: list[str],
    has_full_suite_flag: bool,
    is_receipt_run: bool,
) -> tuple[bool, str, str]:
    if not target_items:
        if has_full_suite_flag:
            return True, "AUTHORIZED_FULL_SUITE", "Authorized full-suite execution with explicit operator flag."
        return (
            False,
            "REJECTED",
            "GOVERNANCE_VIOLATION_FULL_SUITE: Unbounded pytest execution with no target file specified. "
            "Full-suite execution requires explicit operator authorization (--allow-full-suite).",
        )

    normalized_targets = [t.replace("\\", "/").rstrip("/") for t in target_items]
    if any(t in ("tests", "test", ".", "./tests") for t in normalized_targets):
        if has_full_suite_flag:
            return True, "AUTHORIZED_FULL_SUITE", "Authorized broad-scope execution with explicit operator flag."
        return (
            False,
            "REJECTED",
            f"GOVERNANCE_VIOLATION_BROAD_SCOPE: Target '{target_items}' runs the entire test directory. "
            "Scoping must target specific test files. Full suite requires explicit authorization.",
        )

    if len(target_items) == 1:
        target = target_items[0]
        if target.endswith(".py") or "::" in target:
            run_class = "CLASS_B_RECEIPT" if is_receipt_run else "CLASS_A_DEBUG"
            return True, run_class, f"Valid {run_class} run scoped to single target: {target}"
        return True, "CLASS_A_DEBUG", f"Valid scoped run: {target_items}"

    if all(t.endswith(".py") or "::" in t for t in target_items):
        return True, "CLASS_B_RECEIPT", f"Valid Class B wave-scoped receipt run targeting {len(target_items)} files."
    return (
        False,
        "REJECTED",
        f"GOVERNANCE_VIOLATION_INVALID_TARGETS: Multi-target arguments contain non-python files: {target_items}",
    )


def verify_pytest_command(
    cmd: str | Sequence[str],
    repo_root: Path | None = None,
    allow_full_suite: bool = False,
) -> tuple[bool, str, str]:
    """Verify a pytest command string or arg list against the governance protocol."""
    if repo_root is None:
        repo_root = ROOT

    clean_tokens = _clean_cmd_tokens(cmd)
    pytest_idx = _find_pytest_token_index(clean_tokens)
    if pytest_idx == -1:
        return False, "UNKNOWN", "Command does not invoke pytest."

    pytest_args = clean_tokens[pytest_idx + 1 :]
    has_full_suite_flag = allow_full_suite or "--allow-full-suite" in pytest_args
    filtered_args = [a for a in pytest_args if a != "--allow-full-suite"]

    target_items, has_timeout_flag, is_receipt_run = _extract_targets_and_flags(filtered_args)

    has_toml_timeout, _ = check_pyproject_timeout_configured(repo_root)
    if not has_timeout_flag and not has_toml_timeout:
        return (
            False,
            "REJECTED",
            "GOVERNANCE_VIOLATION_NO_TIMEOUT: pytest must enforce a timeout. "
            "Neither --timeout=<seconds> flag was supplied nor is 'timeout' configured in pyproject.toml.",
        )

    return _validate_targets_scope(target_items, has_full_suite_flag, is_receipt_run)


def check_test_duration(
    test_id: str,
    duration_seconds: float,
    max_duration: float = DEFAULT_MAX_CLASS_A_DURATION,
) -> tuple[bool, str]:
    """Verify that a Class A unit test duration is within the fast-feedback limit (<= 3.0s)."""
    if duration_seconds > max_duration:
        return (
            False,
            f"DURATION_VIOLATION: Unit test '{test_id}' took {duration_seconds:.2f}s, "
            f"exceeding maximum allowed {max_duration:.1f}s. Candidate for decoupling or mocking.",
        )
    return (
        True,
        f"DURATION_OK: Unit test '{test_id}' completed in {duration_seconds:.2f}s (threshold: {max_duration:.1f}s).",
    )


def check_test_durations_report(
    durations: Sequence[tuple[str, float]],
    max_duration: float = DEFAULT_MAX_CLASS_A_DURATION,
) -> tuple[bool, list[str]]:
    """Inspect a list of (test_id, duration_seconds) pairs against the duration threshold."""
    violations: list[str] = []
    for test_id, dur in durations:
        ok, msg = check_test_duration(test_id, dur, max_duration=max_duration)
        if not ok:
            violations.append(msg)
    return (len(violations) == 0, violations)


def _parse_cli_args(
    argv: Sequence[str],
) -> tuple[bool, Path, list[str], float, tuple[str, float] | None]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--allow-full-suite", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_CLASS_A_DURATION)
    parser.add_argument("--check-duration", type=str, default=None)

    known, remaining = parser.parse_known_args(argv)
    check_item = None
    if known.check_duration and ":" in known.check_duration:
        parts = known.check_duration.split(":", 1)
        try:
            check_item = (parts[0], float(parts[1]))
        except ValueError:
            pass

    return known.allow_full_suite, known.repo_root, remaining, known.max_duration, check_item


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    allow_full_suite, repo_root, cmd_tokens, max_duration, check_duration_item = _parse_cli_args(argv)

    if check_duration_item is not None:
        ok, msg = check_test_duration(check_duration_item[0], check_duration_item[1], max_duration=max_duration)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if not cmd_tokens:
        print(
            "Usage: check_pytest_compliance.py [--allow-full-suite] [--repo-root=<path>] "
            "[--check-duration=<test_name>:<seconds>] [--max-duration=<seconds>] <pytest command>",
            file=sys.stderr,
        )
        return 2

    cmd_str = " ".join(cmd_tokens).strip("\"'")
    is_compliant, run_class, message = verify_pytest_command(cmd_str, repo_root=repo_root, allow_full_suite=allow_full_suite)

    if is_compliant:
        print(f"COMPLIANT [{run_class}]: {message}")
        return 0
    print(f"NON_COMPLIANT [{run_class}]: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
