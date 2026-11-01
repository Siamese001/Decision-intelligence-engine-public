"""Common data models, path discovery, and file utilities for governance harmonization."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Repo root detection ─────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
PROJECT_INSTRUCTIONS_MD = REPO_ROOT / "PROJECT_INSTRUCTIONS.md"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
RULES_DIR = REPO_ROOT / ".agents" / "rules"
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
SKILLS_JSON = REPO_ROOT / ".agents" / "skills.json"
HOOKS_JSON = REPO_ROOT / ".agents" / "hooks.json"
MCP_CONFIG_JSON = REPO_ROOT / ".agents" / "mcp_config.json"
PLUGINS_DIR = REPO_ROOT / ".agents" / "plugins"
GITHOOKS_DIR = REPO_ROOT / ".githooks"
TOOLS_DIR = REPO_ROOT / "tools"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
GOVERNANCE_DIR = REPO_ROOT / "governance"
CONFIG_DIR = REPO_ROOT / "config"
AUTOMATIONS_DIR = REPO_ROOT / "automations"
CONTRACTS_DIR = REPO_ROOT / "contracts"

DISCOVERY_SCOPE_EXCLUSIONS: list[str] = [
    "tools/audit_governance_harmonization.py (scanner CLI entrypoint)",
    "tools/audit_governance_harmonization_core/* (scanner implementation modules)",
    "governance/rca/* (historical RCA post-mortems)",
    "governance/plans/* (historical frozen plan documentation)",
    "**/__pycache__/* (ephemeral Python bytecode cache)",
    "**/.pytest_cache/* (ephemeral pytest cache)",
]


# ── Data classes ─────────────────────────────────────────────────────────
@dataclass
class Finding:
    """A single harmonization finding."""

    category: str  # threshold_drift | duplication | overloading | taxonomy | missing_backref
    severity: str  # CRITICAL | WARNING | INFO
    title: str
    description: str
    ssot_file: str
    ssot_value: str
    affected_files: list[dict[str, Any]] = field(default_factory=list)
    # Each affected_file: {"path": str, "line": int|None, "current_value": str, "expected_value": str}
    proposed_fix: str = ""


@dataclass
class HarmonizationReport:
    """Full scan output."""

    scan_timestamp: str
    repo_root: str
    scanner_version: str = "1.0.0"
    total_files_scanned: int = 0
    findings: list[Finding] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def finalize(self) -> None:
        counts: dict[str, int] = defaultdict(int)
        for f in self.findings:
            counts[f.category] += 1
            counts[f"severity_{f.severity}"] += 1
        counts["total"] = len(self.findings)
        self.summary = dict(counts)


def _collect_skill_files(skills_dir: Path) -> list[Path]:
    """Collect skill definitions, scripts, and references."""
    if not skills_dir.is_dir():
        return []
    collected: list[Path] = []
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        if (d / "SKILL.md").exists():
            collected.append(d / "SKILL.md")
        for sub in ("scripts", "references", "resources"):
            sub_path = d / sub
            if sub_path.is_dir():
                collected.extend(sorted(p for p in sub_path.rglob("*") if p.is_file()))
    return collected


def _collect_plugin_files(plugins_dir: Path) -> list[Path]:
    """Collect plugin declarations, MCP declarations, and plugin skill definitions."""
    if not plugins_dir.is_dir():
        return []
    collected: list[Path] = []
    for p in sorted(plugins_dir.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".md", ".yaml", ".yml"):
            collected.append(p)
    return collected


def _collect_config_files(config_dir: Path) -> list[Path]:
    """Collect runtime and governance configurations."""
    if not config_dir.is_dir():
        return []
    collected: list[Path] = []
    for p in sorted(config_dir.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".yaml", ".yml", ".py"):
            collected.append(p)
    return collected


def _collect_automation_files(automations_dir: Path) -> list[Path]:
    """Collect automation prompt definitions, runners, and configs."""
    if not automations_dir.is_dir():
        return []
    collected: list[Path] = []
    for p in sorted(automations_dir.rglob("*")):
        if p.is_file() and p.suffix in (".md", ".py", ".json", ".yaml", ".yml"):
            collected.append(p)
    return collected


def _collect_githook_files(githooks_dir: Path) -> list[Path]:
    if not githooks_dir.is_dir():
        return []
    return [f for f in sorted(githooks_dir.iterdir()) if f.is_file()]


def _collect_governance_docs(gov_dir: Path) -> list[Path]:
    if not gov_dir.is_dir():
        return []
    return (
        sorted(gov_dir.rglob("*.md"))
        + sorted(gov_dir.rglob("*.json"))
        + sorted(gov_dir.rglob("*.yaml"))
        + sorted(gov_dir.rglob("*.yml"))
    )


import json

_TEXT_CACHE: dict[Path, tuple[int, int, str]] = {}
_JSON_CACHE: dict[Path, tuple[int, int, dict[str, Any], list[str]]] = {}


def clear_harmonization_caches() -> None:
    """Clear in-memory text and JSON caches."""
    _TEXT_CACHE.clear()
    _JSON_CACHE.clear()


def read_text_safe(path: Path) -> str:
    """Read file text with mtime/size caching, returning empty string on binary/decode errors."""
    try:
        resolved = path.resolve()
        st = resolved.stat()
        cached = _TEXT_CACHE.get(resolved)
        if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]
        text = resolved.read_text(encoding="utf-8", errors="replace")
        _TEXT_CACHE[resolved] = (st.st_mtime_ns, st.st_size, text)
        return text
    except Exception:
        return ""


def load_json_with_dup_check(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Parse JSON file once per (mtime_ns, size) while recording any duplicate keys."""
    resolved = path.resolve()
    st = resolved.stat()
    cached = _JSON_CACHE.get(resolved)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2], list(cached[3])

    text = read_text_safe(resolved)
    dupes: list[str] = []

    def _detect_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for k, _v in pairs:
            if k in seen:
                dupes.append(k)
            seen.add(k)
        return dict(pairs)

    parsed = json.loads(text, object_pairs_hook=_detect_dupes)
    data = parsed if isinstance(parsed, dict) else {}
    _JSON_CACHE[resolved] = (st.st_mtime_ns, st.st_size, data, list(dupes))
    return data, dupes


def relative(path: Path) -> str:
    """Return path relative to repo root."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _is_excluded_from_discovery(rel: str) -> bool:
    if rel == "tools/audit_governance_harmonization.py":
        return True
    if rel.startswith("tools/audit_governance_harmonization_core/"):
        return True
    if rel.startswith("governance/rca/") or rel.startswith("governance/plans/"):
        return True
    return "__pycache__" in rel or ".pytest_cache" in rel


# ── File discovery ───────────────────────────────────────────────────────
def discover_governance_files() -> list[Path]:
    """Return all governance-relevant files sorted by path."""
    files: list[Path] = []

    if AGENTS_MD.exists():
        files.append(AGENTS_MD)
    if PROJECT_INSTRUCTIONS_MD.exists():
        files.append(PROJECT_INSTRUCTIONS_MD)
    if PYPROJECT_TOML.exists():
        files.append(PYPROJECT_TOML)
    if SKILLS_JSON.exists():
        files.append(SKILLS_JSON)
    if HOOKS_JSON.exists():
        files.append(HOOKS_JSON)
    if MCP_CONFIG_JSON.exists():
        files.append(MCP_CONFIG_JSON)
    if RULES_DIR.is_dir():
        files.extend(sorted(RULES_DIR.glob("*.md")))
    files.extend(_collect_skill_files(SKILLS_DIR))
    files.extend(_collect_plugin_files(PLUGINS_DIR))
    files.extend(_collect_githook_files(GITHOOKS_DIR))
    if TOOLS_DIR.is_dir():
        files.extend(sorted(p for p in TOOLS_DIR.glob("*.py") if p.is_file()))
    if WORKFLOWS_DIR.is_dir():
        files.extend(sorted(WORKFLOWS_DIR.glob("*.yml")))
        files.extend(sorted(WORKFLOWS_DIR.glob("*.yaml")))
    files.extend(_collect_governance_docs(GOVERNANCE_DIR))
    files.extend(_collect_config_files(CONFIG_DIR))
    files.extend(_collect_automation_files(AUTOMATIONS_DIR))
    if CONTRACTS_DIR.is_dir():
        files.extend(sorted(p for p in CONTRACTS_DIR.glob("*.json") if p.is_file()))

    # Deduplicate while preserving order
    unique_files: list[Path] = []
    seen: set[Path] = set()
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    # Filter exclusions
    filtered: list[Path] = [
        f for f in unique_files
        if not _is_excluded_from_discovery(relative(f).replace("\\", "/"))
    ]

    return sorted(filtered, key=lambda p: relative(p))
