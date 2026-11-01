"""Pre-Command Execution Firewall and Prohibited Search Sentinel.

Enforces fail-closed blocking of dangerous, unindexed shell searches (grep, find, rg),
raw git worktree/branch deletion bypasses, and direct shell writes into root output/.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any, NamedTuple, Optional, Sequence, Tuple

from dual_pipeline.exceptions import DualPipelineValueError


class GraphDBRequiredError(DualPipelineValueError):
    """Raised when an agent attempts an unindexed shell search instead of GraphDB inspection."""

    def __init__(
        self,
        command: str = "",
        matched_pattern: str = "",
        target_path: str = "",
        message: str | None = None,
    ) -> None:
        self.command = command
        self.matched_pattern = matched_pattern or "GRAPHDB_TRAVERSAL_REQUIRED"
        self.target_path = target_path or "CANONICAL_GRAPH.db"
        if message:
            remediation = message
        elif matched_pattern or target_path:
            remediation = (
                f"Prohibited shell search command blocked: '{command}'.\n"
                f"Pattern '{self.matched_pattern}' targeting '{self.target_path}' violates Rule 5 (Scope Containment) "
                "and Query Resolution Governance.\n"
                "Execution must follow the 5-Tier Query Resolution Cascade.\n"
                "Remediation: Use governed GraphDB inspection tools instead:\n"
                "  1. nee ask --question \"<question>\"\n"
                "  2. nee_query / nee query: Governed multi-tier query engine with semantic routing.\n"
                "  3. graphdb_read_query: Direct indexed SQLite queries on active graph.sqlite (must include WHERE canonical_status = 'canonical' AND canonical_eligible = 1 when querying assertion).\n"
                "  4. EvidenceLocatorService: Deterministic byte/line offset lookup via evidence_item.locator_value."
            )
        else:
            remediation = command
        super().__init__(remediation)


_ORIGINAL_RUN = subprocess.run
_ORIGINAL_POPEN = subprocess.Popen


def _check_subprocess_args(cmd_args: Any) -> None:
    if isinstance(cmd_args, (list, tuple)):
        if cmd_args and isinstance(cmd_args[0], str):
            ProhibitedCommandGuard.inspect(" ".join(cmd_args))
    elif isinstance(cmd_args, str):
        ProhibitedCommandGuard.inspect(cmd_args)


def _firewalled_run(*args: Any, **kwargs: Any) -> Any:
    if args:
        _check_subprocess_args(args[0])
    elif "args" in kwargs:
        _check_subprocess_args(kwargs["args"])
    return _ORIGINAL_RUN(*args, **kwargs)


class _FirewalledPopen(_ORIGINAL_POPEN):  # type: ignore
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if args:
            _check_subprocess_args(args[0])
        elif "args" in kwargs:
            _check_subprocess_args(kwargs["args"])
        super().__init__(*args, **kwargs)


def install_subprocess_firewall() -> None:
    """Installs the firewall at the subprocess level."""
    subprocess.run = _firewalled_run
    subprocess.Popen = _FirewalledPopen  # type: ignore


def uninstall_subprocess_firewall() -> None:
    """Uninstalls the subprocess firewall."""
    subprocess.run = _ORIGINAL_RUN
    subprocess.Popen = _ORIGINAL_POPEN


class ViolationMatch(NamedTuple):
    """Details of a detected firewall pattern violation."""

    is_violation: bool
    matched_pattern: str
    target_path: str
    rationale: str


ALLOWLISTED_TOOLS: Sequence[str] = (
    "tools/validate_output_hierarchy.py",
    "tools/lint_production_purity.py",
    "tools/verify_main_readiness.py",
    "tools/pr_governance.py",
    "tools/run_tests_with_progress.py",
    "pytest",
    "nee.py",
)


class ProhibitedCommandGuard:
    """Pre-command execution firewall hook intercepting dangerous shell searches and worktree bypasses."""

    SEARCH_BINARIES: Sequence[str] = ("grep", "egrep", "fgrep", "rg", "ripgrep", "find", "ag", "ack")
    READER_BINARIES: Sequence[str] = ("cat", "tail", "head", "sed", "awk", "less")
    ENUMERATION_BINARIES: Sequence[str] = ("ls", "tree", "stat", "du", "wc", "file")
    MUTATION_BINARIES: Sequence[str] = ("cp", "mv", "rsync", "install", "tar", "unzip", "ditto")

    WORKTREE_BYPASS_GIT_PATTERNS: Sequence[Tuple[re.Pattern[str], str]] = (
        (
            re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+worktree\s+(?:remove|prune)\b", re.IGNORECASE),
            "git worktree remove/prune [bypass]",
        ),
        (
            re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+branch\s+(?:-[dD]|--delete)\b", re.IGNORECASE),
            "git branch -D/-d [bypass]",
        ),
    )

    PROTECTED_TARGET_PATTERNS: Sequence[re.Pattern[str]] = (
        re.compile(r"(?:^|[\s/\"\'])output(?:/|[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|[\s/\"\'])dual_pipeline(?:/|[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|[\s/\"\'])\.local(?:/|[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|[\s/\"\'])governance(?:/|[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|\s)\.(?:/|[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|\s)\.\.(?:/|[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|\s)\*(?:[\s\"\']|$)", re.IGNORECASE),
        re.compile(r"(?:^|[\s/\"\'])\.worktrees/live_.+?/output(?:/|[\s\"\']|$)", re.IGNORECASE),
    )

    LIVE_TRANSCRIPT_PATTERN = re.compile(r".*_live_transcript\.md", re.IGNORECASE)
    GREP_RECURSIVE_FLAG_PATTERN = re.compile(
        r"(?:^|\s)(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|--dereference-recursive)(?:$|\s)"
    )
    INLINE_PYTHON_SCAN_RE = re.compile(
        r"(?:^|[\s/\"\'])(?:python|python3)\b.*?-c\s+[\'\"]?.*?(?:listdir|scandir|walk|glob|Path\([\'\"]output)",
        re.IGNORECASE,
    )

    @classmethod
    def split_pipeline_segments(cls, command_line: str) -> list[str]:
        """Splits piped or chained shell command lines into discrete command segments."""
        raw_segments = re.split(r"(?:&&|\|\||;|\|)", command_line)
        return [seg.strip() for seg in raw_segments if seg.strip()]

    @classmethod
    def _match_patterns(cls, text: str, patterns: Sequence[re.Pattern[str]]) -> Optional[str]:
        for pat in patterns:
            m = pat.search(text)
            if m:
                return m.group(0).strip()
        return None

    @classmethod
    def _check_reader_or_enum(cls, binary_name: str, tokens: list[str], full_seg: str) -> Optional[ViolationMatch]:
        if binary_name in cls.READER_BINARIES:
            out_pats = cls.PROTECTED_TARGET_PATTERNS[:1] + (cls.PROTECTED_TARGET_PATTERNS[-1],)
            for t in tokens[1:]:
                if cls.LIVE_TRANSCRIPT_PATTERN.search(t) or cls._match_patterns(t, out_pats):
                    return ViolationMatch(
                        True,
                        f"{binary_name} [live_transcript]",
                        t,
                        "Raw reads of live transcripts or output directories are strictly prohibited. Use `nee copilot`.",
                    )
        if binary_name in cls.ENUMERATION_BINARIES:
            target = cls._match_patterns(full_seg, cls.PROTECTED_TARGET_PATTERNS[:4] + (cls.PROTECTED_TARGET_PATTERNS[-1],))
            if target:
                return ViolationMatch(
                    True,
                    f"{binary_name} [directory enumeration]",
                    target,
                    f"Direct filesystem enumeration using '{binary_name}' on protected corpus is forbidden. Route all queries through GraphDB.",
                )
        return None

    @classmethod
    def _check_worktree_or_output_mutation(cls, binary_name: str, tokens: list[str], full_seg: str) -> Optional[ViolationMatch]:
        if binary_name == "git":
            for pat, label in cls.WORKTREE_BYPASS_GIT_PATTERNS:
                if pat.search(full_seg):
                    return ViolationMatch(
                        True,
                        label,
                        ".worktrees",
                        (
                            "WORKTREE_GOVERNANCE_BYPASS_BLOCKED: Direct 'git worktree remove/prune' or 'git branch -D/-d' "
                            "bypasses branch merge verification and archive ref protections. "
                            "Use '.venv/bin/python tools/pr_governance.py dehydrate <branch>' or 'finish <branch>' instead."
                        ),
                    )
        if binary_name in cls.MUTATION_BINARIES:
            non_flag_args = [t for t in tokens[1:] if not t.startswith("-")]
            if non_flag_args:
                dest_arg = non_flag_args[-1]
                norm = dest_arg.replace("\\", "/")
                while norm.startswith("./"):
                    norm = norm[2:]
                in_wt = norm.startswith(".worktrees/") or "/.worktrees/" in norm
                is_root_out = not in_wt and (
                    norm == "output" or norm.startswith("output/") or "/output/" in norm or norm.endswith("/output")
                )
                if is_root_out:
                    return ViolationMatch(
                        True,
                        f"{binary_name} [main_output_mutation]",
                        dest_arg,
                        (
                            f"MAIN_WORKTREE_WRITE_BLOCKED: Direct shell '{binary_name}' into primary root '{dest_arg}' "
                            "on branch 'main' is prohibited per Master Invariant 8. Commit deliverables inside their "
                            "isolated '.worktrees/<slug>' branch and promote via 'tools/pr_governance.py merge'."
                        ),
                    )
        return None

    @classmethod
    def _check_search_binary(cls, binary_name: str, tokens: list[str], full_seg: str) -> Optional[ViolationMatch]:
        if binary_name == "find":
            target = tokens[1] if len(tokens) > 1 else "."
            if cls._match_patterns(target, cls.PROTECTED_TARGET_PATTERNS) or cls._match_patterns(full_seg, cls.PROTECTED_TARGET_PATTERNS):
                return ViolationMatch(True, f"find {target}", target, "Unindexed find search across protected directory or root.")
        elif binary_name in ("grep", "egrep", "fgrep"):
            if cls.GREP_RECURSIVE_FLAG_PATTERN.search(full_seg):
                target = cls._match_patterns(full_seg, cls.PROTECTED_TARGET_PATTERNS)
                if target:
                    return ViolationMatch(
                        True,
                        f"{binary_name} [recursive]",
                        target,
                        "Unindexed recursive grep causes severe I/O thrashing and bypasses entity resolution & assertion confidence.",
                    )
            target = cls._match_patterns(full_seg, cls.PROTECTED_TARGET_PATTERNS[:4])
            if target:
                return ViolationMatch(True, f"{binary_name} [direct directory]", target, "Direct directory grep on protected corpus is forbidden.")
        elif binary_name in ("rg", "ripgrep", "ag", "ack"):
            target = cls._match_patterns(full_seg, cls.PROTECTED_TARGET_PATTERNS)
            if target:
                return ViolationMatch(True, f"{binary_name} [protected search]", target, f"Unindexed {binary_name} search across protected directory or root.")
            if len([t for t in tokens[1:] if not t.startswith("-")]) <= 1:
                return ViolationMatch(True, f"{binary_name} [unscoped]", ".", f"Unscoped {binary_name} search defaults to workspace root.")
        return None

    @classmethod
    def _evaluate_segment(cls, seg: str) -> Optional[ViolationMatch]:
        try:
            tokens = shlex.split(seg)
        except ValueError:
            tokens = seg.split()
        if not tokens:
            return None
        binary_name = tokens[0].rsplit("/", 1)[-1]
        full_seg = " ".join(tokens)
        if any(allow in full_seg for allow in ALLOWLISTED_TOOLS):
            return None

        res = cls._check_reader_or_enum(binary_name, tokens, full_seg)
        if res or binary_name in cls.READER_BINARIES:
            return res

        if binary_name in ("python", "python3") and "-c" in tokens and cls.INLINE_PYTHON_SCAN_RE.search(full_seg):
            target = cls._match_patterns(full_seg, cls.PROTECTED_TARGET_PATTERNS[:4])
            if target:
                return ViolationMatch(
                    True,
                    "python -c [inline traversal]",
                    target,
                    "Inline Python directory scanning on protected corpus is forbidden. Route queries through GraphDB.",
                )

        res = cls._check_worktree_or_output_mutation(binary_name, tokens, full_seg)
        if res:
            return res

        if binary_name in cls.SEARCH_BINARIES:
            return cls._check_search_binary(binary_name, tokens, full_seg)
        return None

    @classmethod
    def evaluate_command(cls, command_line: str) -> ViolationMatch:
        """Evaluates whether a command contains prohibited unindexed shell search or worktree bypass patterns."""
        normalized = command_line.strip()
        if not normalized:
            return ViolationMatch(False, "", "", "")

        if cls.INLINE_PYTHON_SCAN_RE.search(normalized):
            target = cls._match_patterns(normalized, cls.PROTECTED_TARGET_PATTERNS[:4])
            if target:
                return ViolationMatch(
                    True,
                    "python -c [inline traversal]",
                    target,
                    "Inline Python directory scanning on protected corpus is forbidden. Route queries through GraphDB.",
                )

        for seg in cls.split_pipeline_segments(normalized):
            match = cls._evaluate_segment(seg)
            if match is not None:
                return match
        return ViolationMatch(False, "", "", "")

    @classmethod
    def inspect(cls, command_line: str) -> None:
        """Inspects a command line and raises GraphDBRequiredError fail-closed if blocked."""
        violation = cls.evaluate_command(command_line)
        if violation.is_violation:
            raise GraphDBRequiredError(
                command=command_line,
                matched_pattern=violation.matched_pattern,
                target_path=violation.target_path,
            )

    @classmethod
    def is_prohibited(cls, command_line: str) -> bool:
        """Returns True if the command is prohibited by the firewall."""
        return cls.evaluate_command(command_line).is_violation


def check_command_firewall(command_line: str) -> None:
    """Convenience function enforcing pre-command firewall checks."""
    ProhibitedCommandGuard.inspect(command_line)
