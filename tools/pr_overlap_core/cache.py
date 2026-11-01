"""
Durable and memory cache for branch evidence records.
Keyed on (branch, head_sha, merge_base).
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .models import (
    BranchEvidence,
    BranchFileSymbols,
    CommitPatchEvidence,
    FileHunkDiff,
    HunkRange,
    SymbolSpan,
)


class EvidenceCache:
    """Read/write cache for BranchEvidence records stored as JSON on disk or in memory."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir
        self._memory_cache: Dict[str, BranchEvidence] = {}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, branch: str, head_sha: str, merge_base: str) -> str:
        safe_branch = branch.replace("/", "_").replace("@", "_").replace("{", "_").replace("}", "_")
        return f"{safe_branch}_{head_sha[:10]}_{merge_base[:10]}"

    def get(self, branch: str, head_sha: str, merge_base: str) -> Optional[BranchEvidence]:
        key = self._cache_key(branch, head_sha, merge_base)
        if key in self._memory_cache:
            return self._memory_cache[key]

        if self.cache_dir:
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    evidence = self._deserialize_branch_evidence(data)
                    self._memory_cache[key] = evidence
                    return evidence
                except Exception:
                    pass
        return None

    def set(self, evidence: BranchEvidence) -> None:
        key = self._cache_key(evidence.branch, evidence.head_sha, evidence.merge_base)
        self._memory_cache[key] = evidence
        if self.cache_dir:
            cache_file = self.cache_dir / f"{key}.json"
            with contextlib.suppress(Exception):
                cache_file.write_text(json.dumps(evidence.to_dict(), indent=2), encoding="utf-8")

    def _deserialize_branch_evidence(self, data: Dict[str, Any]) -> BranchEvidence:
        commits = [
            CommitPatchEvidence(
                commit_sha=c["commit_sha"],
                patch_id=c.get("patch_id", ""),
                subject=c.get("subject", ""),
                author=c.get("author", ""),
                timestamp=c.get("timestamp", ""),
                files=c.get("files", []),
            )
            for c in data.get("commits", [])
        ]

        files = {}
        for path, fd in data.get("files", {}).items():
            hunks = [
                HunkRange(
                    old_start=h["old_start"],
                    old_lines=h["old_lines"],
                    new_start=h["new_start"],
                    new_lines=h["new_lines"],
                    hunk_header=h.get("hunk_header", ""),
                )
                for h in fd.get("hunks", [])
            ]
            files[path] = FileHunkDiff(
                path=path,
                status=fd.get("status", "M"),
                old_path=fd.get("old_path"),
                hunks=hunks,
                blob_sha=fd.get("blob_sha"),
                hunk_hashes=fd.get("hunk_hashes", []),
            )

        symbols = {}
        for path, sd in data.get("symbols", {}).items():
            all_syms = [
                SymbolSpan(
                    symbol_name=s["symbol_name"],
                    symbol_type=s["symbol_type"],
                    start_line=s["start_line"],
                    end_line=s["end_line"],
                    parent_symbol=s.get("parent_symbol"),
                )
                for s in sd.get("all_symbols", [])
            ]
            touched_syms = [
                SymbolSpan(
                    symbol_name=s["symbol_name"],
                    symbol_type=s["symbol_type"],
                    start_line=s["start_line"],
                    end_line=s["end_line"],
                    parent_symbol=s.get("parent_symbol"),
                )
                for s in sd.get("touched_symbols", [])
            ]
            symbols[path] = BranchFileSymbols(
                path=path,
                all_symbols=all_syms,
                touched_symbols=touched_syms,
            )

        return BranchEvidence(
            branch=data["branch"],
            merge_base=data["merge_base"],
            head_sha=data["head_sha"],
            commit_count=data.get("commit_count", len(commits)),
            commits=commits,
            files=files,
            symbols=symbols,
        )
