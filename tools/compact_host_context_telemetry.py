#!/usr/bin/env python3
"""
tools/compact_host_context_telemetry.py

Tool output compaction utility for Antigravity host context and telemetry.
Slashes the multi-turn session footprint by compacting verbose test runner,
CLI tool outputs, and historical messages while preserving critical diagnostic signal.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi_codes(text: str) -> str:
    """Removes ANSI escape sequences and terminal control codes."""
    if not text:
        return ""
    clean = ANSI_ESCAPE_RE.sub("", text)
    # Remove carriage returns often used in progress bars
    return clean.replace("\r\n", "\n").replace("\r", "\n")


def estimate_token_reduction(original_text: str, compacted_text: str) -> Dict[str, Any]:
    """Calculates estimated token counts and reduction percentage (~4 chars/token)."""
    orig_chars = len(original_text)
    comp_chars = len(compacted_text)
    orig_tokens = max(1, orig_chars // 4)
    comp_tokens = max(1, comp_chars // 4)
    saved_tokens = max(0, orig_tokens - comp_tokens)
    reduction_pct = (saved_tokens / orig_tokens) * 100.0 if orig_tokens > 0 else 0.0

    return {
        "original_chars": orig_chars,
        "compacted_chars": comp_chars,
        "original_tokens": orig_tokens,
        "compacted_tokens": comp_tokens,
        "saved_tokens": saved_tokens,
        "reduction_pct": round(reduction_pct, 2),
    }


def _is_summary_line(line: str) -> bool:
    """Checks if a line represents a pytest terminal summary banner."""
    if not line.startswith("==="):
        return False
    return any(k in line for k in ("passed", "failed", "error", "warning"))


def _extract_pytest_sections(lines: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """Extracts header, failures/errors, and summary lines from pytest output."""
    header: List[str] = []
    failures: List[str] = []
    summary: List[str] = []
    in_failures = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("===") and "test session starts" in stripped:
            header.append(stripped)
        elif stripped.startswith("===") and ("FAILURES" in stripped or "ERRORS" in stripped):
            in_failures = True
            failures.append(stripped)
        elif in_failures:
            if _is_summary_line(stripped):
                in_failures = False
                summary.append(stripped)
            else:
                failures.append(line)
        elif _is_summary_line(stripped):
            summary.append(stripped)

    return header, failures, summary


def compact_pytest_output(
    raw_output: str,
    max_lines: int = 50,
    max_chars: int = 3500,
) -> Dict[str, Any]:
    """Compacts pytest output into essential header, failure traces, and summary."""
    clean = strip_ansi_codes(raw_output)
    lines = clean.splitlines()
    header, failures, summary = _extract_pytest_sections(lines)

    compacted_lines: List[str] = []
    if header:
        compacted_lines.extend(header[:2])

    if failures:
        # Keep failure section within reasonable line budget
        if len(failures) > max_lines:
            compacted_lines.extend(failures[: max_lines - 5])
            compacted_lines.append(f"... [{len(failures) - max_lines + 5} failure lines truncated] ...")
        else:
            compacted_lines.extend(failures)
    else:
        # If no failures, mention passing runs concisely
        passing_count = sum(1 for line in lines if "PASSED" in line)
        if passing_count > 0:
            compacted_lines.append(f"[Compact Test Telemetry]: {passing_count} individual tests PASSED.")

    if summary:
        compacted_lines.extend(summary)
    elif lines and not failures:
        compacted_lines.append(lines[-1].strip())

    compacted_text = "\n".join(compacted_lines)
    if len(compacted_text) > max_chars:
        compacted_text = compacted_text[: max_chars - 50] + "\n... [Output character budget reached] ..."

    metrics = estimate_token_reduction(raw_output, compacted_text)
    return {
        "compacted_text": compacted_text,
        "metrics": metrics,
        "has_failures": bool(failures),
    }


def compact_cli_output(
    raw_output: str,
    max_chars: int = 2000,
    dedup_threshold: int = 3,
) -> Dict[str, Any]:
    """Compacts generic CLI/subshell outputs, stripping repetitive logs and deduping."""
    clean = strip_ansi_codes(raw_output)
    lines = clean.splitlines()

    deduped_lines: List[str] = []
    prev_line = ""
    repeat_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == prev_line:
            repeat_count += 1
            if repeat_count < dedup_threshold:
                deduped_lines.append(stripped)
            elif repeat_count == dedup_threshold:
                deduped_lines.append("... [repeated lines suppressed] ...")
        else:
            prev_line = stripped
            repeat_count = 1
            deduped_lines.append(stripped)

    compacted_text = "\n".join(deduped_lines)
    if len(compacted_text) > max_chars:
        half = max_chars // 2
        compacted_text = (
            compacted_text[:half]
            + f"\n\n... [{len(compacted_text) - max_chars} characters pruned] ...\n\n"
            + compacted_text[-half:]
        )

    metrics = estimate_token_reduction(raw_output, compacted_text)
    return {
        "compacted_text": compacted_text,
        "metrics": metrics,
    }


def compact_conversation_history(
    messages: List[Dict[str, Any]],
    max_tool_chars: int = 1200,
    preserve_recent_turns: int = 2,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Prunes verbose tool responses in multi-turn history while preserving recent fidelity."""
    compacted_messages: List[Dict[str, Any]] = []
    total_orig_chars = 0
    total_comp_chars = 0
    cutoff_index = max(0, len(messages) - preserve_recent_turns)

    for i, msg in enumerate(messages):
        msg_copy = dict(msg)
        content = str(msg.get("content", ""))
        total_orig_chars += len(content)

        if i < cutoff_index and msg.get("role") == "tool" and len(content) > max_tool_chars:
            # Compact older tool outputs
            pruned_content = (
                content[: max_tool_chars // 2]
                + f"\n... [Historical tool output compacted: {len(content) - max_tool_chars} chars pruned] ...\n"
                + content[-(max_tool_chars // 2) :]
            )
            msg_copy["content"] = pruned_content
            total_comp_chars += len(pruned_content)
        else:
            total_comp_chars += len(content)

        compacted_messages.append(msg_copy)

    metrics = estimate_token_reduction(" " * total_orig_chars, " " * total_comp_chars)
    return compacted_messages, metrics


def main() -> None:
    """CLI entrypoint for compacting input streams."""
    parser = argparse.ArgumentParser(description="Compact verbose CLI/pytest logs for host telemetry.")
    parser.add_argument("--pytest", action="store_true", help="Format specifically as pytest compact output.")
    parser.add_argument("--max-chars", type=int, default=3000, help="Maximum character budget.")
    args = parser.parse_args()

    if sys.stdin.isatty():
        parser.print_help(sys.stderr)
        sys.exit(1)

    import select
    try:
        r, _, _ = select.select([sys.stdin], [], [], 10.0)
        if not r:
            print("Error: Stdin read timed out after 10.0s", file=sys.stderr)
            sys.exit(1)
        raw_input = sys.stdin.read()
    except (OSError, ValueError):
        raw_input = ""
    if args.pytest:
        result = compact_pytest_output(raw_input, max_chars=args.max_chars)
    else:
        result = compact_cli_output(raw_input, max_chars=args.max_chars)

    print(result["compacted_text"])
    print(
        f"\n[Compaction Telemetry: {result['metrics']['reduction_pct']}% reduction | "
        f"Saved ~{result['metrics']['saved_tokens']} tokens]",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
