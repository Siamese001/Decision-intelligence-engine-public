"""
tools/pr_governance_core/github_adapter.py

Governed GitHub Pull Request integration adapter for PR queue governance.
Queries active Pull Request state, correlating branches with PR numbers, titles, and CI checks.

Supports:
- Primary method: `gh pr list` CLI if available
- Secondary fallback: GitHub REST API (https://api.github.com/repos/{owner}/{repo}/pulls) using GITHUB_TOKEN / GH_TOKEN
- Air-gap / offline fallback: Gracefully returns empty PR mapping or cached data without failing or blocking
- Local disk caching with TTL (default 300s / 5min) in .local/cache/github_pr_cache.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .common import ROOT, run_git
except ImportError:
    import sys

    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.pr_governance_core.common import ROOT, run_git

DEFAULT_CACHE_TTL_SECONDS = 300.0  # 5 minutes


@dataclass
class GitHubPRInfo:
    number: int
    title: str
    url: str
    head_ref: str
    base_ref: str = "main"
    state: str = "OPEN"
    draft: bool = False
    ci_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GitHubPRInfo":
        return cls(
            number=int(data.get("number", 0)),
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            head_ref=str(data.get("head_ref", "")),
            base_ref=str(data.get("base_ref", "main")),
            state=str(data.get("state", "OPEN")),
            draft=bool(data.get("draft", False)),
            ci_status=data.get("ci_status"),
        )


def get_cache_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or ROOT
    return root / ".local" / "cache" / "github_pr_cache.json"


def load_cached_prs(
    cache_path: Path,
    max_age_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
) -> Optional[List[GitHubPRInfo]]:
    """Loads cached PR list if valid and within TTL; returns None if expired, missing, or invalid."""
    if not cache_path.exists():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_time = float(raw.get("cached_at_epoch", 0.0))
        if (time.time() - cached_time) > max_age_seconds:
            return None
        prs_data = raw.get("prs", [])
        return [GitHubPRInfo.from_dict(p) for p in prs_data]
    except Exception:
        return None


def save_cached_prs(cache_path: Path, prs: List[GitHubPRInfo]) -> None:
    """Saves PR list to cache_path atomically."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at_iso": datetime.now(timezone.utc).isoformat(),
            "cached_at_epoch": time.time(),
            "count": len(prs),
            "prs": [pr.to_dict() for pr in prs],
        }
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(cache_path)
    except Exception:
        pass


def parse_repo_slug_from_origin(repo_root: Optional[Path] = None) -> str:
    """Parses owner/repo slug from git remote get-url origin, falling back to default."""
    root = repo_root or ROOT
    try:
        url = run_git(["remote", "get-url", "origin"], cwd=root).strip()
        match = re.search(r"github\.com[:/]([^/]+/[^/\.]+?)(?:\.git)?$", url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "Siamese001/decision-intelligence-engine"


def fetch_prs_via_gh_cli(
    repo_root: Optional[Path] = None,
    timeout: float = 10.0,
) -> Optional[List[GitHubPRInfo]]:
    """Fetches open PRs using local `gh` CLI if installed."""
    if not shutil.which("gh"):
        return None
    root = repo_root or ROOT
    cmd = [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--json",
        "number,title,headRefName,baseRefName,state,url,isDraft",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return None
        items = json.loads(proc.stdout)
        prs = []
        for it in items:
            prs.append(
                GitHubPRInfo(
                    number=int(it["number"]),
                    title=str(it.get("title", "")),
                    url=str(it.get("url", "")),
                    head_ref=str(it.get("headRefName", "")),
                    base_ref=str(it.get("baseRefName", "main")),
                    state=str(it.get("state", "OPEN")),
                    draft=bool(it.get("isDraft", False)),
                )
            )
        return prs
    except Exception:
        return None


def fetch_prs_via_rest_api(
    repo_slug: str,
    token: Optional[str] = None,
    timeout: float = 10.0,
) -> Optional[List[GitHubPRInfo]]:
    """Fetches open PRs via GitHub REST API with optional Bearer token."""
    from config.credentials import CredentialManager

    auth_token = token or CredentialManager.get_github_token()
    api_url = f"https://api.github.com/repos/{repo_slug}/pulls?state=open&per_page=100"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "decision-intelligence-engine/1.0",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, list):
                return None
            prs = []
            for it in data:
                head = it.get("head", {}) or {}
                base = it.get("base", {}) or {}
                prs.append(
                    GitHubPRInfo(
                        number=int(it.get("number", 0)),
                        title=str(it.get("title", "")),
                        url=str(it.get("html_url", "")),
                        head_ref=str(head.get("ref", "")),
                        base_ref=str(base.get("ref", "main")),
                        state=str(it.get("state", "open")).upper(),
                        draft=bool(it.get("draft", False)),
                    )
                )
            return prs
    except Exception:
        return None


def _build_pr_lookup_map(prs: List[GitHubPRInfo]) -> Dict[str, GitHubPRInfo]:
    """Indexes GitHubPRInfo by various ref representations."""
    lookup: Dict[str, GitHubPRInfo] = {}
    for pr in prs:
        if not pr.head_ref:
            continue
        lookup[pr.head_ref] = pr
        lookup[f"origin/{pr.head_ref}"] = pr
        lookup[f"refs/heads/{pr.head_ref}"] = pr
        lookup[f"refs/remotes/origin/{pr.head_ref}"] = pr
    return lookup


def get_github_prs_with_provenance(
    repo_root: Optional[Path] = None,
    force_refresh: bool = False,
    timeout: float = 10.0,
    max_age_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    allow_stale: bool = True,
) -> Tuple[Dict[str, GitHubPRInfo], str]:
    """
    Returns a mapping of branch ref -> GitHubPRInfo along with discovery provenance.
    Provenance values:
      - 'LIVE': freshly fetched from gh CLI or REST API with non-empty results
      - 'LIVE_VERIFIED_EMPTY': freshly fetched, verified 0 open PRs
      - 'CACHED_FRESH': loaded from valid disk cache within TTL
      - 'STALE_CACHE': loaded from expired disk cache (fallback)
      - 'UNAVAILABLE': live fetch failed and no cache available (or allow_stale is False)
    """
    cache_path = get_cache_path(repo_root)

    # 1. Check valid cache unless forced
    if not force_refresh:
        cached = load_cached_prs(cache_path, max_age_seconds=max_age_seconds)
        if cached is not None:
            return _build_pr_lookup_map(cached), "CACHED_FRESH"

    # 2. Try gh CLI
    prs = fetch_prs_via_gh_cli(repo_root=repo_root, timeout=timeout)

    # 3. Fallback to GitHub REST API
    if prs is None:
        slug = parse_repo_slug_from_origin(repo_root)
        prs = fetch_prs_via_rest_api(slug, timeout=timeout)

    # 4. If live fetch succeeded, persist to cache
    if prs is not None:
        save_cached_prs(cache_path, prs)
        prov = "LIVE" if prs else "LIVE_VERIFIED_EMPTY"
        return _build_pr_lookup_map(prs), prov

    # 5. Offline fallback: check for any stale cache if permitted
    if allow_stale:
        stale = load_cached_prs(cache_path, max_age_seconds=float("inf"))
        if stale is not None:
            return _build_pr_lookup_map(stale), "STALE_CACHE"

    return {}, "UNAVAILABLE"


def get_github_prs(
    repo_root: Optional[Path] = None,
    force_refresh: bool = False,
    timeout: float = 10.0,
    max_age_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    allow_stale: bool = True,
) -> Dict[str, GitHubPRInfo]:
    """
    Returns a mapping of branch ref -> GitHubPRInfo.
    Both local ref ('feat/foo') and remote ref ('origin/feat/foo') map to the corresponding PR.
    Employs disk caching (TTL 5m), `gh` CLI, REST API, and graceful air-gap fallbacks.
    """
    pr_map, _ = get_github_prs_with_provenance(
        repo_root=repo_root,
        force_refresh=force_refresh,
        timeout=timeout,
        max_age_seconds=max_age_seconds,
        allow_stale=allow_stale,
    )
    return pr_map


def get_pr_for_branch(branch: str, pr_map: Dict[str, GitHubPRInfo]) -> Optional[GitHubPRInfo]:
    """Looks up a GitHubPRInfo for a given local or remote branch name."""
    if not pr_map:
        return None
    if branch in pr_map:
        return pr_map[branch]
    if branch.startswith("origin/"):
        return pr_map.get(branch[len("origin/") :])
    return pr_map.get(f"origin/{branch}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed GitHub PR Integration Adapter.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    parser.add_argument("--refresh", action="store_true", help="Force refresh bypassing cache.")
    parser.add_argument("--strict", action="store_true", help="Fail if live discovery unavailable (disallow stale).")
    parser.add_argument("--receipt", help="Path to write JSON execution receipt.")
    args = parser.parse_args()

    pr_map, provenance = get_github_prs_with_provenance(
        force_refresh=args.refresh,
        allow_stale=not args.strict,
    )
    # Deduplicate unique PR objects for reporting
    unique_prs = {pr.number: pr for pr in pr_map.values()}
    prs_list = [pr.to_dict() for pr in sorted(unique_prs.values(), key=lambda p: p.number)]

    if args.json:
        print(json.dumps(prs_list, indent=2))
    else:
        print("=" * 80)
        print("  GOVERNED GITHUB PR INTEGRATION ADAPTER")
        print("=" * 80)
        print(f"Discovery Provenance: {provenance}")
        print(f"Total open PRs discovered: {len(prs_list)}")
        for pr in prs_list:
            draft_str = " [DRAFT]" if pr.get("draft") else ""
            print(f"  • #{pr['number']}: {pr['title']} ({pr['head_ref']} -> {pr['base_ref']}){draft_str}")
            print(f"    URL: {pr['url']}")
        print("=" * 80)

    if args.receipt:
        receipt_path = Path(args.receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if provenance in ("LIVE", "LIVE_VERIFIED_EMPTY", "CACHED_FRESH"):
            receipt_status = "PASSED"
        elif provenance == "STALE_CACHE":
            receipt_status = "STALE"
        else:
            receipt_status = "UNAVAILABLE"

        receipt = {
            "$schema": "urn:nee:github-adapter-receipt:1.0.0",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": receipt_status,
            "provenance": provenance,
            "open_prs_count": len(prs_list),
            "prs": prs_list,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(f"Receipt written to: {receipt_path}")
        if args.strict and receipt_status != "PASSED":
            return 1

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
