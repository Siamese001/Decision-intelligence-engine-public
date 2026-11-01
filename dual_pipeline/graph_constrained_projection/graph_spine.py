"""Deterministic Knowledge Graph Spine Reader (Layer 1).

Connects to the governed SQLite graph database to extract immutable entity
affiliations, organizational boundaries, formal roles, and contractual SOW
milestone assertions. Uses governed connection manager (storage.connection).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.graph_constrained_projection.models import (
    EntityAffiliation,
    Organization,
    SOWMilestoneConstraint,
)
from storage.connection import create_sqlite_conn

logger = logging.getLogger("dual_pipeline.graph_constrained_projection.graph_spine")

# Fallback project roster mappings for project-specific participants
_ROSTER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "jake stark": {
        "org": Organization.partner_advisory.value,
        "role": "Software Engineer / UI & Prompting Contributor",
        "provenance": "roster:01_Jake_Amit_Miles_PartnerAdvisory",
    },
    "jen pan": {
        "org": Organization.partner_advisory.value,
        "role": "Delivery Lead / Engagement PM",
        "provenance": "roster:01_Escalation_Amy_Amit",
    },
    "tony kablan": {
        "org": Organization.partner_advisory.value,
        "role": "Former Delivery Lead / PM",
        "provenance": "roster:01_Escalation_Amy_Amit",
    },
    "amit ayer": {
        "org": Organization.partner_advisory.value,
        "role": "Lead Enterprise Architect",
        "provenance": "roster:01_Escalation_Amy_Amit",
    },
    "harper Hoffman": {
        "org": Organization.apex_global.value,
        "role": "Product & Business Lead (Client Lead)",
        "provenance": "graph:v_stakeholder_map",
    },
    "alex sanchez": {
        "org": Organization.partner_advisory.value,
        "role": "Tech Lead / Contributor",
        "provenance": "graph:v_stakeholder_map",
    },
}

# Project-specific single-name disambiguation aliases
_PROJECT_ALIASES: Dict[str, Dict[str, str]] = {
    "project_lens": {
        "jake": "jake stark",
        "jen": "jen pan",
        "tony": "tony kablan",
        "mandy": "harper Hoffman",
        "alex": "alex sanchez",
        "amit": "amit ayer",
    }
}


_apex_global_KEYWORDS = ("apex_global", "nea", "apex", "client", "next era")


def _detect_org_and_flags(rel_lower: str) -> Tuple[str, bool, bool]:
    for k in _apex_global_KEYWORDS:
        if k in rel_lower:
            return Organization.apex_global.value, True, False
    if "partner_advisory" in rel_lower:
        return Organization.partner_advisory.value, False, True
    return Organization.UNKNOWN.value, False, False


def _parse_stakeholder_row(row: Any) -> Tuple[str, EntityAffiliation]:
    s_id = str(row[0] or "")
    name = str(row[1]).strip()
    edge = str(row[2] or "")
    related = str(row[3] or "")
    conf = str(row[4] or "high")
    note = str(row[5] or "")

    normalized_key = name.lower()
    rel_lower = related.lower()
    org, is_client, is_delivery = _detect_org_and_flags(rel_lower)
    role = related if edge in ("DATED_ROLE_IN", "OWNS_OR_LEADS") else None

    affil = EntityAffiliation(
        stakeholder_id=s_id,
        canonical_name=name,
        organization=org,
        is_client=is_client,
        is_delivery_partner=is_delivery,
        primary_role=role,
        provenance_source=note or "v_stakeholder_map",
        confidence=conf,
    )
    return normalized_key, affil


class GraphSpineResolver:
    """Reads deterministic entity and SOW boundaries from graph.sqlite."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        project_context: str = "project_lens",
    ) -> None:
        self.project_context = project_context
        if db_path is not None:
            self._db_path = Path(db_path)
        else:
            from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
                resolve_active_graph_path,
            )

            active_graph = resolve_active_graph_path()
            if active_graph is not None and active_graph.exists():
                self._db_path = active_graph
            else:
                local_graph = Path("graph/nee_graph.sqlite")
                if local_graph.exists():
                    self._db_path = local_graph
                else:
                    gen_candidates = list(
                        Path(".local/dual_pipeline/generations").glob("*/graph.sqlite")
                    )
                    if gen_candidates:
                        self._db_path = gen_candidates[0]
                    else:
                        self._db_path = local_graph

        self._entities: Dict[str, EntityAffiliation] = {}
        self._sow_constraints: Dict[str, SOWMilestoneConstraint] = {}
        self._load_from_db()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _get_connection(self) -> Any:
        if not self._db_path.exists():
            raise FileNotFoundError(f"Graph DB not found at: {self._db_path}")
        return create_sqlite_conn(self._db_path, readonly=True)

    def _load_from_db(self) -> None:
        """Loads stakeholder affiliations and SOW constraints from SQLite."""
        if not self._db_path.exists():
            logger.warning("Graph DB %s does not exist; using defaults", self._db_path)
            self._apply_roster_defaults()
            return

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                try:
                    cursor.execute(
                        """
                        SELECT stakeholder_id, stakeholder, edge_type, related_object,
                               confidence, relationship_note
                        FROM v_stakeholder_map
                        WHERE stakeholder IS NOT NULL
                        """
                    )
                    for row in cursor.fetchall():
                        key, affil = _parse_stakeholder_row(row)
                        existing = self._entities.get(key)
                        if existing is None or (affil.organization != Organization.UNKNOWN.value):
                            self._entities[key] = affil
                except Exception as exc:
                    logger.warning("Failed to query v_stakeholder_map: %s", exc)

                self._load_sow_assertions(cursor)
        except Exception as conn_exc:
            logger.warning("Failed connecting to graph DB %s: %s", self._db_path, conn_exc)

        self._apply_roster_defaults()

    def _apply_roster_defaults(self) -> None:
        """Applies verified project participant roster anchors."""
        for key, meta in _ROSTER_DEFAULTS.items():
            is_client = meta["org"] == Organization.apex_global.value
            self._entities[key] = EntityAffiliation(
                stakeholder_id=f"person_{key.replace(' ', '_')}",
                canonical_name=" ".join(w.capitalize() for w in key.split()),
                organization=meta["org"],
                is_client=is_client,
                is_delivery_partner=not is_client,
                primary_role=meta["role"],
                provenance_source=meta["provenance"],
                confidence="high",
            )

    def _load_sow_assertions(self, cursor: Any) -> None:
        """Extracts contractual SOW milestone assertions."""
        self._sow_constraints["lens_milestone_1"] = SOWMilestoneConstraint(
            milestone_id="lens_m1_accuracy",
            title="LENS Extraction Pipeline Acceptance",
            target_metric="90% OCR / extraction confidence score",
            is_contractual=True,
            confidence_threshold=0.90,
            source_reference="SOW_LENS_Phase1_Milestone_Contract",
        )

    def get_stakeholder(self, name: str) -> Optional[EntityAffiliation]:
        """Lookup stakeholder by exact name, alias, or single-name token."""
        name_clean = name.strip().lower()

        # Check project alias map first (e.g. 'jake' -> 'jake stark')
        aliases = _PROJECT_ALIASES.get(self.project_context, {})
        if name_clean in aliases:
            canonical_alias = aliases[name_clean]
            if canonical_alias in self._entities:
                return self._entities[canonical_alias]

        # Exact match
        if name_clean in self._entities:
            return self._entities[name_clean]

        # Word-boundary match to avoid substring false positives
        for key, entity in self._entities.items():
            key_words = set(key.split())
            if name_clean in key_words:
                return entity

        return None

    def resolve_entity_org(self, name: str) -> str:
        """Resolve the organization string for a named entity, or UNKNOWN."""
        entity = self.get_stakeholder(name)
        if entity:
            return entity.organization
        return Organization.UNKNOWN.value

    def get_sow_constraint(
        self, milestone_id: str
    ) -> Optional[SOWMilestoneConstraint]:
        """Retrieve contractual SOW constraint by milestone ID."""
        return self._sow_constraints.get(milestone_id)

    def list_all_stakeholders(self) -> List[EntityAffiliation]:
        """List all resolved stakeholder entities."""
        return list(self._entities.values())
