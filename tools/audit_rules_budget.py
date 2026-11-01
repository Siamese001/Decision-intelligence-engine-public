#!/usr/bin/env python3
"""Workspace Rules Context Budget, 90% Capacity Cleanup & >90% Deduplication Auditor."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    fuzz = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_RULES_DIR = ROOT / ".agents" / "rules"
DEFAULT_AGENTS_MD = ROOT / "AGENTS.md"
DEFAULT_GLOBAL_AGENTS_MD = Path.home() / ".gemini" / "config" / "plugins" / "core-governance" / "rules" / "AGENTS.md"
DEFAULT_MAX_BUDGET_TOKENS: int = 20000
DEFAULT_MAX_FILE_BYTES: int = 24000
DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT: float = 90.0
DEFAULT_DUPLICATION_THRESHOLD_PCT: float = 90.0


@dataclass
class RuleAuditResult:
    rule_name: str
    rule_path: str
    rule_scope: str
    trigger_mode: str
    token_count: int
    file_bytes: int
    is_bytes_compliant: bool
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DuplicateRulePair:
    file_a: str
    file_b: str
    similarity_pct: float
    matched_text_preview: str
    block_a: str = ""
    block_b: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RulesBudgetReport:
    total_rule_files: int
    always_on_files_count: int
    model_decision_files_count: int
    total_tokens: int
    total_always_on_tokens: int
    budget_token_limit: int
    budget_utilization_pct: float
    cleanup_threshold_pct: float
    is_at_or_above_capacity_threshold: bool
    is_budget_compliant: bool
    is_bytes_compliant: bool
    duplicate_pairs_above_90pct: List[DuplicateRulePair] = field(default_factory=list)
    is_dedup_compliant: bool = True
    is_overall_compliant: bool = True
    rules: List[RuleAuditResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def count_tokens(text: str) -> int:
    if not text.strip():
        return 0
    if tiktoken is not None:
        try:
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def parse_rule_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    frontmatter: Dict[str, str] = {}
    for raw_line in parts[1].splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z0-9_-]+)\s*:\s*(.*)$", line)
        if m:
            frontmatter[m.group(1)] = m.group(2).strip("\"'")
    return frontmatter, parts[2]


_LINK_POINTER_RE = re.compile(r"^[-*+]\s+[^:]+:\s+\[[^\]]+\]\([^)]+\)\.?$")


def extract_rule_blocks(content: str, min_chars: int = 70) -> List[str]:
    return [
        s for line in content.splitlines()
        if (s := line.strip()) and len(s) >= min_chars
        and not s.startswith(("|", "<!--", "#", "---"))
        and not _LINK_POINTER_RE.match(s)
    ]


def scan_rule_pairwise_duplicates(
    rules_content: Dict[str, str],
    threshold_pct: float = DEFAULT_DUPLICATION_THRESHOLD_PCT,
) -> List[DuplicateRulePair]:
    if fuzz is None:
        return []
    items: List[Tuple[str, str]] = [
        (path, blk) for path, text in rules_content.items()
        for blk in extract_rule_blocks(text)
    ]
    duplicates: List[DuplicateRulePair] = []
    seen: set[Tuple[str, str, str]] = set()

    for i, (f_a, b_a) in enumerate(items):
        for f_b, b_b in items[i + 1 :]:
            if f_a == f_b:
                continue
            key = (min(f_a, f_b), max(f_a, f_b), b_a[:50])
            if key in seen:
                continue
            ratio = fuzz.ratio(b_a.lower(), b_b.lower())
            if ratio >= threshold_pct:
                seen.add(key)
                duplicates.append(DuplicateRulePair(f_a, f_b, round(ratio, 1), b_a[:100], b_a, b_b))
    return duplicates


def _audit_file(
    file_path: Path, scope: str, max_bytes: int, display_name: Optional[str] = None
) -> Optional[Tuple[RuleAuditResult, str]]:
    if not file_path.is_file():
        return None
    txt = file_path.read_text(encoding="utf-8")
    fm, _ = parse_rule_frontmatter(txt)
    name = display_name or file_path.name
    trig = "always_on" if scope in ("global", "index") else fm.get("trigger", "always_on" if not txt.startswith("---") else "glob")
    f_bytes = len(txt.encode("utf-8"))
    toks = count_tokens(txt)
    is_ok = f_bytes <= max_bytes
    v = [f"Size {f_bytes} exceeds {max_bytes} bytes"] if not is_ok else []
    return RuleAuditResult(name, str(file_path), scope, trig, toks, f_bytes, is_ok, v), txt


def audit_rules_budget(
    rules_dir: Path = DEFAULT_RULES_DIR,
    agents_md: Path = DEFAULT_AGENTS_MD,
    global_agents_md: Optional[Path] = DEFAULT_GLOBAL_AGENTS_MD,
    max_budget_tokens: int = DEFAULT_MAX_BUDGET_TOKENS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    capacity_threshold_pct: float = DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
    duplication_threshold_pct: float = DEFAULT_DUPLICATION_THRESHOLD_PCT,
) -> RulesBudgetReport:
    results: List[RuleAuditResult] = []
    rules_content: Dict[str, str] = {}

    if global_agents_md:
        g = _audit_file(global_agents_md, "global", max_file_bytes, "global:AGENTS.md")
        if g:
            results.append(g[0])
            rules_content[g[0].rule_name] = g[1]

    a = _audit_file(agents_md, "index", max_file_bytes, "AGENTS.md")
    if a:
        results.append(a[0])
        rules_content[a[0].rule_name] = a[1]

    if rules_dir.is_dir():
        for r_file in sorted(rules_dir.glob("*.md")):
            w = _audit_file(r_file, "workspace", max_file_bytes)
            if w:
                results.append(w[0])
                rules_content[w[0].rule_name] = w[1]

    tot_tokens = sum(r.token_count for r in results)
    always_toks = sum(r.token_count for r in results if r.trigger_mode == "always_on")
    always_cnt = sum(1 for r in results if r.trigger_mode == "always_on")
    on_dem_cnt = sum(1 for r in results if r.trigger_mode != "always_on")
    util_pct = round((always_toks / max(1, max_budget_tokens)) * 100.0, 2)
    at_cap = util_pct >= capacity_threshold_pct
    budget_ok = always_toks <= max_budget_tokens
    bytes_ok = all(r.is_bytes_compliant for r in results)

    duplicates = scan_rule_pairwise_duplicates(rules_content, threshold_pct=duplication_threshold_pct)
    dedup_ok = len(duplicates) == 0

    errors: List[str] = []
    if not budget_ok:
        errors.append(f"Always-on rules ({always_toks} tokens) exceed budget ({max_budget_tokens}) [{util_pct}%]")
    if not bytes_ok:
        errors.append("One or more rule files exceed the 24,000-byte per-file ceiling")
    if not dedup_ok:
        errors.append(f"Found {len(duplicates)} duplicate rule pair(s) with >= {duplication_threshold_pct}% similarity")

    return RulesBudgetReport(
        total_rule_files=len(results),
        always_on_files_count=always_cnt,
        model_decision_files_count=on_dem_cnt,
        total_tokens=tot_tokens,
        total_always_on_tokens=always_toks,
        budget_token_limit=max_budget_tokens,
        budget_utilization_pct=util_pct,
        cleanup_threshold_pct=capacity_threshold_pct,
        is_at_or_above_capacity_threshold=at_cap,
        is_budget_compliant=budget_ok,
        is_bytes_compliant=bytes_ok,
        duplicate_pairs_above_90pct=duplicates,
        is_dedup_compliant=dedup_ok,
        is_overall_compliant=budget_ok and bytes_ok and dedup_ok and not at_cap,
        rules=results,
        errors=errors,
    )


DOMAIN_TRIGGER_CALIBRATIONS: Dict[str, str] = {
    "agent-routing.md": "model_decision",
    "e2e-and-edge-case-coverage.md": "model_decision",
    "graphdb-context-prompt-governance.md": "model_decision",
    "unified-agent-architecture.md": "model_decision",
    "scratchpad-reasoning-transparency.md": "model_decision",
    "risk-tiered-change-lanes.md": "model_decision",
    "conversational-communication-style.md": "model_decision",
}


def _calibrate_rule_triggers(rules_dir: Path) -> List[Dict[str, Any]]:
    actions = []
    for r_name, target_trig in DOMAIN_TRIGGER_CALIBRATIONS.items():
        r_path = rules_dir / r_name
        if not r_path.is_file():
            continue
        txt = r_path.read_text(encoding="utf-8")
        fm, body = parse_rule_frontmatter(txt)
        curr_trig = fm.get("trigger", "always_on" if not txt.startswith("---") else "glob")
        if curr_trig == "always_on":
            fm["trigger"] = target_trig
            lines = ["---"] + [f"{k}: {val}" for k, val in fm.items()] + ["---"]
            r_path.write_text("\n".join(lines) + "\n" + body.lstrip("\n"), encoding="utf-8")
            actions.append({"action": "calibrated_trigger", "rule": r_name, "new_trigger": target_trig})
    return actions


def deduplicate_and_compact_rules(
    rules_dir: Path = DEFAULT_RULES_DIR,
    agents_md: Path = DEFAULT_AGENTS_MD,
    global_agents_md: Optional[Path] = DEFAULT_GLOBAL_AGENTS_MD,
    max_budget_tokens: int = DEFAULT_MAX_BUDGET_TOKENS,
    capacity_threshold_pct: float = DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
    duplication_threshold_pct: float = DEFAULT_DUPLICATION_THRESHOLD_PCT,
) -> Dict[str, Any]:
    initial = audit_rules_budget(
        rules_dir, agents_md, global_agents_md, max_budget_tokens,
        capacity_threshold_pct=capacity_threshold_pct,
        duplication_threshold_pct=duplication_threshold_pct,
    )
    actions: List[Dict[str, Any]] = []
    for pair in initial.duplicate_pairs_above_90pct:
        pb = Path(pair.file_b) if Path(pair.file_b).is_absolute() else (rules_dir / pair.file_b)
        pa = Path(pair.file_a) if Path(pair.file_a).is_absolute() else (rules_dir / pair.file_a)
        if pb.is_file() and pa.is_file():
            tb = pb.read_text(encoding="utf-8")
            if pair.block_b and pair.block_b in tb:
                ptr = f"See [{pa.name}](file://{pa.resolve()})."
                pb.write_text(tb.replace(pair.block_b, ptr), encoding="utf-8")
                actions.append({"action": "deduplicated_rule_block", "file": str(pb), "source": str(pa)})

    if initial.is_at_or_above_capacity_threshold:
        actions.extend(_calibrate_rule_triggers(rules_dir))

    if agents_md and agents_md.is_file():
        try:
            from tools.generate_agents_index import build_agents_index_content
            repo_root = agents_md.parent if agents_md else ROOT
            agents_md.write_text(build_agents_index_content(repo_root), encoding="utf-8")
            actions.append({"action": "synchronized_agents_md_index", "path": str(agents_md)})
        except Exception as exc:
            actions.append({"action": "agents_md_sync_failed", "error": str(exc)})

    final = audit_rules_budget(
        rules_dir, agents_md, global_agents_md, max_budget_tokens,
        capacity_threshold_pct=capacity_threshold_pct,
        duplication_threshold_pct=duplication_threshold_pct,
    )
    return {
        "triggered": initial.is_at_or_above_capacity_threshold or len(initial.duplicate_pairs_above_90pct) > 0,
        "before_always_on_tokens": initial.total_always_on_tokens,
        "after_always_on_tokens": final.total_always_on_tokens,
        "before_utilization_pct": initial.budget_utilization_pct,
        "after_utilization_pct": final.budget_utilization_pct,
        "actions": actions,
        "report": final,
    }


def build_audit_receipt(report: RulesBudgetReport, command: str = "tools/audit_rules_budget.py --check") -> Dict[str, Any]:
    metrics = {
        k: getattr(report, k) for k in [
            "total_rule_files", "always_on_files_count", "model_decision_files_count",
            "total_tokens", "total_always_on_tokens", "budget_token_limit",
            "budget_utilization_pct", "cleanup_threshold_pct", "is_at_or_above_capacity_threshold",
            "is_budget_compliant", "is_bytes_compliant", "is_dedup_compliant", "is_overall_compliant",
        ]
    }
    metrics["duplicate_pairs_count"] = len(report.duplicate_pairs_above_90pct)
    receipt_body = {
        "$schema": "urn:nee:rules-budget-receipt:1.0.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "status": "PASSED" if report.is_overall_compliant else "FAILED",
        "metrics": metrics,
        "errors": report.errors,
        "duplicate_pairs": [d.to_dict() for d in report.duplicate_pairs_above_90pct],
        "itemized_rules": [
            {"rule_name": r.rule_name, "trigger_mode": r.trigger_mode, "tokens": r.token_count, "bytes": r.file_bytes, "is_compliant": not r.violations}
            for r in report.rules
        ],
    }
    serialized = json.dumps(receipt_body, sort_keys=True, separators=(",", ":"))
    receipt_body["canonical_digest"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return receipt_body


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit workspace rules context budget, 90% capacity cleanup & deduplication.")
    parser.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES_DIR)
    parser.add_argument("--agents-md", type=Path, default=DEFAULT_AGENTS_MD)
    parser.add_argument("--budget", type=int, default=DEFAULT_MAX_BUDGET_TOKENS)
    parser.add_argument("--capacity-threshold", type=float, default=DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT)
    parser.add_argument("--duplication-threshold", type=float, default=DEFAULT_DUPLICATION_THRESHOLD_PCT)
    parser.add_argument("--cleanup-at-capacity", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    fn = deduplicate_and_compact_rules if args.cleanup_at_capacity else audit_rules_budget
    res = fn(
        rules_dir=args.rules_dir, agents_md=args.agents_md,
        max_budget_tokens=args.budget, capacity_threshold_pct=args.capacity_threshold,
        duplication_threshold_pct=args.duplication_threshold,
    )
    report = res["report"] if args.cleanup_at_capacity else res

    if args.receipt:
        receipt = build_audit_receipt(report, command=" ".join(sys.argv))
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        status_line = "PASSED" if report.is_overall_compliant else "FAILED"
        print(
            f"Rules Governance Audit: {report.total_rule_files} files "
            f"({report.always_on_files_count} always_on + {report.model_decision_files_count} on_demand), "
            f"{report.total_always_on_tokens}/{report.budget_token_limit} always_on tokens ({report.budget_utilization_pct}%) "
            f"[DEDUP: {'OK' if report.is_dedup_compliant else f'{len(report.duplicate_pairs_above_90pct)} DUPLICATES'}] -> {status_line}"
        )
        for err in report.errors:
            print(f"  ❌ {err}")

    return 1 if (args.check and not report.is_overall_compliant) else 0


if __name__ == "__main__":
    sys.exit(main())
