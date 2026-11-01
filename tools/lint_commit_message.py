#!/usr/bin/env python3
"""
Conventional Commit Message Linter for Decision Intelligence Engine.

Validates that commit messages adhere to the Conventional Commits specification:
  <type>(<optional-scope>): <description>

Supported types:
  feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert

Also supports standard Git merge commit messages:
  Merge branch '<branch>' into main (<Topic>)
  Merge pull request #<num> ...
  Merge branch '...' ...
  merge: <description>

Usage:
  python tools/lint_commit_message.py -m "feat(pre-commit): add commit-msg hook"
  python tools/lint_commit_message.py -f .git/COMMIT_EDITMSG
  python tools/lint_commit_message.py "feat(pre-commit): add commit-msg hook"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Sequence

CONVENTIONAL_COMMIT_REGEX = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\((?P<scope>[a-zA-Z0-9_\-\./]+)\))?"
    r"(?P<breaking>!)?"
    r":\s+"
    r"(?P<description>.+)$"
)

MERGE_COMMIT_PATTERNS = [
    re.compile(r"^Merge branch '[^']+'(\s+into\s+[^\s]+)?(\s+\([^\)]+\))?.*$", re.IGNORECASE),
    re.compile(r"^Merge remote-tracking branch '[^']+'.*$", re.IGNORECASE),
    re.compile(r"^Merge pull request #\d+.*$", re.IGNORECASE),
    re.compile(r"^merge:\s+.+$", re.IGNORECASE),
]

DIRTY_DUMP_REGEX = re.compile(
    r"(?i)\b(dirty|wip|temp dump|save files|save changes|checkpoint dump|dirty files)\b"
)

PROHIBITED_MODEL_REGEX = re.compile(
    r"(?i)\b(gpt-?[0-9a-z]*|astra|sol[-_](?:max|medium|high)|luna|terra|claude[-_0-9a-z]*|gemini[-_0-9a-z]*|llama[-_0-9a-z]*)\b"
)


def validate_commit_message(msg: str) -> tuple[bool, str]:
    """Validates a commit message string.

    Returns:
        tuple of (is_valid, explanation_or_error)
    """
    # Strip comments (lines starting with #) and leading/trailing whitespace
    clean_lines = [
        line for line in msg.splitlines()
        if not line.strip().startswith("#")
    ]
    if not clean_lines:
        return False, "Commit message cannot be empty or contain only comments."

    first_line = clean_lines[0].strip()
    if not first_line:
        return False, "Commit message summary line cannot be empty."

    # Check for merge commits
    for pattern in MERGE_COMMIT_PATTERNS:
        if pattern.match(first_line):
            text_to_check = re.sub(r"^Merge (?:branch|remote-tracking branch) '[^']+'", "", first_line)
            model_match = PROHIBITED_MODEL_REGEX.search(text_to_check)
            if model_match:
                return False, f"Prohibited model identifier '{model_match.group(0)}' in commit message (violates Rule C-07 / Model Neutrality). Use domain-functional descriptions."
            return True, f"Valid merge commit format: '{first_line}'"

    # Reject dirty-dump keywords
    dirty_match = DIRTY_DUMP_REGEX.search(first_line)
    if dirty_match:
        return False, f"Prohibited dirty-dump keyword '{dirty_match.group(0)}' in commit message. Commits must be deliberate, atomic, and conventional."

    # Reject model names (Rule C-07 / Model Neutrality)
    model_match = PROHIBITED_MODEL_REGEX.search(first_line)
    if model_match:
        return False, f"Prohibited model identifier '{model_match.group(0)}' in commit message (violates Rule C-07 / Model Neutrality). Use domain-functional descriptions."

    # Check for Conventional Commit syntax
    match = CONVENTIONAL_COMMIT_REGEX.match(first_line)
    if match:
        desc = match.group("description").strip()
        if len(desc) < 3:
            return False, f"Description '{desc}' is too short (minimum 3 characters required)."
        return True, f"Valid Conventional Commit: '{first_line}'"

    # Format error with helpful explanation
    error_msg = (
        f"Invalid commit message header: '{first_line}'\n\n"
        "Must follow Conventional Commits specification:\n"
        "  <type>(<scope>): <subject>\n\n"
        "Allowed types:\n"
        "  feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert\n\n"
        "Examples:\n"
        "  feat(pre-commit): add commit-msg hook and consolidate structural linters\n"
        "  fix(qa): resolve type annotation gaps in test suite\n"
        "  chore(deps): update runtime dependency pins\n"
        "  Merge branch 'feature/foo' into main (Governance)\n"
    )
    return False, error_msg


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Conventional Commit message linter.")
    parser.add_argument("message", nargs="?", default=None, help="Commit message string")
    parser.add_argument("-m", "--message-str", dest="flag_msg", help="Commit message string via flag")
    parser.add_argument("-f", "--file", type=Path, help="Path to commit message file (e.g. .git/COMMIT_EDITMSG)")
    args = parser.parse_args(argv)

    raw_msg: Optional[str] = None
    if args.file:
        if not args.file.exists():
            print(f"Error: Commit message file does not exist: {args.file}", file=sys.stderr)
            return 1
        raw_msg = args.file.read_text(encoding="utf-8", errors="replace")
    elif args.flag_msg:
        raw_msg = args.flag_msg
    elif args.message:
        raw_msg = args.message

    if raw_msg is None:
        # Check stdin if piped
        if not sys.stdin.isatty():
            import select
            try:
                r, _, _ = select.select([sys.stdin], [], [], 5.0)
                raw_msg = sys.stdin.read() if r else None
            except (OSError, ValueError):
                raw_msg = None
            if raw_msg is None:
                print("Error: Stdin read timed out or failed", file=sys.stderr)
                return 1
        else:
            parser.print_help(sys.stderr)
            return 1

    valid, reason = validate_commit_message(raw_msg)
    if valid:
        return 0
    else:
        print(f"❌ COMMIT MESSAGE REJECTED:\n{reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
