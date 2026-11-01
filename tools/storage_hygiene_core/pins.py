"""Shared Pin Resolver for NEE Storage and Graph Generations."""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .policy import StorageRetentionPolicy, load_storage_retention_policy
from .receipt import create_hygiene_report

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


def get_main_repo_root(p: Path) -> Path:
    """Return main repository root even if executed within a worktree."""
    resolved = p.resolve()
    if ".worktrees" in resolved.parts:
        idx = resolved.parts.index(".worktrees")
        return Path(*resolved.parts[:idx])
    return resolved


MAIN_REPO_ROOT = get_main_repo_root(ROOT)
GEN_ID_RE = re.compile(r"generation-[a-z0-9-]+")


class CorruptPinSourceError(RuntimeError):
    """Raised when an active_pair source is syntactically invalid or corrupt."""


@dataclass(frozen=True)
class InvalidPinSource:
    path: str
    reason: str


@dataclass
class PinResolutionResult:
    active_generations: set[str] = field(default_factory=set)
    rollback_generations: set[str] = field(default_factory=set)
    preserved_generations: set[str] = field(default_factory=set)
    receipt_cited_generations: set[str] = field(default_factory=set)
    invalid_sources: list[InvalidPinSource] = field(default_factory=list)
    fail_closed: bool = False
    fail_reason: str = ""

    @property
    def all_pinned(self) -> set[str]:
        return (
            self.active_generations
            | self.rollback_generations
            | self.preserved_generations
            | self.receipt_cited_generations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_generations": sorted(self.active_generations),
            "rollback_generations": sorted(self.rollback_generations),
            "preserved_generations": sorted(self.preserved_generations),
            "receipt_cited_generations": sorted(self.receipt_cited_generations),
            "invalid_sources": [asdict(s) for s in self.invalid_sources],
            "total_pinned_count": len(self.all_pinned),
            "fail_closed": self.fail_closed,
            "fail_reason": self.fail_reason,
        }


def _find_candidate_active_pairs(repo_root: Path) -> list[Path]:
    """Find all potential active_pair.json files outside worktrees and node_modules."""
    candidates: list[Path] = []
    # Known canonical locations
    primary_locations = [
        repo_root / ".local" / "dual_pipeline" / "active_pair.json",
        repo_root / ".local" / "dual_pipeline" / "runtime" / "active_pair.json",
        repo_root / "dual_pipeline" / "retrieval" / "active_pair.json",
        repo_root / "runtime" / "active_pair.json",
        repo_root / "active_pair.json",
    ]
    for loc in primary_locations:
        if loc.exists() and loc not in candidates:
            candidates.append(loc)

    # Preserved untracked backups
    preserved_dir = repo_root / ".local" / "preserved_untracked"
    if preserved_dir.exists():
        for sub in preserved_dir.iterdir():
            if sub.is_dir():
                pair = sub / "active_pair.json"
                if pair.exists() and pair not in candidates:
                    candidates.append(pair)

    return candidates


def _find_prev_id_in_releases(releases_dir: Path, current_gen: str) -> str | None:
    if not releases_dir.exists():
        return None
    for rf in releases_dir.glob("*.json"):
        try:
            rdata = json.loads(rf.read_text(encoding="utf-8"))
            if rdata.get("generation_id") == current_gen:
                prev_id = rdata.get("previous_generation_id")
                if prev_id:
                    return prev_id
        except Exception:
            continue
    return None


def _get_prev_generation_id(gen_dir: Path, releases_dir: Path, current_gen: str) -> str | None:
    manifest_path = gen_dir / "pair_manifest.json"
    if manifest_path.exists():
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            prev_id = mdata.get("previous_generation_id")
            if prev_id:
                return prev_id
        except Exception:
            pass
    return _find_prev_id_in_releases(releases_dir, current_gen)


def _resolve_rollback_chain(
    runtime_root: Path,
    active_gen_id: str,
    max_depth: int,
) -> set[str]:
    """Traverse previous_generation_id links up to max_depth."""
    rollback_set: set[str] = set()
    current_gen = active_gen_id
    depth = 0
    releases_dir = runtime_root / "releases"

    while current_gen and depth < max_depth:
        gen_dir = runtime_root / "generations" / current_gen
        prev_id = _get_prev_generation_id(gen_dir, releases_dir, current_gen)
        if prev_id and prev_id != current_gen:
            rollback_set.add(prev_id)
            current_gen = prev_id
            depth += 1
        else:
            break

    return rollback_set


def _resolve_graph_file(p: Path, rt_root: Path, graph_path_str: str | None) -> Path | None:
    if not graph_path_str:
        return None
    p_cand = Path(graph_path_str)
    if p_cand.is_absolute():
        return p_cand
    cand1 = p.parent / p_cand
    if cand1.exists():
        return cand1
    cand2 = rt_root / p_cand
    if cand2.exists():
        return cand2
    return cand1


def _is_safe_graph_target(graph_resolved: Path, r_root: Path, main_root: Path) -> bool:
    resolved_canon = graph_resolved.resolve()
    for boundary in (r_root, main_root):
        try:
            resolved_canon.relative_to(boundary)
            return True
        except ValueError:
            continue
    return False


def _process_pair_file(
    p: Path,
    r_root: Path,
    main_root: Path,
    rt_root: Path,
    result: PinResolutionResult,
) -> bool:
    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        result.fail_closed = True
        result.fail_reason = f"Corrupt active_pair at {p}: {exc}"
        result.invalid_sources.append(
            InvalidPinSource(path=str(p), reason=f"corrupt_json: {exc}")
        )
        return False

    gen_id = data.get("generation_id")
    graph_path_str = data.get("graph_path") or data.get("graph")
    if not gen_id or not isinstance(gen_id, str):
        result.invalid_sources.append(
            InvalidPinSource(path=str(p), reason="missing_or_invalid_generation_id")
        )
        return True

    graph_resolved = _resolve_graph_file(p, rt_root, graph_path_str)
    if not graph_resolved or not graph_resolved.exists():
        result.invalid_sources.append(
            InvalidPinSource(
                path=str(p),
                reason=f"graph_target_missing ({gen_id}): {graph_path_str}",
            )
        )
        return True

    if not _is_safe_graph_target(graph_resolved, r_root, main_root):
        result.invalid_sources.append(
            InvalidPinSource(
                path=str(p),
                reason=f"graph_target_outside_repo ({gen_id}): {graph_path_str}",
            )
        )
        return True

    if "preserved_untracked" in str(p):
        result.preserved_generations.add(gen_id)
    else:
        result.active_generations.add(gen_id)
        prev_id = data.get("previous_generation_id")
        if prev_id and isinstance(prev_id, str):
            result.rollback_generations.add(prev_id)
    return True


def _collect_receipt_cited_generations(runtime_root: Path) -> set[str]:
    """Scan release and evaluation receipts for referenced generation IDs."""
    cited: set[str] = set()
    for subdir_name in ("releases", "retrieval_evaluations"):
        sub = runtime_root / subdir_name
        if not sub.exists():
            continue
        for item in sub.glob("*.json"):
            try:
                text = item.read_text(encoding="utf-8", errors="ignore")
                matches = GEN_ID_RE.findall(text)
                cited.update(matches)
            except Exception:
                continue
    return cited


def resolve_pins(
    repo_root: Path | None = None,
    runtime_root: Path | None = None,
    policy: StorageRetentionPolicy | None = None,
) -> PinResolutionResult:
    """Resolve union of all pinned generation IDs according to policy and evidence."""
    r_root = (repo_root or ROOT).resolve()
    main_root = get_main_repo_root(r_root)
    rt_root = (runtime_root or (main_root / ".local" / "dual_pipeline")).resolve()
    pol = policy or load_storage_retention_policy()

    result = PinResolutionResult()
    pair_files = _find_candidate_active_pairs(r_root)
    if main_root != r_root:
        for p in _find_candidate_active_pairs(main_root):
            if p not in pair_files:
                pair_files.append(p)

    for p in pair_files:
        ok = _process_pair_file(p, r_root, main_root, rt_root, result)
        if not ok:
            return result

    depth = pol.generations.rollback_depth
    for active_id in list(result.active_generations):
        chain = _resolve_rollback_chain(rt_root, active_id, depth)
        result.rollback_generations.update(chain)

    if pol.generations.preserve_receipt_cited:
        cited = _collect_receipt_cited_generations(rt_root)
        result.receipt_cited_generations.update(cited)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve and verify storage hygiene pins.")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / ".local" / "dual_pipeline",
        help="Dual pipeline runtime root",
    )
    parser.add_argument("--report", type=Path, default=None, help="Save sealed receipt path")
    args = parser.parse_args()

    pol = load_storage_retention_policy()
    res = resolve_pins(args.repo_root, args.runtime_root, pol)

    pins_dict = res.to_dict()
    summary = {
        "total_candidates": 0,
        "total_pinned": res.to_dict()["total_pinned_count"],
        "reclaimable_bytes": 0,
    }

    report = create_hygiene_report(
        mode="plan",
        scheduled=False,
        policy_digest=pol.digest,
        pins=pins_dict,
        summary=summary,
    )

    out_json = json.dumps(report, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(out_json, encoding="utf-8")
        logger.info("Saved pins receipt to %s", args.report)
    print(out_json)


if __name__ == "__main__":
    main()
