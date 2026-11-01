"""
dual_pipeline/security_paths.py

Filesystem path containment and traversal security guardrails.
Blocks path traversal attacks, symlink breakout outside workspace, and access to sensitive files
(private keys, environment secrets, tokens, credentials).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Union

logger = logging.getLogger("dual_pipeline.security_paths")


class PathTraversalError(ValueError):
    """Raised when a path attempts directory traversal or escapes allowed roots."""


class SensitiveFileAccessBlockedError(PermissionError):
    """Raised when an operation attempts to read sensitive files (secrets, keys, tokens)."""


BLOCKED_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)(?:^|/)\.env(?:[._-].*)?$"),
    re.compile(r"(?i)id_rsa(?:[._-].*)?$"),
    re.compile(r"(?i)id_ed25519(?:[._-].*)?$"),
    re.compile(r"(?i)id_ecdsa(?:[._-].*)?$"),
    re.compile(r"(?i)\.pem$"),
    re.compile(r"(?i)\.key$"),
    re.compile(r"(?i)(?:^|/)\.ssh(?:/|$)"),
    re.compile(r"(?i)(?:^|/)\.aws(?:/|$)"),
    re.compile(r"(?i)(?:^|.*[._-])credentials?(?:[._-].*)?$"),
    re.compile(r"(?i)(?:^|.*[._-])secrets?(?:[._-].*)?$"),
    re.compile(r"(?i)(?:^|.*[._-])tokens?(?:[._-].*)?$"),
    re.compile(r"(?i)(?:/etc/passwd|/etc/shadow)$"),
]


def assert_safe_source_path(
    base_root: Path,
    path: Union[str, Path],
    allow_tmp: bool = True,
) -> Path:
    """
    Validates that a path is contained within base_root (or tempdir) and does not point to sensitive files.
    Resolves symlinks and fails closed on traversal or security violations.
    """
    raw_str = str(path).strip()
    if not raw_str:
        raise PathTraversalError("Empty file path provided.")

    target = Path(raw_str)
    # Check for direct pattern match in raw path string
    for pat in BLOCKED_FILE_PATTERNS:
        if pat.search(raw_str) or pat.search(target.name):
            raise SensitiveFileAccessBlockedError(
                f"ACCESS_DENIED: Access to sensitive file pattern '{target.name}' is strictly blocked."
            )

    try:
        resolved = target.resolve()
    except Exception as exc:
        raise PathTraversalError(f"Cannot resolve path '{raw_str}': {exc}") from exc

    resolved_str = str(resolved)
    for pat in BLOCKED_FILE_PATTERNS:
        if pat.search(resolved_str) or pat.search(resolved.name):
            raise SensitiveFileAccessBlockedError(
                f"ACCESS_DENIED: Resolved path '{resolved.name}' matches sensitive file pattern."
            )

    # Check root containment
    root_resolved = base_root.resolve()
    is_inside_root = False
    try:
        resolved.relative_to(root_resolved)
        is_inside_root = True
    except ValueError:
        is_inside_root = False

    if not is_inside_root:
        # Check if allowed in system temporary directory
        import tempfile
        tmp_dir = Path(tempfile.gettempdir()).resolve()
        is_inside_tmp = False
        if allow_tmp:
            try:
                resolved.relative_to(tmp_dir)
                is_inside_tmp = True
            except ValueError:
                is_inside_tmp = False

        if not is_inside_tmp:
            raise PathTraversalError(
                f"PATH_TRAVERSAL_DETECTED: Target path '{raw_str}' resolves outside allowed workspace "
                f"root '{root_resolved}'."
            )

    return resolved
