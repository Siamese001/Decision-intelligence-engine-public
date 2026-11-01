"""
dual_pipeline/identity_resolver_core/sources_loader.py

Modular loader utilities for ingesting identity catalogues from base definitions,
reconciliation JSON artifacts, PM Markdown rosters, and SQLite knowledge graphs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def load_base_identities(add_fn: Callable[..., None]) -> None:
    """Load core base identities across PartnerAdvisory and NEA Lens governance."""
    base_specs = [
        ("Amit Ayer", "person_amit_ayer", "PartnerAdvisory", None, ["Amit", "Mitt", "Amin"], "urn:nee:card-view:NEE_10 / Core Lens governance", None, None),
        ("Alex Sanchez", "person_alex_sanchez", "PartnerAdvisory", "Lead Software Engineer / Lens Developer", ["Alex", "Alex S", "alex.s@consulting.example.com", "Alston Cassio", "Alston", "Cassio", "Austin Cassio"], "approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json / Lens pod lead", None, None),
        ("Jen Pan", "person_jen_pan", "PartnerAdvisory", "Delivery Lead / Engagement PM", ["Jen", "Jennifer Pan", "Jenn"], "Core Lens delivery lead", None, None),
        ("Cameron Mercer", "person_stephane", "PartnerAdvisory", "PartnerAdvisory Engineering-Model and Q3 Alignment Owner", ["Stephan", "Stephane", "stephan.marceau@consulting.example.com"], "approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json / NEE_10", None, None),
        ("Stefanos Georgiades", "person_stefanos", "NEA", "Head of Platform Strategy / GenAI", ["Stefanos", "Stefano"], "urn:nee:card-view:NEE_10 / NEA leadership", None, None),
        ("Drew Oakley", "person_dan_okray", "PartnerAdvisory", "Client Partner / Account Lead", ["Dan", "Dan O", "dano@consulting.example.com"], "approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json / NEE_10", None, None),
        ("Tony Kablan", "person_tony_kablan", "PartnerAdvisory", "Former Lens Engagement Lead / PM", ["Tony", "Tony K"], "inputs/pm_roster_enrichment_20260727 / Colleen Barbato Roster", "Miami, FL", "LENS / Visual Asset Intelligence System"),
        ("harper Hoffman", "stakeholder_mandy_hopmann", "NEA", "Product Manager", ["Mandy", "Amanda", "Mandy Bambenek Hopmann"], "Colleen Barbato Charter Roster row 2; PM location roster; NEE_16", "Saint Paul / Minneapolis, Minnesota", "PD Conduit / Conductor"),
        ("Brett Gavagni", "person_brett_gavagni", "NEA", "Engineering Manager", ["Brett"], "Colleen Barbato Charter Roster row 2", None, "Power Delivery / Conduit / Lens"),
        ("Matthew Koch", "person_matthew_koch", "PartnerAdvisory", "Vertical Lead / Engineering Lead", ["Matt Koch", "Matthew", "Matt"], "approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json", None, None),
    ]

    for name, nid, org, role, aliases, basis, loc, charter in base_specs:
        add_fn(
            canonical_name=name,
            node_id=nid,
            organization=org,
            role=role,
            aliases=aliases,
            basis=basis,
            location=loc,
            charter=charter,
        )


def _find_reconciliation_path(repo_root: Path) -> Optional[Path]:
    candidates = [
        repo_root / "config/identity/identity_and_alias_reconciliation.json",
        repo_root / "approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json",
        repo_root / "archive/approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_json_reconciliations(
    repo_root: Path,
    add_fn: Callable[..., None],
    aliases: Dict[str, str],
    identities: Dict[str, Any],
    normalize_key_fn: Callable[[str], str],
) -> None:
    """Ingests identity reconciliation JSON data if available."""
    identity_path = _find_reconciliation_path(repo_root)
    if not identity_path:
        return

    try:
        with open(identity_path, "r", encoding="utf-8") as f:
            v51_data = json.load(f)

        for item in v51_data.get("identities", []):
            if item.get("active") != 1:
                continue
            name = item.get("proposed_name")
            status_desc = item.get("status", "")
            org = "PartnerAdvisory" if "PartnerAdvisory" in status_desc else "NEA"
            add_fn(
                canonical_name=name,
                node_id=item.get("node_id"),
                organization=org,
                role=status_desc,
                basis=f"{identity_path.relative_to(repo_root)} (lineage: approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json)",
            )

        for node_id, al_list in v51_data.get("aliases", {}).items():
            match_name = next((n for n, d in identities.items() if d.get("node_id") == node_id), None)
            if match_name:
                for al in al_list:
                    aliases[normalize_key_fn(al)] = match_name
    except Exception:
        pass


def load_pm_roster(
    repo_root: Path,
    add_fn: Callable[..., None],
    normalize_key_fn: Callable[[str], str],
) -> None:
    """Ingests PM Roster Enrichment Markdown roster."""
    pm_roster_path = repo_root / "inputs/pm_roster_enrichment_20260727/nea-pm-location-business-unit-roster.md"
    if not pm_roster_path.exists():
        return

    try:
        with open(pm_roster_path, "r", encoding="utf-8") as f:
            for line in f:
                if "|" not in line or "Product Manager" in line or "---" in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 6:
                    continue
                charter, pm_name, org, loc = parts[1], parts[2], parts[3], parts[5]
                if pm_name and len(pm_name) > 2:
                    add_fn(
                        canonical_name=pm_name,
                        node_id=f"stakeholder_{normalize_key_fn(pm_name)}",
                        organization=org,
                        role="Product Manager",
                        location=loc,
                        charter=charter,
                        basis="inputs/pm_roster_enrichment_20260727/nea-pm-location-business-unit-roster.md",
                    )
    except Exception:
        pass


def _find_sqlite_graph_path(repo_root: Path) -> Optional[Path]:
    try:
        from dual_pipeline.runtime_root import resolve_active_graph_path
        return resolve_active_graph_path(repo_root / ".local" / "dual_pipeline", enforce_integrity=False)
    except Exception:
        pass
    sqlite_candidates = [
        repo_root / "graph/nee_graph.sqlite",
        repo_root / "rollback/baseline/nee_graph_v53.sqlite",
        repo_root / "rollback/baseline/nee_graph_v52.sqlite",
    ]
    for db_path in sqlite_candidates:
        if db_path.exists():
            return db_path
    return None


def load_sqlite_identities(
    repo_root: Path,
    add_fn: Callable[..., None],
    db_path: Optional[Path] = None,
) -> None:
    """Ingests identities from local SQLite graph if available."""
    target_db = db_path or _find_sqlite_graph_path(repo_root)
    if not target_db:
        return
    try:
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        conn = GovernedStorageContext.connect(target_db, readonly=True)
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT node_id, canonical_name, node_type, coalesce(description, '') "
            "FROM node WHERE active=1 AND node_type IN ('stakeholder', 'person', 'internal_person');"
        ).fetchall()
        node_map = {}
        for node_id, c_name, _n_type, desc in rows:
            if not c_name:
                continue
            node_map[node_id] = c_name
            org = "PartnerAdvisory" if "PartnerAdvisory" in desc else ("NEA" if "NEA" in desc else "Unresolved")
            add_fn(
                canonical_name=c_name,
                node_id=node_id,
                organization=org,
                role=None,
                basis=f"sqlite:{db_path.name}",
            )
        try:
            ass_rows = cur.execute("""
                SELECT 
                    a.subject_node_id,
                    coalesce(o.canonical_name, a.object_literal, a.scope_text, '') AS role_text,
                    coalesce(a.confidence_score, 1.0) AS confidence,
                    coalesce(a.currentness_state, 'current') AS currentness_state,
                    coalesce(a.validation_state, 'needs_review') AS validation_state
                FROM assertion a
                LEFT JOIN node o ON a.object_node_id = o.node_id
                WHERE (
                    a.predicate IN ('HAS_ROLE', 'HAS_ROLE_IN', 'OWNS_OR_LEADS')
                    OR o.node_type = 'role_group'
                    OR a.scope_text LIKE '%HAS_ROLE%'
                )
                AND (a.superseded_by_assertion_id IS NULL OR a.superseded_by_assertion_id = '')
                AND (a.currentness_state IS NULL OR lower(a.currentness_state) NOT IN ('superseded', 'retired', 'invalidated', 'expired', 'retracted'))
                ORDER BY 
                    CASE WHEN lower(coalesce(a.currentness_state, '')) IN ('current', 'active', 'confirmed') AND lower(coalesce(a.currentness_state, '')) NOT IN ('historical', 'superseded', 'retired', 'invalidated') THEN 1 ELSE 0 END DESC,
                    CASE WHEN lower(coalesce(a.validation_state, '')) IN ('verified', 'confirmed', 'approved', 'human_verified', 'operator_verified') THEN 1 ELSE 0 END DESC,
                    a.confidence_score DESC, 
                    a.valid_from DESC
            """).fetchall()
            seen_subjects: set[str] = set()
            for sub_id, role_text, _conf, _cur_state, _val_state in ass_rows:
                if sub_id in node_map and role_text and sub_id not in seen_subjects:
                    clean_role = role_text.strip()
                    if "legacy canonical graph relationship" in clean_role.lower():
                        continue
                    m = re.search(r"(?:HAS_ROLE|HAS_ROLE_IN|OWNS_OR_LEADS)\s+([^\.]+)", clean_role)
                    if m:
                        clean_role = m.group(1).strip()
                    seen_subjects.add(sub_id)
                    add_fn(
                        canonical_name=node_map[sub_id],
                        node_id=sub_id,
                        role=clean_role,
                        basis=f"sqlite_assertion:{db_path.name}",
                    )
        except Exception:
            pass
        try:
            alias_rows = cur.execute("SELECT node_id, alias FROM alias WHERE active=1;").fetchall()
            for nid, alias_val in alias_rows:
                if nid in node_map and alias_val:
                    add_fn(
                        canonical_name=node_map[nid],
                        aliases=[alias_val],
                        basis=f"sqlite_alias:{db_path.name}",
                    )
        except Exception:
            pass
        conn.close()
    except Exception:
        pass


def load_canonical_directory_identities(
    repo_root: Path,
    add_fn: Callable[..., None],
    aliases: Dict[str, str],
    normalize_key_fn: Callable[[str], str],
) -> None:
    """Ingests canonical enterprise directory JSON into identity resolver."""
    manifest_p = repo_root / "dual_pipeline" / "transcript_metadata_core" / "canonical_enterprise_directory.json"
    if not manifest_p.exists():
        return

    try:
        with open(manifest_p, "r", encoding="utf-8") as f:
            raw = json.load(f)

        canon_to_meta: Dict[str, Tuple[str, str, List[str]]] = {}
        for key, val in raw.items():
            if not isinstance(val, (list, tuple)) or len(val) < 3:
                continue
            c_name, org, title = val[0], val[1], val[2]
            if c_name not in canon_to_meta:
                canon_to_meta[c_name] = (org, title, [])
            if key.lower() != c_name.lower():
                canon_to_meta[c_name][2].append(key)
                norm_key = normalize_key_fn(key)
                if norm_key:
                    aliases[norm_key] = c_name

        for c_name, (org, title, alias_list) in canon_to_meta.items():
            slug = normalize_key_fn(c_name)
            add_fn(
                canonical_name=c_name,
                node_id=f"stakeholder_{slug}",
                organization=org,
                role=title,
                aliases=alias_list,
                basis="canonical_enterprise_directory.json",
            )
    except Exception:
        pass

