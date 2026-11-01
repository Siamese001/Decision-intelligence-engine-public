"""
Priority facts provider for the PR queue (PRQ hardening, Waves 1-2).

Single source of truth for the evidence that tier qualification consumes:
  * real 3-dot numstat + per-commit file lists, fetched once per (base_sha, branch_sha)
    (never placeholder LOC; see committee RCA prq-rca A/B/C),
  * anchored glob matching where ``*`` never crosses ``/``,
  * specificity-aware path classification with an explicit ``unclassified`` default,
  * a hotfix exception scoped to conventional ``fix:`` commits that actually touch the tier.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

CATEGORY_ORDER: Tuple[str, ...] = ("noise", "artifact", "test", "doc", "govcfg", "tool", "prod")
UNCLASSIFIED = "unclassified"
CONVENTIONAL_FIX_RE = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)

_FACTS_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}


@lru_cache(maxsize=4096)
def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a glob into an anchored regex: ``**/`` = zero+ dirs, ``**`` = anything, ``*``/``?`` stay in one segment."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_matches(file_path: str, pattern: str) -> bool:
    """Patterns containing ``/`` are anchored at the repo root; bare patterns match the basename."""
    path = file_path.replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    target = path if "/" in pattern else PurePosixPath(path).name
    return bool(_glob_to_regex(pattern).match(target))


def match_path_globs(file_path: str, globs: Iterable[str]) -> bool:
    return any(glob_matches(file_path, g) for g in globs)


def _literal_prefix_len(pattern: str) -> int:
    m = re.search(r"[*?\[]", pattern)
    return len(pattern if m is None else pattern[: m.start()])


def classify_path(file_path: str, rules: Dict[str, List[str]]) -> str:
    """Most specific matching pattern wins (longest literal prefix); ties follow CATEGORY_ORDER.

    ``config/runtime/**`` (prod) therefore beats ``config/**`` (govcfg). Paths matching no
    rule are ``unclassified`` and never count as production LOC.
    """
    best: Optional[Tuple[int, int, str]] = None
    for order, cat in enumerate(CATEGORY_ORDER):
        for pat in rules.get(cat, []):
            if glob_matches(file_path, pat):
                cand = (_literal_prefix_len(pat), -order, cat)
                if best is None or cand > best:
                    best = cand
    return best[2] if best else UNCLASSIFIED


def is_conventional_fix(subject: str) -> bool:
    return bool(CONVENTIONAL_FIX_RE.match(subject or ""))


def has_scoped_fix(commits: List[Dict[str, Any]], tier_files: Iterable[str]) -> bool:
    """True only when a conventional fix commit itself modified at least one in-tier file."""
    in_tier = set(tier_files)
    if not in_tier:
        return False
    for c in commits:
        if is_conventional_fix(c.get("subject", "")) and in_tier.intersection(c.get("files") or []):
            return True
    return False


def _git(args: List[str], root: Path) -> Tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=120)
    return proc.returncode, proc.stdout


def parse_numstat_z(raw: str) -> Dict[str, Tuple[int, int]]:
    """Parse ``git diff --numstat -z --no-renames`` output (binary files count 0 LOC)."""
    stats: Dict[str, Tuple[int, int]] = {}
    for rec in raw.split("\0"):
        parts = rec.strip("\n").split("\t", 2)
        if len(parts) == 3 and parts[2]:
            a = 0 if parts[0] == "-" else int(parts[0])
            d = 0 if parts[1] == "-" else int(parts[1])
            stats[parts[2]] = (a, d)
    return stats


def _parse_commit_log(raw: str) -> List[Dict[str, Any]]:
    commits: List[Dict[str, Any]] = []
    for block in raw.split("\x1e"):
        block = block.strip("\n")
        if not block.strip():
            continue
        head, _, files_blob = block.partition("\x1d")
        subject, _, body = head.partition("\x1f")
        files = [f for f in files_blob.split("\n") if f.strip()]
        commits.append({"subject": subject.strip(), "body": body.strip(), "files": files})
    return commits


def fetch_branch_facts(branch: str, root: Path, base: str = "main") -> Dict[str, Any]:
    """Real diff facts for ``base...branch``; ``ok=False`` (fail closed) when git cannot resolve refs."""
    rc_b, base_sha = _git(["rev-parse", "--verify", "--quiet", base], root)
    rc_h, head_sha = _git(["rev-parse", "--verify", "--quiet", branch], root)
    if rc_b != 0 or rc_h != 0:
        return {"ok": False, "numstat": {}, "commits": [], "error": f"unresolvable ref ({base} or {branch})"}
    key = (base_sha.strip(), head_sha.strip(), str(root))
    if key in _FACTS_CACHE:
        return _FACTS_CACHE[key]
    rc_n, raw_num = _git(["diff", "--numstat", "-z", "--no-renames", f"{key[0]}...{key[1]}"], root)
    rc_l, raw_log = _git(
        ["log", "--no-renames", "--format=%x1e%s%x1f%b%x1d", "--name-only", f"{key[0]}..{key[1]}"], root
    )
    if rc_n != 0 or rc_l != 0:
        return {"ok": False, "numstat": {}, "commits": [], "error": "git diff/log failed"}
    facts = {"ok": True, "numstat": parse_numstat_z(raw_num), "commits": _parse_commit_log(raw_log), "error": ""}
    _FACTS_CACHE[key] = facts
    return facts


def fetch_worktree_dirty_numstat(worktree_path: str, max_bytes: int = 2_000_000) -> Dict[str, Tuple[int, int]]:
    """Numstat of uncommitted work (tracked diff vs HEAD + untracked line counts) for COMMIT_FIRST rows."""
    root = Path(worktree_path)
    stats: Dict[str, Tuple[int, int]] = {}
    rc, raw = _git(["diff", "HEAD", "--numstat", "-z", "--no-renames"], root)
    if rc == 0:
        stats.update(parse_numstat_z(raw))
    rc, raw = _git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    if rc == 0:
        for rel in (p for p in raw.split("\0") if p):
            fp = root / rel
            try:
                if fp.is_file() and fp.stat().st_size <= max_bytes:
                    stats[rel] = (fp.read_bytes().count(b"\n"), 0)
                else:
                    stats[rel] = (0, 0)
            except OSError:
                stats[rel] = (0, 0)
    return stats


def clear_facts_cache() -> None:
    _FACTS_CACHE.clear()
