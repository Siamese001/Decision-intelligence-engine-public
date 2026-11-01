"""
dual_pipeline/project_aliases.py

Layer 0 Project Taxonomy and Alias Resolution Engine.
Canonicalizes colloquial project aliases against config/governance/project_registry.json.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

UNRESOLVED_PROJECT_SENTINEL: str = "UNRESOLVED"


def get_project_domains() -> dict[str, Any]:
    """Returns the canonical 7-domain hierarchical taxonomy from SSOT."""
    reg_path = (
        Path(__file__).resolve().parent.parent / "config" / "governance" / "project_registry.json"
    )
    if reg_path.exists():
        with contextlib.suppress(Exception), open(reg_path, "r", encoding="utf-8") as f:
            return json.load(f).get("domains", {})
    return {}


def get_domain_for_project(proj: str) -> Optional[str]:
    """Maps a project identifier, workstream, or alias to its canonical domain."""
    if not proj:
        return None
    canonical = resolve_project_name(proj)
    domains = get_project_domains()
    for dom_key, dom_data in domains.items():
        workstreams = dom_data.get("workstreams", {})
        for ws_slug, ws_meta in workstreams.items():
            if canonical == ws_meta.get("short_id") or canonical == ws_slug:
                return dom_key
            if canonical in ws_meta.get("aliases", []):
                return dom_key
    # Fallback domain mapping heuristics for standard canonical IDs
    domain_map = {
        "dociq": "NEA",
        "project_lens": "NEA",
        "project_jarvis": "NEA",
        "project_nexus": "NEA",
        "conduit": "NEA",
        "regai": "NEA",
        "ec_scheduling": "NEA",
        "nea": "NEA",
        "project_spring": "EDO",
        "edo": "EDO",
        "apex_trans": "APEX_TRANS",
        "project_beacon": "NEE_IT",
        "enterprise_architecture": "NEE_IT",
        "infrastructure_automation": "NEE_IT",
        "gemini_enablement": "NEE_IT",
        "bmo": "NEE_IT",
        "hrit": "NEE_IT",
        "project_atlas": "ENTERPRISE_OPS",
        "enterprise_account_planning": "INTERNAL_CONSULTING",
        "partner_alliances": "INTERNAL_CONSULTING",
        "internal_consulting": "INTERNAL_CONSULTING",
        "market_intelligence": "MARKET_INTELLIGENCE",
    }
    return domain_map.get(canonical)


def get_known_projects() -> set[str]:
    """Returns canonical set of known project taxonomy identifiers from SSOT and projects.d/."""
    reg_path = (
        Path(__file__).resolve().parent.parent / "config" / "governance" / "project_registry.json"
    )
    projects: set[str] = set()
    if reg_path.exists():
        with contextlib.suppress(Exception), open(reg_path, "r", encoding="utf-8") as f:
            projects.update(json.load(f).get("known_projects", []))
    frag_dir = reg_path.parent / "projects.d"
    if frag_dir.is_dir():
        for frag in sorted(frag_dir.glob("*.json")):
            with contextlib.suppress(Exception), open(frag, "r", encoding="utf-8") as f:
                projects.update(json.load(f).get("known_projects", []))
    if projects:
        return projects
    return {
        "bmo",
        "conduit",
        "project_beacon",
        "dociq",
        "ec_scheduling",
        "edo",
        "enterprise_architecture",
        "enterprise_account_planning",
        "project_atlas",
        "gemini_enablement",
        "hrit",
        "infrastructure_automation",
        "internal_consulting",
        "market_intelligence",
        "nea",
        "apex_trans",
        "partner_alliances",
        "project_jarvis",
        "project_lens",
        "project_nexus",
        "project_spring",
        "regai",
    }


def get_project_aliases() -> dict[str, str]:
    """Returns canonical mapping of colloquial project aliases to SSOT identifiers."""
    reg_path = (
        Path(__file__).resolve().parent.parent / "config" / "governance" / "project_registry.json"
    )
    aliases: dict[str, str] = {}
    if reg_path.exists():
        with contextlib.suppress(Exception), open(reg_path, "r", encoding="utf-8") as f:
            aliases.update(json.load(f).get("project_aliases", {}))
    frag_dir = reg_path.parent / "projects.d"
    if frag_dir.is_dir():
        for frag in sorted(frag_dir.glob("*.json")):
            with contextlib.suppress(Exception), open(frag, "r", encoding="utf-8") as f:
                aliases.update(json.load(f).get("project_aliases", {}))
    if aliases:
        return aliases
    return {
        "dociq": "dociq",
        "project dociq": "dociq",
        "project_dociq": "dociq",
        "pgd dociq": "dociq",
        "jarvis": "project_jarvis",
        "project jarvis": "project_jarvis",
        "lens": "project_lens",
        "nexus": "project_nexus",
        "project nexus": "project_nexus",
        "alliances": "partner_alliances",
        "partner gcp": "partner_alliances",
        "partner google": "partner_alliances",
        "partner sync": "partner_alliances",
        "sherpa": "project_beacon",
        "nea": "nea",
        "spring": "project_spring",
        "rewire": "project_atlas",
        "project atlas": "project_atlas",
        "partner_advisory": "internal_consulting",
        "fcdb": "regai",
        "apex_trans": "apex_trans",
        "conduit": "conduit",
        "hrit": "hrit",
        "hr it": "hrit",
        "enterprise4u": "hrit",
        "market intel": "market_intelligence",
        "weekly trends": "market_intelligence",
        "energy utilities": "market_intelligence",
        "energy and utilities": "market_intelligence",
        "energy & utilities": "market_intelligence",
        "market trends": "market_intelligence",
    }


def resolve_project_name(name: str) -> str:
    """Canonicalizes a raw project name or colloquial alias against the SSOT project registry."""
    raw = (name or "").strip().lower()
    aliases = get_project_aliases()
    if raw in aliases:
        return aliases[raw]
    norm = raw.replace("-", "_").replace(" ", "_")
    if norm in aliases:
        return aliases[norm]
    known = get_known_projects()
    if norm in known:
        return norm
    inferred = infer_project_from_title(raw)
    if inferred:
        return inferred
    return norm or "project_lens"


def infer_project_from_title(title: Optional[str]) -> Optional[str]:
    """Infers canonical project name from explicit keywords in meeting or document title."""
    if not title:
        return None
    import re
    tokens = set(re.split(r"[^a-z0-9]+", title.strip().lower()))
    if not tokens:
        return None
    # Priority-ordered project keyword taxonomy
    keyword_map = (
        ({"dociq"}, "dociq"),
        ({"jarvis"}, "project_jarvis"),
        ({"nexus"}, "project_nexus"),
        ({"lens"}, "project_lens"),
        ({"sherpa"}, "project_beacon"),
        ({"spring"}, "project_spring"),
        ({"rewire"}, "project_atlas"),
        ({"conduit"}, "conduit"),
        ({"hrit", "enterprise4u"}, "hrit"),
        ({"apex_trans"}, "apex_trans"),
        ({"nea"}, "nea"),
        ({"regai", "fcdb"}, "regai"),
        ({"immersion", "partner_advisory"}, "internal_consulting"),
        ({"partner", "alliances"}, "partner_alliances"),
        (
            {
                "trends",
                "market",
                "markets",
                "utilities",
                "utility",
                "utilitieds",
                "intel",
                "intelligence",
                "energy",
            },
            "market_intelligence",
        ),
    )
    for kw_set, proj in keyword_map:
        if tokens & kw_set:
            return proj
    return None

