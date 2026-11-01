"""
tests/fixtures/high_fidelity_graph.py

High-Fidelity SQLite Graph and Runtime Fixture Generator.
Enforces Phase 2 CI Testing Rigor: Outlaws synthetic mocks and stubs.
Provisions authentic SQLite databases with schema-conforming node, assertion,
evidence, and canonical edge views, accompanied by verified active_pair.json metadata.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple


def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_high_fidelity_sqlite_graph(
    db_path: Path,
    runtime_dir: Path | None = None,
    generation_id: str = "generation-hf-20261009",
) -> Tuple[Path, str, Path]:
    """
    Creates an authentic SQLite graph database and active_pair.json manifest.
    Guarantees 100% schema fidelity with InMemoryGraphTopology and CopilotGraphRetriever.
    """
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1. Enable WAL mode and configure table schemas
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA foreign_keys = ON;")

    cur.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            assertion_type TEXT DEFAULT 'fact',
            atomicity_state TEXT DEFAULT 'atomic',
            canonical_status TEXT DEFAULT 'approved',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality TEXT DEFAULT 'high',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting", "nee-engineering"]',
            hitl_required INTEGER DEFAULT 0,
            validation_priority TEXT,
            validation_owner TEXT,
            origin_type TEXT DEFAULT 'production_intake',
            extraction_run_id TEXT,
            primary_source_id TEXT,
            scope_text TEXT,
            engagement_id TEXT,
            project_scope TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            title TEXT,
            source_date_normalized TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            transcript_timestamp TEXT,
            direct_quote TEXT,
            FOREIGN KEY (source_id) REFERENCES source(source_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT NOT NULL DEFAULT 'supports',
            PRIMARY KEY (assertion_id, evidence_id),
            FOREIGN KEY (assertion_id) REFERENCES assertion(assertion_id),
            FOREIGN KEY (evidence_id) REFERENCES evidence_item(evidence_id)
        )
        """
    )

    cur.execute(
        """
        CREATE VIEW v_canonical_edge AS
        SELECT a.assertion_id,
               a.subject_node_id AS from_node_id,
               'RELATES_TO' AS relationship_family,
               a.predicate AS edge_type,
               a.object_node_id AS to_node_id,
               a.materiality,
               a.primary_source_id AS source_id,
               a.hitl_required
          FROM assertion a
         WHERE a.object_node_id IS NOT NULL
        """
    )

    # 2. Insert authentic nodes
    nodes_data = [
        ("project:project_jarvis", "Project Jarvis", "Jarvis Core", "project", "Live AI Copilot and Executive Memory", 1),
        ("project:project_nexus", "Project Nexus", "Nexus Integration", "project", "Predecessor ERP and Infrastructure Integration", 1),
        ("project:project_unrelated", "Project External", "External Initiative", "project", "Isolated Lateral Tenant Project", 1),
        ("person:daniel_brakin", "Daniel Brakin", "Daniel Brakin", "person", "Principal Engineer & SAP Lead", 1),
        ("person:sharon_perez", "Sharon Perez", "Sharon Perez", "person", "Enterprise Architect", 1),
        ("person:mandy_hopmann", "harper Hoffman", "harper Hoffman", "person", "VP Operations", 1),
        ("system:sap_erp", "SAP ERP", "SAP ECC/S4", "system", "Core Financial and Enterprise System", 1),
        ("system:lone_star_ems", "Lone Star EMS", "Lone Star EMS", "system", "Energy Management System", 1),
        ("system:unrelated_crm", "Unrelated CRM", "External CRM", "system", "Third-party customer database", 1),
    ]
    cur.executemany(
        "INSERT INTO node (node_id, canonical_name, display_name, node_type, description, active) VALUES (?, ?, ?, ?, ?, ?)",
        nodes_data,
    )

    # 3. Insert sources
    sources_data = [
        ("src-nexus-01", "Project Nexus Architecture Charter", "10/15/2025"),
        ("src-jarvis-01", "Project Jarvis Kickoff and Scope Specification", "02/01/2026"),
        ("src-unrelated-01", "External Client Review", "01/10/2026"),
    ]
    cur.executemany(
        "INSERT INTO source (source_id, title, source_date_normalized) VALUES (?, ?, ?)",
        sources_data,
    )

    # 4. Insert evidence items
    evidence_data = [
        ("ev-01", "src-nexus-01", "00:14:22", "Daniel Brakin is the designated technical owner for SAP ERP across all workstreams."),
        ("ev-02", "src-jarvis-01", "00:03:45", "Project Jarvis inherits operational dependency on Project Nexus architecture and SAP bridges."),
        ("ev-03", "src-nexus-01", "00:25:10", "harper Hoffman oversees the Lone Star EMS pilot deployment in Q4."),
        ("ev-04", "src-unrelated-01", "00:05:00", "Confidential tenant customer data stored in Unrelated CRM."),
    ]
    cur.executemany(
        "INSERT INTO evidence_item (evidence_id, source_id, transcript_timestamp, direct_quote) VALUES (?, ?, ?, ?)",
        evidence_data,
    )

    # 5. Insert assertions and canonical relationships
    assertions_data = [
        # Upstream dependency: Jarvis depends on Nexus (Predecessor)
        (
            "ast-jarvis-nexus-dep",
            "project:project_jarvis",
            "DEPENDS_ON",
            "project:project_nexus",
            "Project Jarvis inherits architecture and depends on Project Nexus",
            "critical",
            "internal",
            '["partner_advisory_consulting", "nee-engineering"]',
            "src-jarvis-01",
            "project:project_jarvis",
            "project:project_jarvis",
        ),
        (
            "ast-jarvis-predecessor",
            "project:project_jarvis",
            "PREDECESSOR_PROJECT",
            "project:project_nexus",
            "Project Nexus is the validated predecessor project for Jarvis",
            "high",
            "internal",
            '["partner_advisory_consulting", "nee-engineering"]',
            "src-jarvis-01",
            "project:project_jarvis",
            "project:project_jarvis",
        ),
        # Predecessor assertion: Daniel Brakin owns SAP ERP
        (
            "ast-daniel-sap-owner",
            "person:daniel_brakin",
            "OWNS_OR_LEADS",
            "system:sap_erp",
            "Daniel Brakin is the technical owner and architecture lead for SAP ERP",
            "critical",
            "internal",
            '["partner_advisory_consulting", "nee-engineering"]',
            "src-nexus-01",
            "project:project_nexus",
            "project:project_nexus",
        ),
        # Nexus contains SAP
        (
            "ast-nexus-sap-contains",
            "project:project_nexus",
            "USES_PATTERN",
            "system:sap_erp",
            "Project Nexus integrates SAP ERP into enterprise workflows",
            "high",
            "internal",
            '["partner_advisory_consulting", "nee-engineering"]',
            "src-nexus-01",
            "project:project_nexus",
            "project:project_nexus",
        ),
        # Nexus owns Lone Star EMS
        (
            "ast-mandy-ems-owner",
            "person:mandy_hopmann",
            "OWNS_OR_LEADS",
            "system:lone_star_ems",
            "harper Hoffman leads operations for Lone Star EMS",
            "high",
            "internal",
            '["partner_advisory_consulting", "nee-engineering"]',
            "src-nexus-01",
            "project:project_nexus",
            "project:project_nexus",
        ),
        # Unrelated lateral tenant assertion (Rule C-19 boundary isolation test)
        (
            "ast-unrelated-crm",
            "project:project_unrelated",
            "CONTAINS",
            "system:unrelated_crm",
            "Confidential third-party tenant data",
            "medium",
            "restricted",
            '["external_client_only"]',
            "src-unrelated-01",
            "project:project_unrelated",
            "project:project_unrelated",
        ),
    ]

    cur.executemany(
        """
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            materiality, classification, allowed_audiences_json, primary_source_id,
            scope_text, project_scope, engagement_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                a[0], a[1], a[2], a[3], a[4],
                a[5], a[6], a[7], a[8],
                a[9], a[10], a[11]
            )
            for a in [
                (*d[:11], d[10]) for d in assertions_data
            ]
        ],
    )

    # 6. Insert assertion evidence bindings
    evidence_links = [
        ("ast-jarvis-nexus-dep", "ev-02", "supports"),
        ("ast-jarvis-predecessor", "ev-02", "supports"),
        ("ast-daniel-sap-owner", "ev-01", "supports"),
        ("ast-mandy-ems-owner", "ev-03", "supports"),
        ("ast-unrelated-crm", "ev-04", "supports"),
    ]
    cur.executemany(
        "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship) VALUES (?, ?, ?)",
        evidence_links,
    )

    conn.commit()
    conn.close()

    # Calculate real SHA256 of the generated SQLite database
    db_sha256 = compute_sha256(db_path)

    # 7. Write active_pair.json manifest
    if runtime_dir is None:
        runtime_dir = db_path.parent
    else:
        runtime_dir = Path(runtime_dir).resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)

    active_pair_path = runtime_dir / "active_pair.json"
    rel_graph_path = db_path.name if db_path.parent == runtime_dir else str(db_path)

    manifest = {
        "generation_id": generation_id,
        "generation": generation_id,
        "graph_path": rel_graph_path,
        "graph_sha256": db_sha256,
        "verified_at": "2026-10-09T12:00:00Z",
        "provenance_signature": "sha256_verified_governed_fixture",
    }
    active_pair_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return db_path, db_sha256, active_pair_path
