"""
dual_pipeline/transcript_metadata_core/entity_resolver.py

Layer 1: Pre-synthesis entity resolution engine.
Queries the active Knowledge Graph (graph.sqlite), role assignment charters,
and enterprise directory priors before synthesis begins. Resolves attendee names,
nicknames, and initials into verified corporate entities, full names, roles, and titles.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .entity_resolver_graph import EntityCandidate

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

ORIGIN_INPUT_PROVENANCE: str = "ORIGIN_INPUT_PROVENANCE"


@dataclass(frozen=True)
class ResolvedEntity:
    """Immutable resolved enterprise entity."""
    canonical_name: str
    display_name: str
    org: str
    role: str
    source: str
    generation_id: Optional[str] = None
    assertion_id: Optional[str] = None
    provenance_id: Optional[str] = None
    temporal_scope: Optional[Dict[str, Any]] = None
    is_current: bool = True
    status: str = "RESOLVED"
    candidate_name: Optional[str] = None
    requires_operator_confirmation: bool = False
    confidence: float = 1.0
    provenance_origin: Optional[str] = None
    candidates: Tuple[EntityCandidate, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.candidates, list):
            object.__setattr__(self, "candidates", tuple(self.candidates))


def _load_canonical_directory() -> Dict[str, Tuple[str, str, str]]:
    manifest_p = Path(__file__).resolve().parent / "canonical_enterprise_directory.json"
    if manifest_p.is_file():
        try:
            raw = json.loads(manifest_p.read_text(encoding="utf-8"))
            return {k: (v[0], v[1], v[2]) for k, v in raw.items()}
        except Exception as e:
            logger.error("Failed loading canonical enterprise directory JSON: %s", e)
            raise RuntimeError(f"Fail-Fast: Failed loading canonical directory JSON: {e}") from e
    return {}


# Canonical domain priors (SSOT for known project team and executive stakeholders)
_CANONICAL_ENTERPRISE_DIRECTORY: Dict[str, Tuple[str, str, str]] = _load_canonical_directory()


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute standard Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (c1 != c2)
            curr.append(min(insertions, deletions, substitutions))
        prev = curr
    return prev[-1]


def _normalize_role_key(role: str) -> str:
    """Normalizes role strings by standardizing delimiters (/, -, _, etc.) and whitespace."""
    if not role:
        return ""
    r = str(role).lower()
    return re.sub(r"[\s/_\-–—,]+", " ", r).strip()


class PreSynthesisEntityResolver:
    """
    SSOT Pre-synthesis entity resolver adhering to Epistemic Air-Gap.
    Queries the Knowledge Graph and immutable Enterprise Directory priors and charter CSV
    to resolve attendee names, corporate affiliations, and roles.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        enable_graph_query: bool = True,
        runtime_root: Optional[Union[Path, str]] = None,
        generation_id: Optional[str] = None,
        as_of_timestamp: Optional[str] = None,
        identity_corrections_path: Optional[Union[Path, str]] = None,
    ) -> None:
        self.root = Path(repo_root).resolve() if repo_root else _REPO_ROOT
        self.enable_graph_query = enable_graph_query
        self.runtime_root = Path(runtime_root).resolve() if runtime_root else None
        self.generation_id = generation_id
        self.as_of_timestamp = as_of_timestamp
        self.identity_corrections_path = (
            Path(identity_corrections_path).resolve() if identity_corrections_path else None
        )
        self._loaded_generation_id: Optional[str] = None
        self._loaded_runtime_root: Optional[Path] = None
        self._corrections_cache: Dict[str, ResolvedEntity] = {}
        self._kg_cache: Dict[str, ResolvedEntity] = {}
        self._charter_cache: Dict[str, ResolvedEntity] = {}
        self._project_rosters: Dict[str, Dict[str, List[Any]]] = {}
        self._loaded = False

    def invalidate_cache(self) -> None:
        """Invalidates all cached KG and charter entities."""
        self._corrections_cache.clear()
        self._kg_cache.clear()
        self._charter_cache.clear()
        self._project_rosters.clear()
        self._loaded = False
        self._loaded_generation_id = None
        self._loaded_runtime_root = None

    def _load_authoritative_corrections(self) -> None:
        """Load authoritative user identity corrections taking precedence over directory priors."""
        target_path = self.identity_corrections_path
        if not target_path or not target_path.is_file():
            candidates = [
                self.root / "data/reference/prf_20261006_identity_corrections.json",
                self.root / "reports/prf_identity_corrections.json",
            ]
            target_path = next((c for c in candidates if c.is_file()), None)
        if not target_path or not target_path.is_file():
            return

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("corrections", []):
                canon_name = item.get("canonical_name") or item.get("raw_meeting_label") or ""
                org = item.get("corrected_affiliation") or item.get("candidate_affiliation") or ""
                role = item.get("role", "Participant")
                status = item.get("status", "RESOLVED")
                cand_name = item.get("candidate_canonical_name")
                ent = ResolvedEntity(
                    canonical_name=canon_name,
                    display_name=canon_name,
                    org=org,
                    role=role,
                    source="authoritative_correction" if status == "RESOLVED" else "authoritative_candidate_record",
                    provenance_id=item.get("provenance") or item.get("governance_rationale"),
                    status=status,
                    candidate_name=cand_name,
                    requires_operator_confirmation=(status == "UNRESOLVED_CANDIDATE_ONLY"),
                )
                aliases = [canon_name.lower()] + [a.lower() for a in item.get("alias_keys", [])]
                if item.get("raw_meeting_label"):
                    aliases.append(item["raw_meeting_label"].lower())
                for k in set(filter(None, aliases)):
                    self._corrections_cache[k] = ent
        except Exception as e:
            logger.warning("PreSynthesisEntityResolver: Identity corrections load failed: %s", e)

    def _get_active_graph_path(self) -> Optional[Path]:
        """Resolve active graph SQLite database from active_pair.json or repository locations."""
        from .entity_resolver_graph import resolve_active_graph_path
        return resolve_active_graph_path(self.root, self.runtime_root)

    def _load_knowledge_graph(self) -> None:
        """Query node and alias tables in active graph.sqlite."""
        db_path = self._get_active_graph_path()
        if not db_path or not db_path.is_file():
            return

        from .entity_resolver_graph import load_knowledge_graph_data
        self._loaded_runtime_root = self.runtime_root
        self._loaded_as_of_timestamp = self.as_of_timestamp
        kg_cache, gen_id = load_knowledge_graph_data(
            db_path=db_path,
            root=self.root,
            runtime_root=self.runtime_root,
            fallback_generation_id=str(self.generation_id or ""),
            entity_factory=ResolvedEntity,
            as_of_timestamp=self.as_of_timestamp,
        )
        self._project_rosters = kg_cache.pop("__project_rosters__", {})
        self._kg_cache.update(kg_cache)
        if gen_id:
            self._loaded_generation_id = gen_id

    def _load_charter_csv(self) -> None:
        """Load charter role assignments from exports/v53_nea_charter_role_assignments.csv."""
        csv_p = self.root / "exports" / "v53_nea_charter_role_assignments.csv"
        if not csv_p.is_file():
            return

        try:
            with open(csv_p, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = (row.get("Individual Name") or row.get("Role Assumed By") or "").strip()
                    if not name:
                        continue
                    clean_name = re.sub(r"\s*\(.*?\)", "", name).strip()
                    raw_org = (row.get("Vendor / Provider") or "").strip()
                    org = "PartnerAdvisory" if "partner_advisory" in raw_org.lower() else "Apex Global Enterprises (NEA)"
                    role = (row.get("Assigned Delivery Role") or "Participant").strip()

                    canon_key = clean_name.lower()
                    if canon_key in _CANONICAL_ENTERPRISE_DIRECTORY:
                        c_name, c_org, c_role = _CANONICAL_ENTERPRISE_DIRECTORY[canon_key]
                        clean_name, org = c_name, c_org or org
                    elif len(canon_key) >= 6:
                        for dir_k, (d_name, d_org, _) in _CANONICAL_ENTERPRISE_DIRECTORY.items():
                            if len(dir_k) >= 6 and _levenshtein_distance(canon_key, dir_k) == 1:
                                clean_name, org = d_name, d_org or org
                                break

                    entity = ResolvedEntity(
                        canonical_name=clean_name,
                        display_name=clean_name,
                        org=org,
                        role=role,
                        source="charter_csv",
                    )
                    self._charter_cache[clean_name.lower()] = entity
        except Exception as e:
            logger.error("PreSynthesisEntityResolver: Charter load failed: %s", e)
            raise RuntimeError(f"Fail-Fast: Failed loading charter CSV at {csv_p}: {e}") from e

    def _ensure_loaded(self) -> None:
        if self._loaded and (
            (self.runtime_root and self._loaded_runtime_root != self.runtime_root)
            or (self.generation_id and self._loaded_generation_id != self.generation_id)
            or (self.as_of_timestamp and getattr(self, "_loaded_as_of_timestamp", None) != self.as_of_timestamp)
        ):
            self.invalidate_cache()
        if not self._loaded:
            self._load_authoritative_corrections()
            if self.enable_graph_query:
                self._load_knowledge_graph()
            self._load_charter_csv()
            self._loaded = True

    def get_project_roster_entities(self, project_name: Optional[str]) -> List[ResolvedEntity]:
        """Return list of ResolvedEntity for the given project scope."""
        if not project_name:
            return []
        proj = project_name.lower().strip()
        ents: List[ResolvedEntity] = []
        seen_names: set[str] = set()
        if hasattr(self, "_project_rosters") and proj in self._project_rosters:
            for e in self._project_rosters[proj].get("all", []):
                if e.canonical_name.lower() not in seen_names:
                    seen_names.add(e.canonical_name.lower())
                    ents.append(e)

        # Merge with default project team roster to ensure full baseline coverage
        team_dict = _get_project_team_roster(self, proj)
        names = team_dict.get("partner_advisory", []) + team_dict.get("apex_global", [])
        for name in names:
            if name.lower() not in seen_names:
                ent = self._resolve_internal(name, project_name=None)
                if ent:
                    ents.append(ent)
                    seen_names.add(ent.canonical_name.lower())
                else:
                    org = "PartnerAdvisory" if name in team_dict.get("partner_advisory", []) else "Apex Global Enterprises (NEA)"
                    new_e = ResolvedEntity(
                        canonical_name=name,
                        display_name=name,
                        org=org,
                        role="Participant",
                        source="project_roster_fallback",
                    )
                    ents.append(new_e)
                    seen_names.add(name.lower())
        return ents

    def _resolve_internal(self, raw_name: str, project_name: Optional[str] = None) -> Optional[ResolvedEntity]:
        """Internal entity resolution using directory priors, KG, and charter."""
        if not raw_name or not raw_name.strip():
            return None

        self._ensure_loaded()
        clean = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "", raw_name)
        clean = re.sub(r"[*_#:`]", "", clean).strip()
        m_paren = re.match(r"^([^(]+)", clean)
        base_name = m_paren.group(1).strip() if m_paren else clean
        clean_no_parens = re.sub(r"\s*\([^)]*\)\s*", " ", clean).strip()

        candidate_keys: List[str] = []
        raw_clean = raw_name.strip().lower()
        if raw_clean:
            candidate_keys.append(raw_clean)
        for cand in (clean.lower(), clean_no_parens.lower(), base_name.lower()):
            if cand and cand not in candidate_keys:
                candidate_keys.append(cand)

        for low_key in candidate_keys:
            # 0. Check learned runtime aliases (L4 healing & system learning)
            try:
                from dual_pipeline.transcript_qa_core.alias_registry import load_learned_aliases
                learned_aliases = load_learned_aliases()
                target_alias = (
                    learned_aliases.get(low_key)
                    or learned_aliases.get(f"person:{low_key}")
                    or learned_aliases.get(f"system:{low_key}")
                    or learned_aliases.get(f"org:{low_key}")
                )
                if target_alias:
                    clean_target = target_alias.split(":", 1)[1] if ":" in target_alias else target_alias
                    clean_target = clean_target.replace("-", " ").title()
                    if clean_target.lower() != low_key and clean_target.lower() not in [c.lower() for c in candidate_keys]:
                        res = self._resolve_internal(clean_target, project_name=project_name)
                        if res:
                            return res
            except Exception:
                pass

            low_words = low_key.split()
            # 0.5. Check authoritative identity corrections (highest priority SSOT)
            if low_key in self._corrections_cache:
                cor_ent = self._corrections_cache[low_key]
                from .transcript_placeholder_detector import _is_placeholder
                if not _is_placeholder(cor_ent.role) or low_key not in self._kg_cache:
                    return cor_ent

            # 0.5. Check project-scoped roster match if project_name is provided
            if project_name:
                roster_ents = self.get_project_roster_entities(project_name)
                from .transcript_placeholder_detector import _is_placeholder

                # First check full canonical/display name match
                for r_ent in roster_ents:
                    if low_key in (r_ent.canonical_name.lower(), r_ent.display_name.lower()) and len(r_ent.canonical_name.split()) > 1:
                        eff_role = r_ent.role
                        eff_org = r_ent.org
                        dir_entry = _CANONICAL_ENTERPRISE_DIRECTORY.get(r_ent.canonical_name.lower())
                        if dir_entry:
                            c_name, org, role = dir_entry
                            if _is_placeholder(eff_role) and role and not _is_placeholder(role):
                                eff_role = role
                            if eff_org in ("Client / Partner", "Unknown") and org and org not in ("Client / Partner", "Unknown"):
                                eff_org = org
                        return replace(r_ent, org=eff_org, role=eff_role)

                # Check multi-word nickname / surname match (e.g. "Alex Corrow" matching "Alexandra Corrow")
                low_words = low_key.split()
                if len(low_words) >= 2:
                    for r_ent in roster_ents:
                        r_words = r_ent.canonical_name.lower().split()
                        if len(r_words) >= 2 and r_words[-1] == low_words[-1]:
                            f_in = (
                                low_words[0] in r_words[0]
                                or r_words[0] in low_words[0]
                                or (low_words[0] in ("alex", "alexandra") and r_words[0] in ("alex", "alexandra"))
                            )
                            if f_in:
                                eff_role = r_ent.role
                                eff_org = r_ent.org
                                dir_entry = _CANONICAL_ENTERPRISE_DIRECTORY.get(r_ent.canonical_name.lower())
                                if dir_entry:
                                    c_name, org, role = dir_entry
                                    if _is_placeholder(eff_role) and role and not _is_placeholder(role):
                                        eff_role = role
                                    if eff_org in ("Client / Partner", "Unknown") and org and org not in ("Client / Partner", "Unknown"):
                                        eff_org = org
                                return replace(r_ent, org=eff_org, role=eff_role)

                # Then check single first-name or nickname match within project scope (ONLY when low_key is single word)
                if len(low_words) == 1:
                    matching_first = [
                        r_ent for r_ent in roster_ents
                        if (
                            low_key == r_ent.canonical_name.lower().split()[0]
                            or low_key == r_ent.display_name.lower().split()[0]
                            or (len(low_key) >= 3 and r_ent.canonical_name.lower().split()[0].startswith(low_key))
                            or (len(low_key) >= 3 and low_key.startswith(r_ent.canonical_name.lower().split()[0]))
                        )
                        and len(r_ent.canonical_name.split()) > 1
                    ]
                    if len(matching_first) == 1:
                        r_ent = matching_first[0]
                        eff_role = r_ent.role
                        eff_org = r_ent.org
                        dir_entry = _CANONICAL_ENTERPRISE_DIRECTORY.get(r_ent.canonical_name.lower())
                        if dir_entry:
                            c_name, org, role = dir_entry
                            if _is_placeholder(eff_role) and role and not _is_placeholder(role):
                                eff_role = role
                            if eff_org in ("Client / Partner", "Unknown") and org and org not in ("Client / Partner", "Unknown"):
                                eff_org = org
                        return replace(
                            r_ent,
                            org=eff_org,
                            role=eff_role,
                            status="UNRESOLVED_CANDIDATE_ONLY",
                            candidate_name=r_ent.canonical_name,
                            requires_operator_confirmation=True,
                            source="project_roster_single_token_candidate",
                        )

                # Then check any exact match if no full-name entity matched
                for r_ent in roster_ents:
                    if low_key in (r_ent.canonical_name.lower(), r_ent.display_name.lower()):
                        return r_ent

            # 1. Check Knowledge Graph node table (SSOT authoritative ground truth)
            if low_key in self._kg_cache:
                kg_ent = self._kg_cache[low_key]
                if kg_ent.status in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_CANDIDATE_ONLY", "UNRESOLVED_GRAPH_ATTENDEE") or kg_ent.requires_operator_confirmation:
                    return kg_ent
                from .transcript_placeholder_detector import _is_placeholder
                eff_org = kg_ent.org
                eff_name = kg_ent.canonical_name
                eff_role = kg_ent.role if (kg_ent.role and not _is_placeholder(kg_ent.role)) else "Participant"
                # If graph entity has a placeholder role, check directory prior before settling on placeholder
                dir_entry = _CANONICAL_ENTERPRISE_DIRECTORY.get(low_key) or _CANONICAL_ENTERPRISE_DIRECTORY.get(kg_ent.canonical_name.lower())
                if dir_entry:
                    dir_c_name, dir_org, dir_role = dir_entry
                    if dir_org and ("analytics" in dir_org.lower() or "pgd" in dir_org.lower() or "apex" in dir_org.lower()):
                        eff_org = dir_org
                    if eff_role == "Participant" and dir_role and not _is_placeholder(dir_role):
                        eff_role = dir_role
                if len(low_words) == 1 and len(eff_name.split()) > 1:
                    new_status = kg_ent.status if kg_ent.status in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE") else "UNRESOLVED_CANDIDATE_ONLY"
                    return replace(
                        kg_ent,
                        org=eff_org,
                        canonical_name=eff_name,
                        display_name=eff_name,
                        role=eff_role,
                        status=new_status,
                        candidate_name=eff_name,
                        requires_operator_confirmation=True,
                        source=f"{kg_ent.source}_single_token_candidate",
                    )
                return replace(kg_ent, org=eff_org, canonical_name=eff_name, display_name=eff_name, role=eff_role)

            # 2. Check canonical enterprise directory priors (bootstrapping fallback)
            if low_key in _CANONICAL_ENTERPRISE_DIRECTORY:
                c_name, org, role = _CANONICAL_ENTERPRISE_DIRECTORY[low_key]
                is_single_to_multi = len(low_words) == 1 and len(c_name.split()) > 1
                return ResolvedEntity(
                    canonical_name=c_name,
                    display_name=c_name,
                    org=org,
                    role=role,
                    source="directory_prior" if not is_single_to_multi else "directory_prior_single_token_candidate",
                    status="RESOLVED" if not is_single_to_multi else "UNRESOLVED_CANDIDATE_ONLY",
                    candidate_name=c_name if is_single_to_multi else None,
                    requires_operator_confirmation=is_single_to_multi,
                )

            # 3. Check Charter CSV
            if low_key in self._charter_cache:
                return self._charter_cache[low_key]

        lookup_key = clean_no_parens.lower() if clean_no_parens else base_name.lower()
        # 4. Partial / token matching for full names (e.g. "Mandy H" -> "harper Hoffman", "David C." -> "David Childers")
        parts = lookup_key.split()
        if len(parts) == 2:
            initial = parts[1].rstrip(".")
            if len(initial) == 1:
                first = parts[0]
                if project_name:
                    roster_ents = self.get_project_roster_entities(project_name)
                    for r_ent in roster_ents:
                        r_parts = r_ent.canonical_name.lower().split()
                        if len(r_parts) >= 2 and r_parts[0] == first and r_parts[1].startswith(initial):
                            dir_entry = _CANONICAL_ENTERPRISE_DIRECTORY.get(r_ent.canonical_name.lower())
                            if dir_entry:
                                c_name, org, role = dir_entry
                                from .transcript_placeholder_detector import _is_placeholder
                                eff_role = r_ent.role if (r_ent.role and not _is_placeholder(r_ent.role)) else (role if (role and not _is_placeholder(role)) else r_ent.role)
                                return replace(
                                    r_ent,
                                    canonical_name=c_name,
                                    display_name=c_name,
                                    org=org or r_ent.org,
                                    role=eff_role,
                                )
                            return r_ent
                for k, ent in _CANONICAL_ENTERPRISE_DIRECTORY.items():
                    k_parts = k.split()
                    if len(k_parts) >= 2 and k_parts[0] == first and k_parts[1].startswith(initial):
                        return ResolvedEntity(
                            canonical_name=ent[0],
                            display_name=ent[0],
                            org=ent[1],
                            role=ent[2],
                            source="directory_token_match",
                        )

        # 5. Fuzzy Levenshtein distance <= 1 matching against directory keys and canonical names
        fuzzy_target = candidate_keys[0] if candidate_keys else raw_name.strip().lower()
        if len(fuzzy_target) >= 6:
            # 5a. Fuzzy Levenshtein distance <= 2 and token overlap matching against GraphDB nodes
            if self._kg_cache:
                kg_matches: List[Tuple[int, ResolvedEntity]] = []
                target_tokens = set(fuzzy_target.split())
                for kg_k, kg_ent in self._kg_cache.items():
                    c_name_low = kg_ent.canonical_name.lower()
                    dist_k = _levenshtein_distance(fuzzy_target, kg_k)
                    dist_c = _levenshtein_distance(fuzzy_target, c_name_low)
                    min_dist = min(dist_k, dist_c)

                    kg_tokens = set(kg_k.split())
                    token_overlap = len(target_tokens & kg_tokens) == len(target_tokens) and len(target_tokens) >= 2

                    if min_dist <= 2 or token_overlap:
                        kg_matches.append((min_dist, kg_ent))

                if kg_matches:
                    kg_matches.sort(key=lambda x: x[0])
                    best_dist, best_kg_ent = kg_matches[0]
                    cand_list = [
                        EntityCandidate(
                            role=ent_m.role,
                            confidence=max(0.5, 1.0 - (d * 0.2)),
                            currentness_state="current",
                            validation_state="needs_review",
                            assertion_id=ent_m.assertion_id,
                            source_node_id=ent_m.provenance_id,
                        )
                        for d, ent_m in kg_matches
                    ]
                    return ResolvedEntity(
                        canonical_name=best_kg_ent.canonical_name,
                        display_name=best_kg_ent.display_name,
                        org=best_kg_ent.org,
                        role=best_kg_ent.role,
                        source="graph_fuzzy_levenshtein_2",
                        status="UNRESOLVED_GRAPH_ATTENDEE",
                        candidate_name=best_kg_ent.canonical_name,
                        requires_operator_confirmation=True,
                        confidence=max(0.5, 1.0 - (best_dist * 0.2)),
                        candidates=tuple(cand_list),
                    )

            # 5b. Fuzzy Levenshtein distance <= 1 matching against directory keys and canonical names
            candidates: List[Tuple[str, Tuple[str, str, str]]] = []
            for dir_k, ent_tuple in _CANONICAL_ENTERPRISE_DIRECTORY.items():
                if len(dir_k) >= 6 and _levenshtein_distance(fuzzy_target, dir_k) <= 1:
                    candidates.append((dir_k, ent_tuple))
                elif len(ent_tuple[0]) >= 6 and _levenshtein_distance(fuzzy_target, ent_tuple[0].lower()) <= 1:
                    candidates.append((ent_tuple[0].lower(), ent_tuple))

            if candidates:
                unique_canonicals = {c[1][0]: c[1] for c in candidates}
                if len(unique_canonicals) == 1:
                    c_name, org, role = next(iter(unique_canonicals.values()))
                    is_single_to_multi = len(fuzzy_target.split()) == 1 and len(c_name.split()) > 1
                    return ResolvedEntity(
                        canonical_name=c_name,
                        display_name=c_name,
                        org=org,
                        role=role,
                        source="directory_fuzzy_levenshtein_1" if not is_single_to_multi else "directory_fuzzy_single_token_candidate",
                        status="RESOLVED" if not is_single_to_multi else "UNRESOLVED_CANDIDATE_ONLY",
                        candidate_name=c_name if is_single_to_multi else None,
                        requires_operator_confirmation=is_single_to_multi,
                    )

        return None

    def resolve(
        self,
        raw_name: str,
        explicit_org: Optional[str] = None,
        explicit_role: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> Optional[ResolvedEntity]:
        """Resolves raw person string into verified entity using directory priors, KG, and charter.

        Enforces field-specific input roster provenance locks when explicit_org is provided.
        Detects conflicts between explicit input role and GraphDB assertions.
        """
        ent = self._resolve_internal(raw_name, project_name=project_name)
        if not ent:
            return None

        if explicit_org and str(explicit_org).strip().lower() not in ("unknown", "tbd", "placeholder", "none"):
            exp_clean = str(explicit_org).strip()
            if ent.org != exp_clean:
                logger.info(
                    "EntityResolver: Enforcing field-specific roster affiliation lock '%s' over '%s' for '%s'",
                    exp_clean, ent.org, ent.canonical_name,
                )
                ent = replace(ent, org=exp_clean, provenance_origin="ORIGIN_INPUT_PROVENANCE")

        if explicit_role and str(explicit_role).strip().lower() not in ("unknown", "tbd", "placeholder", "none", "participant"):
            exp_role = str(explicit_role).strip()
            exp_norm = _normalize_role_key(exp_role)
            # Enforce GraphDB conflict detection and disambiguation when candidates or graph assertions exist
            if ent.candidates or "graph" in (ent.source or ""):
                cand_roles_map = {c.role.lower(): c.role for c in ent.candidates} if ent.candidates else {ent.role.lower(): ent.role}
                matched_role = cand_roles_map.get(exp_role.lower())
                if not matched_role:
                    for cr_low, cr_orig in cand_roles_map.items():
                        cr_norm = _normalize_role_key(cr_low)
                        if exp_norm == cr_norm or exp_norm in cr_norm or cr_norm in exp_norm:
                            matched_role = cr_orig
                            break
                        if exp_role.lower() in cr_low or cr_low in exp_role.lower():
                            matched_role = cr_orig
                            break
                if matched_role:
                    is_single_token_input = len(raw_name.split()) == 1
                    was_unconfirmed_candidate = (
                        is_single_token_input
                        or ent.status in ("UNRESOLVED_CANDIDATE_ONLY", "UNRESOLVED_GRAPH_ATTENDEE")
                    ) and ent.requires_operator_confirmation
                    ent = replace(
                        ent,
                        role=matched_role,
                        status=ent.status if was_unconfirmed_candidate else "RESOLVED",
                        requires_operator_confirmation=ent.requires_operator_confirmation if was_unconfirmed_candidate else False,
                        candidate_name=ent.candidate_name if was_unconfirmed_candidate else None,
                    )
                else:
                    canon_key = ent.canonical_name.strip().lower()
                    dir_ent = _CANONICAL_ENTERPRISE_DIRECTORY.get(canon_key)
                    has_verified_cand = any(str(c.validation_state or "").lower() in ("verified", "confirmed", "approved", "human_verified", "operator_verified") for c in (ent.candidates or []))
                    if dir_ent and dir_ent[2] and _normalize_role_key(dir_ent[2]) == exp_norm and not has_verified_cand:
                        ent = replace(
                            ent,
                            role=dir_ent[2],
                            status="RESOLVED",
                            requires_operator_confirmation=False,
                            candidate_name=None,
                        )
                    else:
                        logger.info(
                            "EntityResolver: Explicit role '%s' conflicts with GraphDB candidates %s for '%s'",
                            exp_role, list(cand_roles_map.keys()), ent.canonical_name,
                        )
                        ent = replace(
                            ent,
                            status="AMBIGUOUS_CONFLICT",
                            requires_operator_confirmation=True,
                            candidate_name=f"{exp_role} vs {ent.role}",
                        )

        return ent

    def resolve_attendee_list(
        self,
        raw_attendees: Union[str, List[Any]],
        project_name: Optional[str] = None,
        default_org: Optional[str] = None,
    ) -> List[ResolvedEntity]:
        """Resolves raw attendee input into deduplicated list of verified corporate entities.

        Handles team group expansions (e.g. 'PartnerAdvisory team'), first-name/nickname resolution ('Ed'),
        initial tokens ('Alex S'), and context-aware disambiguation for same-first-name cohorts.
        """
        self._ensure_loaded()
        if not raw_attendees:
            return []

        tokens = _tokenize_attendee_input(raw_attendees)
        proj = (project_name or "").lower().strip()
        all_text = " ".join(tokens).lower()
        if not proj or proj in ("none", "default"):
            if "lens" in all_text:
                proj = "project_lens"

        project_teams = _get_project_team_roster(self, proj)
        resolved_entities: List[ResolvedEntity] = []
        seen_canonical: set[str] = set()

        def _add_entity(ent: Optional[ResolvedEntity]) -> None:
            if not ent:
                return
            key = ent.canonical_name.strip().lower()
            if key not in seen_canonical:
                seen_canonical.add(key)
                resolved_entities.append(ent)

        individual_tokens: List[str] = []
        has_partner_advisory_team = False
        has_apex_global_team = False

        for tok in tokens:
            tok_low = tok.lower().strip()
            is_partner_advisory_team = bool(re.search(r"\b(?:full\s+)?partner_advisory\s+team\b", tok_low) or tok_low in ("partner_advisory team", "the partner_advisory team"))
            is_apex_global_team = bool(re.search(r"\b(?:full\s+)?(?:apex_global|nea|client)\s+team\b", tok_low) or tok_low in ("apex_global team", "nea team"))
            if is_partner_advisory_team:
                has_partner_advisory_team = True
                for m_name in project_teams.get("partner_advisory", []):
                    _add_entity(self.resolve(m_name, explicit_org="PartnerAdvisory", project_name=proj))
            elif is_apex_global_team:
                has_apex_global_team = True
                for m_name in project_teams.get("apex_global", []):
                    _add_entity(self.resolve(m_name, explicit_org="Apex Global Enterprises (NEA)", project_name=proj))
            else:
                individual_tokens.append(tok)

        partner_advisory_count = (5 if has_partner_advisory_team else 0) + sum(
            1 for t in individual_tokens if t.lower() in ("jen", "jennifer", "amit", "jake", "karyn", "alex sanchez", "partner_advisory")
        )
        apex_global_count = (3 if has_apex_global_team else 0) + sum(
            1 for t in individual_tokens if t.lower() in ("ed", "eduardo", "alex corrow", "daniel", "sabet", "nea", "apex_global")
        )

        for tok in individual_tokens:
            tok_clean = tok.strip()
            homonym = _disambiguate_homonym(
                self, tok_clean, proj, seen_canonical, has_partner_advisory_team, partner_advisory_count, apex_global_count
            )
            if homonym:
                _add_entity(homonym)
                continue

            ent = self.resolve(tok_clean, explicit_org=default_org, project_name=proj)
            if ent:
                _add_entity(ent)
            else:
                _add_entity(ResolvedEntity(
                    canonical_name=tok_clean,
                    display_name=tok_clean,
                    org=default_org or "Unknown",
                    role="Participant",
                    source="unresolved_input",
                ))

        return resolved_entities


def _tokenize_attendee_input(raw: Union[str, List[Any]]) -> List[str]:
    """Parse comma/newline/parenthetical attendee inputs into stripped tokens."""
    tokens: List[str] = []
    if isinstance(raw, str):
        clean_raw = re.sub(r"<[^>]*>", "", raw)
        m_parens = re.search(r"\(([^)]+)\)", clean_raw)
        if m_parens and any(k in clean_raw.lower() for k in ("team", "attendees", "roster")):
            inner = m_parens.group(1).strip()
            tokens.extend([p.strip() for p in re.split(r"[,;\n]+", inner) if p.strip()])
        else:
            for chunk in re.split(r"[,;\n]+", clean_raw):
                clean_chunk = re.sub(r"<[^>]*>", "", chunk).strip()
                if clean_chunk:
                    tokens.append(clean_chunk)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("name") or item.get("canonical_name") or ""
                clean_name = re.sub(r"<[^>]*>", "", name).strip()
                if clean_name:
                    tokens.append(clean_name)
            elif isinstance(item, str) and item.strip():
                clean_s = re.sub(r"<[^>]*>", "", item).strip()
                if clean_s:
                    tokens.append(clean_s)
    return tokens


DEFAULT_PROJECT_ROSTERS: Dict[str, Dict[str, List[str]]] = {
    "project_lens": {
        "partner_advisory": ["Jennifer Pan", "Amit Ayer", "Alex Sanchez", "Jake Stark", "Morgan Vance Krutilla"],
        "apex_global": ["Eduardo More", "Alex Corrow", "Daniel Sabet"],
    },
    "lens": {
        "partner_advisory": ["Jennifer Pan", "Amit Ayer", "Alex Sanchez", "Jake Stark", "Morgan Vance Krutilla"],
        "apex_global": ["Eduardo More", "Alex Corrow", "Daniel Sabet"],
    },
    "project_beacon": {
        "partner_advisory": ["Andy Kilinskis", "Lillian Le", "Speaker_Beta Tharakan", "Amit Ayer"],
        "apex_global": [
            "David Childers",
            "Speaker_Alpha Muschett",
            "Marcus Balk",
            "Brandon Que",
            "Catalina Orostegui",
            "Jeffrey Glade",
            "Egle Tamasevice",
            "Cansu Miller",
        ],
    },
    "sherpa": {
        "partner_advisory": ["Andy Kilinskis", "Lillian Le", "Speaker_Beta Tharakan", "Amit Ayer"],
        "apex_global": [
            "David Childers",
            "Speaker_Alpha Muschett",
            "Marcus Balk",
            "Brandon Que",
            "Catalina Orostegui",
            "Jeffrey Glade",
            "Egle Tamasevice",
            "Cansu Miller",
        ],
    },
}


def _get_project_team_roster(resolver: PreSynthesisEntityResolver, project: str) -> Dict[str, List[str]]:
    """Return project roster stakeholder names by organization."""
    proj = (project or "").lower().strip()
    teams: Dict[str, List[str]] = {"partner_advisory": [], "apex_global": []}
    if hasattr(resolver, "_project_rosters") and proj in resolver._project_rosters:
        db_roster = resolver._project_rosters[proj]
        if db_roster.get("partner_advisory"):
            teams["partner_advisory"] = [e.canonical_name for e in db_roster["partner_advisory"]]
        if db_roster.get("apex_global"):
            teams["apex_global"] = [e.canonical_name for e in db_roster["apex_global"]]
        if teams["partner_advisory"] or teams["apex_global"]:
            return teams
    if proj in DEFAULT_PROJECT_ROSTERS:
        return {
            "partner_advisory": list(DEFAULT_PROJECT_ROSTERS[proj].get("partner_advisory", [])),
            "apex_global": list(DEFAULT_PROJECT_ROSTERS[proj].get("apex_global", [])),
        }
    return teams


def _disambiguate_homonym(
    resolver: PreSynthesisEntityResolver,
    tok_clean: str,
    project: str,
    seen_canonical: set[str],
    has_partner_advisory_team: bool,
    partner_advisory_count: int,
    apex_global_count: int,
) -> Optional[ResolvedEntity]:
    """Resolve homonyms via project team context from GraphDB."""
    tok_low = tok_clean.lower().strip()
    proj = (project or "").lower().strip()
    if proj:
        roster_ents = resolver.get_project_roster_entities(proj)
        matching = [
            e for e in roster_ents
            if e.canonical_name.lower().split()[0] == tok_low
            and len(e.canonical_name.split()) > 1
        ]
        if len(matching) == 1:
            return resolver.resolve(matching[0].canonical_name, project_name=proj)

    if tok_low == "alex":
        alex_sanchez_present = any("alex sanchez" in s for s in seen_canonical)
        alex_corrow_present = any(any(k in s for k in ("alex corrow", "alexandra coro", "alexandra corrow")) for s in seen_canonical)

        if has_partner_advisory_team or alex_sanchez_present:
            return resolver.resolve("Alex Corrow", explicit_org="Apex Global Enterprises (NEA)", project_name=proj)
        if alex_corrow_present:
            return resolver.resolve("Alex Sanchez", explicit_org="PartnerAdvisory", project_name=proj)
        if partner_advisory_count > apex_global_count:
            return resolver.resolve("Alex Sanchez", explicit_org="PartnerAdvisory", project_name=proj)
        if apex_global_count > partner_advisory_count:
            return resolver.resolve("Alex Corrow", explicit_org="Apex Global Enterprises (NEA)", project_name=proj)
        return resolver.resolve("Alex Sanchez", explicit_org="PartnerAdvisory", project_name=proj)

    return None


# Global singleton instance
_RESOLVER = PreSynthesisEntityResolver()


def invalidate_resolver_cache() -> None:
    """Module-level invalidation of singleton resolver cache."""
    global _RESOLVER
    _RESOLVER.invalidate_cache()


def resolve_person_entity(
    name_or_alias: str,
    repo_root: Optional[Path] = None,
    runtime_root: Optional[Union[Path, str]] = None,
    generation_id: Optional[str] = None,
    as_of_timestamp: Optional[str] = None,
    identity_corrections_path: Optional[Union[Path, str]] = None,
    explicit_org: Optional[str] = None,
    explicit_role: Optional[str] = None,
    project_name: Optional[str] = None,
) -> Optional[ResolvedEntity]:
    """Public helper to resolve a person against the Knowledge Graph and directory priors."""
    if any(x is not None for x in (repo_root, runtime_root, generation_id, as_of_timestamp, identity_corrections_path)):
        return PreSynthesisEntityResolver(
            repo_root=repo_root,
            runtime_root=runtime_root,
            generation_id=generation_id,
            as_of_timestamp=as_of_timestamp,
            identity_corrections_path=identity_corrections_path,
        ).resolve(name_or_alias, explicit_org=explicit_org, explicit_role=explicit_role, project_name=project_name)
    return _RESOLVER.resolve(name_or_alias, explicit_org=explicit_org, explicit_role=explicit_role, project_name=project_name)


def resolve_company_for_person(name: str, allow_default: bool = False) -> str:
    """Resolve corporate affiliation for a person (e.g. 'PartnerAdvisory' vs 'Apex Global Enterprises (NEA)')."""
    if (ent := _RESOLVER.resolve(name)) and ent.org:
        return ent.org
    clean = re.sub(r"\s*\(.*?\)", "", name).strip()
    if clean and clean != name and (clean_ent := _RESOLVER.resolve(clean)) and clean_ent.org:
        return clean_ent.org
    low = name.lower()
    if re.search(r"\bpartner_advisory\b", low):
        return "PartnerAdvisory"
    if any(re.search(rf"\b{re.escape(k)}\b", low) for k in ("apex_global", "nea", "apex", "florida power")):
        return "Apex Global Enterprises (NEA)"
    if allow_default:
        return "Client / Partner"
    raise ValueError(f"Fail-Fast: Cannot resolve corporate affiliation for person '{name}'")


def resolve_attendee_list(
    raw_attendees: Union[str, List[Any]],
    project_name: Optional[str] = None,
    default_org: Optional[str] = None,
    resolver: Optional[PreSynthesisEntityResolver] = None,
) -> List[ResolvedEntity]:
    """Module-level helper to resolve attendee lists against GraphDB and directory priors."""
    r = resolver or _RESOLVER
    return r.resolve_attendee_list(raw_attendees, project_name=project_name, default_org=default_org)

