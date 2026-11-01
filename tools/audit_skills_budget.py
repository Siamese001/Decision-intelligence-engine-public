#!/usr/bin/env python3
"""Workspace Skills Context Budget, 90% Capacity Cleanup & Governance Auditor.

Inspects all skills in .agents/skills/ and .agents/plugins/*/skills/, validating:
  1. Per-skill description token ceiling (<= 100 tokens).
  2. Total workspace skills context budget (<= 2,000 tokens, <= 100.0% utilization).
  3. Total skill count ceiling (<= 26 canonical and plugin skills).
  4. Proactive 90% capacity cleanup trigger (token or count utilization >= 90.0%).
  5. Model-neutral naming and description compliance (no vendor branding in text or files).
  6. Disk presence of referenced scripts, modules, and links (zero ghost references).
  7. Strict SSOT consistency between disk skills and .agents/skills.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SKILLS_DIR = ROOT / ".agents" / "skills"
DEFAULT_PLUGINS_DIR = ROOT / ".agents" / "plugins"
DEFAULT_SKILLS_JSON = ROOT / ".agents" / "skills.json"
DEFAULT_MAX_BUDGET_TOKENS: int = 2000
DEFAULT_MAX_DESCRIPTION_TOKENS: int = 100
DEFAULT_MAX_SKILLS_COUNT: int = 26
DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT: float = 90.0
BANNED_MODEL_TOKENS: Sequence[str] = ("gpt-", "openai", "claude", "gemini", "llama", "chatgpt", "codex")
STDLIB_OR_SYSTEM_MODULES: Sequence[str] = ("unittest", "pytest", "json", "http.server", "venv", "sys", "os")

_FILLER_PATTERNS: Sequence[Tuple[re.Pattern[str], str]] = (
    (re.compile(r"\b(?:Use this skill when|Use when|Activate this skill when|This skill provides|Comprehensive guide and reference for)\b\s*", re.IGNORECASE), ""),
    (re.compile(r"\s+"), " "),
)


@dataclass
class SkillAuditResult:
    skill_name: str
    skill_path: str
    skill_type: str  # "core" or "plugin"
    description: str
    description_char_length: int
    description_token_count: int
    full_skill_token_count: int
    max_description_tokens: int
    is_description_compliant: bool
    is_model_neutral: bool
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillsBudgetReport:
    total_skills: int
    canonical_skills_count: int
    plugin_skills_count: int
    max_skills_limit: int
    total_description_tokens: int
    total_full_tokens: int
    budget_token_limit: int
    budget_utilization_pct: float
    is_budget_compliant: bool
    is_count_compliant: bool
    is_ssot_compliant: bool
    is_overall_compliant: bool
    skills: List[SkillAuditResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    count_utilization_pct: float = 0.0
    max_capacity_utilization_pct: float = 0.0
    cleanup_threshold_pct: float = DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT
    is_at_or_above_capacity_threshold: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def count_tokens(text: str) -> int:
    """Calculate token count using cl100k_base or 4-character fallback heuristic."""
    if not text.strip():
        return 0
    if tiktoken is not None:
        try:
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Parse YAML frontmatter between --- markers without external dependencies."""
    frontmatter: Dict[str, str] = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_fm, body = parts[1], parts[2]
            current_key: Optional[str] = None
            current_val: List[str] = []
            for raw_line in raw_fm.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"^([a-zA-Z0-9_-]+)\s*:\s*(.*)$", raw_line)
                if match and not raw_line.startswith((" ", "\t")):
                    if current_key:
                        frontmatter[current_key] = " ".join(current_val).strip()
                    current_key, val = match.group(1), match.group(2).strip()
                    current_val = [] if val in (">", "|") else ([val.strip("\"'")] if val else [])
                elif current_key and raw_line.startswith((" ", "\t")):
                    current_val.append(line.strip("\"'"))
            if current_key:
                frontmatter[current_key] = " ".join(current_val).strip()
    return frontmatter, body


def verify_skill_disk_references(skill_dir: Path, content: str, repo_root: Path) -> List[str]:
    """Verify that all referenced scripts, modules, and links in SKILL.md exist on disk."""
    violations: List[str] = []
    for p in skill_dir.rglob("*"):
        for banned in BANNED_MODEL_TOKENS:
            if banned in p.name.lower():
                violations.append(f"Model neutrality file violation: '{p.relative_to(skill_dir)}' contains '{banned}'")

    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
        text, target = m.group(1), m.group(2)
        if target.startswith("file://"):
            raw_target = Path(target.replace("file://", ""))
            target_exists = raw_target.exists()
            if not target_exists and ".worktrees" in repo_root.parts:
                wt_idx = repo_root.parts.index(".worktrees")
                main_root = Path(*repo_root.parts[:wt_idx])
                try:
                    target_exists = (repo_root / raw_target.relative_to(main_root)).exists()
                except ValueError:
                    pass
            if not target_exists:
                violations.append(f"Broken absolute link: {text} -> {target}")
        elif not target.startswith(("http", "#", "mailto")):
            p_rel = (skill_dir / target.split("#")[0]).resolve()
            p_repo = (repo_root / target.split("#")[0]).resolve()
            if not p_rel.exists() and not p_repo.exists():
                violations.append(f"Broken relative link: {text} -> {target}")

    for block in re.findall(r"```(?:bash|sh)\n(.*?)```", content, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "python":
                if "-m" in parts:
                    m_idx = parts.index("-m")
                    if m_idx + 1 < len(parts):
                        mod = parts[m_idx + 1]
                        if mod.split(".")[0] not in STDLIB_OR_SYSTEM_MODULES:
                            p_py = repo_root / (mod.replace(".", "/") + ".py")
                            p_init = repo_root / mod.replace(".", "/") / "__init__.py"
                            if not p_py.is_file() and not p_init.is_file():
                                violations.append(f"Referenced Python module does not exist on disk: 'python -m {mod}'")
                else:
                    for arg in parts[1:]:
                        if arg.endswith(".py") and not arg.startswith("-"):
                            if not (repo_root / arg).is_file() and not (skill_dir / arg).is_file():
                                violations.append(f"Referenced script does not exist on disk: '{arg}'")
                            break
    return violations


def audit_single_skill(
    skill_dir: Path,
    max_desc_tokens: int = DEFAULT_MAX_DESCRIPTION_TOKENS,
    skill_type: str = "core",
    repo_root: Path = ROOT,
    verify_disk: bool = True,
) -> SkillAuditResult:
    """Audit a single skill directory for frontmatter, token limits, model neutrality, and disk references."""
    skill_md, skill_name = skill_dir / "SKILL.md", skill_dir.name
    if not skill_md.is_file():
        return SkillAuditResult(skill_name, str(skill_dir), skill_type, "", 0, 0, 0, max_desc_tokens, False, True, ["Missing SKILL.md file"])
    try:
        content = skill_md.read_text(encoding="utf-8")
    except Exception as err:
        return SkillAuditResult(skill_name, str(skill_dir), skill_type, "", 0, 0, 0, max_desc_tokens, False, True, [f"Unreadable SKILL.md: {err}"])

    violations: List[str] = []
    fm, _ = parse_frontmatter(content)
    declared_name, description = fm.get("name", ""), fm.get("description", "")
    if not declared_name:
        violations.append("Missing 'name' field in SKILL.md frontmatter")
    elif declared_name != skill_name:
        violations.append(f"Declared name '{declared_name}' does not match directory '{skill_name}'")
    if not description:
        violations.append("Missing 'description' field in SKILL.md frontmatter")

    desc_tokens, full_tokens = count_tokens(description), count_tokens(content)
    is_desc_compliant = desc_tokens <= max_desc_tokens
    if not is_desc_compliant:
        violations.append(f"Description tokens ({desc_tokens}) exceeds maximum ceiling ({max_desc_tokens})")

    banned = [t for t in BANNED_MODEL_TOKENS if t in description.lower()]
    if banned:
        violations.append(f"LLM neutrality violation: banned tokens {banned}")
    if verify_disk:
        violations.extend(verify_skill_disk_references(skill_dir, content, repo_root))

    return SkillAuditResult(
        skill_name=skill_name,
        skill_path=str(skill_dir),
        skill_type=skill_type,
        description=description,
        description_char_length=len(description),
        description_token_count=desc_tokens,
        full_skill_token_count=full_tokens,
        max_description_tokens=max_desc_tokens,
        is_description_compliant=is_desc_compliant,
        is_model_neutral=len(banned) == 0,
        violations=violations,
    )


def audit_skills_budget(
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    max_budget_tokens: int = DEFAULT_MAX_BUDGET_TOKENS,
    max_desc_tokens: int = DEFAULT_MAX_DESCRIPTION_TOKENS,
    max_skills_count: int = DEFAULT_MAX_SKILLS_COUNT,
    plugins_dir: Optional[Path] = DEFAULT_PLUGINS_DIR,
    skills_json: Path = DEFAULT_SKILLS_JSON,
    repo_root: Path = ROOT,
    verify_disk: bool = True,
    capacity_threshold_pct: float = DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
) -> SkillsBudgetReport:
    """Audit all skills in skills_dir and plugins_dir against context budget, 90% capacity, SSOT, and disk verification."""
    if not skills_dir.is_dir():
        return SkillsBudgetReport(0, 0, 0, max_skills_count, 0, 0, max_budget_tokens, 0.0, False, False, False, False, [], [f"Not found: {skills_dir}"])

    subdirs = sorted([p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith(".")])
    results = [audit_single_skill(s, max_desc_tokens, "core", repo_root, verify_disk) for s in subdirs]

    plugin_results: List[SkillAuditResult] = []
    if plugins_dir and plugins_dir.is_dir():
        for pdir in sorted(plugins_dir.iterdir()):
            pskills = pdir / "skills"
            if pdir.is_dir() and not pdir.name.startswith(".") and pskills.is_dir():
                for sp in sorted(pskills.iterdir()):
                    if sp.is_dir() and not sp.name.startswith("."):
                        plugin_results.append(audit_single_skill(sp, max_desc_tokens, "plugin", repo_root, verify_disk))

    all_results = results + plugin_results
    total_skills = len(all_results)
    total_desc = sum(r.description_token_count for r in all_results)
    total_full = sum(r.full_skill_token_count for r in all_results)
    errors = [f"[{r.skill_name}] {v}" for r in all_results for v in r.violations]

    is_ssot_ok = True
    if skills_json.is_file():
        try:
            data = json.loads(skills_json.read_text(encoding="utf-8"))
            reg_canon, disk_canon = {s["name"] for s in data.get("skills", [])}, {r.skill_name for r in results}
            if reg_canon != disk_canon:
                is_ssot_ok = False
                errors.append(f"SSOT mismatch in canonical skills: JSON={reg_canon - disk_canon}, Disk={disk_canon - reg_canon}")

            reg_plug, disk_plug = {s["name"] for s in data.get("plugin_skills", [])}, {r.skill_name for r in plugin_results}
            if reg_plug != disk_plug:
                is_ssot_ok = False
                errors.append(f"SSOT mismatch in plugin skills: JSON={reg_plug - disk_plug}, Disk={disk_plug - reg_plug}")
        except Exception as e:
            is_ssot_ok = False
            errors.append(f"Failed to read skills.json for SSOT verification: {e}")
    else:
        is_ssot_ok = False
        errors.append(f"Missing skills.json at {skills_json}")

    is_count_ok = total_skills <= max_skills_count
    if not is_count_ok:
        errors.append(f"Total skills ({total_skills}) exceed ceiling ({max_skills_count})")

    utilization_pct = round((total_desc / max(1, max_budget_tokens)) * 100.0, 2)
    count_utilization_pct = round((total_skills / max(1, max_skills_count)) * 100.0, 2)
    max_cap_pct = max(utilization_pct, count_utilization_pct)
    at_or_above_cap = max_cap_pct >= capacity_threshold_pct

    is_budget_ok = total_desc <= max_budget_tokens
    if not is_budget_ok:
        errors.append(f"Description tokens ({total_desc}) exceeds budget ({max_budget_tokens}) [{utilization_pct}%]")

    is_overall = is_count_ok and is_budget_ok and is_ssot_ok and all(not r.violations for r in all_results)

    return SkillsBudgetReport(
        total_skills=total_skills,
        canonical_skills_count=len(results),
        plugin_skills_count=len(plugin_results),
        max_skills_limit=max_skills_count,
        total_description_tokens=total_desc,
        total_full_tokens=total_full,
        budget_token_limit=max_budget_tokens,
        budget_utilization_pct=utilization_pct,
        is_budget_compliant=is_budget_ok,
        is_count_compliant=is_count_ok,
        is_ssot_compliant=is_ssot_ok,
        is_overall_compliant=is_overall,
        skills=all_results,
        errors=errors,
        count_utilization_pct=count_utilization_pct,
        max_capacity_utilization_pct=max_cap_pct,
        cleanup_threshold_pct=capacity_threshold_pct,
        is_at_or_above_capacity_threshold=at_or_above_cap,
    )


def compact_skill_description(description: str, target_max_tokens: int = 35) -> str:
    """Deterministically compact a verbose skill description while preserving core domain keywords."""
    cleaned = description.strip()
    for pat, repl in _FILLER_PATTERNS:
        cleaned = pat.sub(repl, cleaned).strip()
    if count_tokens(cleaned) <= target_max_tokens:
        return cleaned
    words = cleaned.split()
    while len(words) > 6 and count_tokens(" ".join(words)) > target_max_tokens:
        words.pop()
    compacted = " ".join(words).rstrip(".,;:") + "."
    return compacted


def cleanup_skills_at_capacity(
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    plugins_dir: Optional[Path] = DEFAULT_PLUGINS_DIR,
    skills_json: Path = DEFAULT_SKILLS_JSON,
    max_budget_tokens: int = DEFAULT_MAX_BUDGET_TOKENS,
    max_desc_tokens: int = DEFAULT_MAX_DESCRIPTION_TOKENS,
    max_skills_count: int = DEFAULT_MAX_SKILLS_COUNT,
    capacity_threshold_pct: float = DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
    repo_root: Path = ROOT,
    verify_disk: bool = True,
) -> Dict[str, Any]:
    """Clean up skills when token or count capacity is >= capacity_threshold_pct (default 90.0%).

    1. Prunes unregistered/deprecated skill directories not present in skills.json when count >= 90%.
    2. Compacts verbose SKILL.md frontmatter descriptions to reduce token overhead below 90% capacity.
    3. Synchronizes .agents/skills.json counts with disk state.
    """
    initial = audit_skills_budget(
        skills_dir=skills_dir,
        max_budget_tokens=max_budget_tokens,
        max_desc_tokens=max_desc_tokens,
        max_skills_count=max_skills_count,
        plugins_dir=plugins_dir,
        skills_json=skills_json,
        repo_root=repo_root,
        verify_disk=verify_disk,
        capacity_threshold_pct=capacity_threshold_pct,
    )
    actions: List[Dict[str, Any]] = []
    if not initial.is_at_or_above_capacity_threshold:
        return {
            "triggered": False,
            "threshold_pct": capacity_threshold_pct,
            "before_token_utilization_pct": initial.budget_utilization_pct,
            "after_token_utilization_pct": initial.budget_utilization_pct,
            "before_count_utilization_pct": initial.count_utilization_pct,
            "after_count_utilization_pct": initial.count_utilization_pct,
            "actions": actions,
            "report": initial,
        }

    # Step 1: If skill count utilization >= threshold and unregistered directories exist on disk, prune them
    if initial.count_utilization_pct >= capacity_threshold_pct and skills_json.is_file():
        try:
            reg_data = json.loads(skills_json.read_text(encoding="utf-8"))
            registered_core = {s["name"] for s in reg_data.get("skills", [])}
            for sub in sorted(skills_dir.iterdir()):
                if sub.is_dir() and not sub.name.startswith(".") and sub.name not in registered_core:
                    shutil.rmtree(sub)
                    actions.append({"action": "pruned_unregistered_skill", "skill_name": sub.name})
        except Exception:
            pass

    # Step 2: Compact verbose descriptions in descending order of token count until below threshold
    target_tokens = int(max_budget_tokens * (capacity_threshold_pct - 5.0) / 100.0)
    sorted_skills = sorted(initial.skills, key=lambda s: s.description_token_count, reverse=True)
    current_tokens = initial.total_description_tokens
    per_skill_target = max(12, min(35, target_tokens // max(1, len(sorted_skills))))

    for s_res in sorted_skills:
        if current_tokens <= target_tokens and s_res.description_token_count <= max_desc_tokens:
            break
        skill_md = Path(s_res.skill_path) / "SKILL.md"
        if not skill_md.is_file():
            continue
        raw = skill_md.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(raw)
        old_desc = fm.get("description", "")
        new_desc = compact_skill_description(old_desc, target_max_tokens=per_skill_target)
        old_t, new_t = count_tokens(old_desc), count_tokens(new_desc)
        if new_t < old_t:
            new_fm_lines = ["---", f"name: {fm.get('name', s_res.skill_name)}", f"description: {new_desc}"]
            for k, v in fm.items():
                if k not in ("name", "description"):
                    new_fm_lines.append(f"{k}: {v}")
            new_fm_lines.append("---")
            skill_md.write_text("\n".join(new_fm_lines) + "\n" + body.lstrip("\n"), encoding="utf-8")
            current_tokens -= (old_t - new_t)
            actions.append({
                "action": "compacted_description",
                "skill_name": s_res.skill_name,
                "tokens_before": old_t,
                "tokens_after": new_t,
            })

    final = audit_skills_budget(
        skills_dir=skills_dir,
        max_budget_tokens=max_budget_tokens,
        max_desc_tokens=max_desc_tokens,
        max_skills_count=max_skills_count,
        plugins_dir=plugins_dir,
        skills_json=skills_json,
        repo_root=repo_root,
        verify_disk=verify_disk,
        capacity_threshold_pct=capacity_threshold_pct,
    )
    return {
        "triggered": True,
        "threshold_pct": capacity_threshold_pct,
        "before_token_utilization_pct": initial.budget_utilization_pct,
        "after_token_utilization_pct": final.budget_utilization_pct,
        "before_count_utilization_pct": initial.count_utilization_pct,
        "after_count_utilization_pct": final.count_utilization_pct,
        "actions": actions,
        "report": final,
    }


def build_audit_receipt(report: SkillsBudgetReport, command: str = "tools/audit_skills_budget.py --check") -> Dict[str, Any]:
    """Generate a governed runtime receipt for the skills context budget audit."""
    receipt_body = {
        "$schema": "urn:nee:skills-budget-receipt:1.0.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "status": "PASSED" if report.is_overall_compliant else "FAILED",
        "metrics": {
            "total_skills": report.total_skills,
            "canonical_skills_count": report.canonical_skills_count,
            "plugin_skills_count": report.plugin_skills_count,
            "max_skills_limit": report.max_skills_limit,
            "total_description_tokens": report.total_description_tokens,
            "budget_token_limit": report.budget_token_limit,
            "budget_utilization_pct": report.budget_utilization_pct,
            "count_utilization_pct": report.count_utilization_pct,
            "max_capacity_utilization_pct": report.max_capacity_utilization_pct,
            "cleanup_threshold_pct": report.cleanup_threshold_pct,
            "is_at_or_above_capacity_threshold": report.is_at_or_above_capacity_threshold,
            "is_budget_compliant": report.is_budget_compliant,
            "is_count_compliant": report.is_count_compliant,
            "is_ssot_compliant": report.is_ssot_compliant,
            "is_overall_compliant": report.is_overall_compliant,
        },
        "errors": report.errors,
        "itemized_skills": [
            {
                "skill_name": s.skill_name,
                "skill_type": s.skill_type,
                "description_tokens": s.description_token_count,
                "is_compliant": len(s.violations) == 0,
                "violations": s.violations,
            }
            for s in report.skills
        ],
    }
    serialized = json.dumps(receipt_body, sort_keys=True, separators=(",", ":"))
    receipt_body["canonical_digest"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return receipt_body


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit workspace skills context budget, 90% capacity cleanup & governance.")
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--plugins-dir", type=Path, default=DEFAULT_PLUGINS_DIR)
    parser.add_argument("--skills-json", type=Path, default=DEFAULT_SKILLS_JSON)
    parser.add_argument("--budget", type=int, default=DEFAULT_MAX_BUDGET_TOKENS)
    parser.add_argument("--max-desc-tokens", type=int, default=DEFAULT_MAX_DESCRIPTION_TOKENS)
    parser.add_argument("--max-skills", type=int, default=DEFAULT_MAX_SKILLS_COUNT)
    parser.add_argument("--capacity-threshold", type=float, default=DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT)
    parser.add_argument("--cleanup-at-capacity", action="store_true", help="Clean up skills when >= capacity threshold (90%)")
    parser.add_argument("--no-verify-disk", action="store_true", help="Skip disk reference validation")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.cleanup_at_capacity:
        cleanup_res = cleanup_skills_at_capacity(
            skills_dir=args.skills_dir,
            plugins_dir=args.plugins_dir,
            skills_json=args.skills_json,
            max_budget_tokens=args.budget,
            max_desc_tokens=args.max_desc_tokens,
            max_skills_count=args.max_skills,
            capacity_threshold_pct=args.capacity_threshold,
            verify_disk=not args.no_verify_disk,
        )
        report = cleanup_res["report"]
    else:
        report = audit_skills_budget(
            skills_dir=args.skills_dir,
            max_budget_tokens=args.budget,
            max_desc_tokens=args.max_desc_tokens,
            max_skills_count=args.max_skills,
            plugins_dir=args.plugins_dir,
            skills_json=args.skills_json,
            verify_disk=not args.no_verify_disk,
            capacity_threshold_pct=args.capacity_threshold,
        )

    if args.receipt:
        receipt = build_audit_receipt(report, command=" ".join(sys.argv))
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        status_line = "PASSED" if report.is_overall_compliant else "FAILED"
        print(
            f"Skills Governance Audit: {report.total_skills}/{report.max_skills_limit} skills "
            f"({report.canonical_skills_count} core + {report.plugin_skills_count} plugins, {report.count_utilization_pct}% count cap), "
            f"{report.total_description_tokens}/{report.budget_token_limit} tokens ({report.budget_utilization_pct}%) "
            f"[SSOT: {'OK' if report.is_ssot_compliant else 'MISMATCH'}] -> {status_line}"
        )
        for s in report.skills:
            if s.violations:
                print(f"  ❌ [{s.skill_type}] {s.skill_name}: {s.violations}")

    return 1 if (args.check and not report.is_overall_compliant) else 0


if __name__ == "__main__":
    sys.exit(main())
