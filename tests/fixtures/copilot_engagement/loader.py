"""
tests/fixtures/copilot_engagement/loader.py

Typed loader, verification, and graph topology builder for frozen Copilot
engagement incident fixtures and negative controls.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

PACKAGE_DIR = Path(__file__).resolve().parent

FROZEN_DIGESTS: Dict[str, str] = {
    "incident_answers_snapshot.jsonl": "ab7ac5e3c3bfbc6549be0840ec659dfa6be339360a6143d9088a723d917dcfe8",
    "session_snapshot.json": "f5d4e1e3e9e20afb94429a910b44c3d3143d3c0fbeb3c1c197ee666c03b1b215",
    "evidence_manifest.json": "616362ce92a172a65144be2e1bbfd08de4f19a17653c6b62dbcf9731a43a63d3",
}


@dataclass
class CopilotProvenanceRecord:
    """Provenance record binding request, session, engagement, assertions, and displayed bytes."""

    record_id: str
    input_request: str
    selected_session_id: str
    engagement_id: str
    source_assertion_ids: List[str]
    displayed_answer: str
    answer_sha256: str
    route_identity: str
    missing_historical_binding: bool = False
    unresolved_attribution_note: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_fixture_dir() -> Path:
    return PACKAGE_DIR


def compute_sha256(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def compute_answer_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_incident_answers() -> List[Dict[str, Any]]:
    p = PACKAGE_DIR / "incident_answers_snapshot.jsonl"
    answers: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            answers.append(json.loads(line))
    return answers


def load_session_snapshot() -> Dict[str, Any]:
    p = PACKAGE_DIR / "session_snapshot.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_evidence_manifest() -> Dict[str, Any]:
    p = PACKAGE_DIR / "evidence_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_engagement_fixtures() -> Dict[str, Any]:
    p = PACKAGE_DIR / "engagement_fixtures.json"
    return json.loads(p.read_text(encoding="utf-8"))


def verify_fixture_digests() -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for filename, expected_digest in FROZEN_DIGESTS.items():
        p = PACKAGE_DIR / filename
        if not p.is_file():
            results[filename] = {"status": "MISSING", "error": "File not found"}
            continue
        actual = compute_sha256(p.read_bytes())
        if actual != expected_digest:
            results[filename] = {
                "status": "MISMATCH",
                "expected": expected_digest,
                "actual": actual,
            }
        else:
            results[filename] = {"status": "MATCH", "digest": actual}
    return results


def build_in_memory_graph() -> Any:
    fixtures = load_engagement_fixtures()
    iso = fixtures["isolated_graph"]
    return SimpleNamespace(
        nodes=iso["nodes"],
        assertions=iso["assertions"],
        forward_adjacency=iso.get("forward_adjacency", {}),
    )


def provision_isolated_sqlite_graph(db_path: Path) -> sqlite3.Connection:
    fixtures = load_engagement_fixtures()
    iso = fixtures["isolated_graph"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                engagement_id TEXT,
                aliases_json TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assertions (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                project_scope TEXT NOT NULL,
                engagement_id TEXT NOT NULL
            );
            """
        )
        for nid, ndata in iso["nodes"].items():
            aliases = json.dumps(ndata.get("aliases", []))
            conn.execute(
                "INSERT OR REPLACE INTO knowledge_nodes VALUES (?, ?, ?, ?, ?);",
                (
                    nid,
                    ndata["canonical_name"],
                    ndata["node_type"],
                    ndata.get("engagement_id", ""),
                    aliases,
                ),
            )
        for asrt_id, adata in iso["assertions"].items():
            conn.execute(
                "INSERT OR REPLACE INTO assertions VALUES (?, ?, ?, ?, ?, ?, ?);",
                (
                    asrt_id,
                    adata["subject_node_id"],
                    adata["predicate"],
                    adata["object_node_id"],
                    adata["claim_text"],
                    adata["project_scope"],
                    adata.get("engagement_id", adata["project_scope"]),
                ),
            )
    return conn


def create_provenance_record(
    input_request: str,
    selected_session_id: str,
    engagement_id: str,
    source_assertion_ids: List[str],
    displayed_answer: str,
    route_identity: str,
    missing_historical_binding: bool = False,
    unresolved_attribution_note: Optional[str] = None,
) -> CopilotProvenanceRecord:
    answer_hash = compute_answer_sha256(displayed_answer)
    sig_raw = f"{input_request}:{selected_session_id}:{engagement_id}:{answer_hash}"
    rec_id = f"prov_{compute_sha256(sig_raw)[:16]}"
    return CopilotProvenanceRecord(
        record_id=rec_id,
        input_request=input_request,
        selected_session_id=selected_session_id,
        engagement_id=engagement_id,
        source_assertion_ids=source_assertion_ids,
        displayed_answer=displayed_answer,
        answer_sha256=answer_hash,
        route_identity=route_identity,
        missing_historical_binding=missing_historical_binding,
        unresolved_attribution_note=unresolved_attribution_note,
    )


def test_copilot_engagement_fixtures_integrity() -> None:
    """Verify frozen fixture existence and SHA256 integrity."""
    for filename, expected_digest in FROZEN_DIGESTS.items():
        filepath = PACKAGE_DIR / filename
        assert filepath.is_file(), f"Fixture {filename} missing"
        actual = compute_sha256(filepath.read_text(encoding="utf-8"))
        assert actual == expected_digest, f"Digest mismatch for {filename}: {actual} != {expected_digest}"


def load_multi_tenant_fixtures() -> Dict[str, Any]:
    """Loads the frozen multi-tenant graph fixtures."""
    p = PACKAGE_DIR / "multi_tenant_fixtures.json"
    return json.loads(p.read_text(encoding="utf-8"))


def provision_multi_tenant_sqlite_graph(db_path: Path) -> sqlite3.Connection:
    """Provisions a SQLite database populated with the frozen multi-tenant graph fixtures."""
    fixtures = load_multi_tenant_fixtures()
    graph_data = fixtures["multi_tenant_graph"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                display_name TEXT,
                node_type TEXT,
                description TEXT,
                status TEXT,
                active INTEGER,
                engagement_id TEXT,
                project_scope TEXT,
                namespace TEXT,
                is_quarantined INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT,
                retrieval_eligible INTEGER,
                materiality TEXT,
                classification TEXT,
                allowed_audiences_json TEXT,
                engagement_id TEXT,
                project_scope TEXT,
                namespace TEXT,
                is_quarantined INTEGER DEFAULT 0,
                observed_at TEXT,
                source_date_normalized TEXT,
                primary_source_id TEXT
            );
            CREATE TABLE IF NOT EXISTS source (
                source_id TEXT PRIMARY KEY,
                title TEXT,
                source_date_normalized TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence_item (
                evidence_id TEXT PRIMARY KEY,
                source_id TEXT,
                locator TEXT,
                content TEXT
            );
            CREATE TABLE IF NOT EXISTS assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT
            );
            CREATE VIEW IF NOT EXISTS v_canonical_edge AS
            SELECT assertion_id, subject_node_id AS from_node_id, predicate AS relationship_family,
                   predicate AS edge_type, object_node_id AS to_node_id, materiality, primary_source_id AS source_id,
                   0 AS hitl_required
            FROM assertion WHERE object_node_id IS NOT NULL;
            """
        )
        conn.execute("INSERT OR REPLACE INTO source VALUES ('s_fixture', 'Architecture Baseline', '10/01/2026');")
        for nid, ndata in graph_data["nodes"].items():
            conn.execute(
                """INSERT OR REPLACE INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
                    nid,
                    ndata.get("canonical_name", ""),
                    ndata.get("display_name", ndata.get("canonical_name", "")),
                    ndata.get("node_type", "entity"),
                    ndata.get("description", ""),
                    "active",
                    1,
                    ndata.get("engagement_id", ""),
                    ndata.get("project_scope", ndata.get("engagement_id", "")),
                    ndata.get("namespace", "CANONICAL"),
                    ndata.get("is_quarantined", 0),
                ),
            )
        for asrt_id, adata in graph_data["assertions"].items():
            conn.execute(
                """INSERT OR REPLACE INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
                    asrt_id,
                    adata.get("subject_node_id", ""),
                    adata.get("predicate", ""),
                    adata.get("object_node_id"),
                    adata.get("claim_text", ""),
                    adata.get("canonical_status", "ACTIVE"),
                    adata.get("retrieval_eligible", 1),
                    adata.get("materiality", "high"),
                    adata.get("classification", "internal"),
                    "[]",
                    adata.get("engagement_id", ""),
                    adata.get("project_scope", adata.get("engagement_id", "")),
                    adata.get("namespace", "CANONICAL"),
                    adata.get("is_quarantined", 0),
                    "10/01/2026",
                    "10/01/2026",
                    "s_fixture",
                ),
            )
            ev_id = f"ev_{asrt_id}"
            conn.execute(
                "INSERT OR REPLACE INTO evidence_item VALUES (?, 's_fixture', 'sec_1', ?);",
                (ev_id, adata.get("claim_text", "")),
            )
            conn.execute(
                "INSERT OR REPLACE INTO assertion_evidence VALUES (?, ?, 'SUPPORTS');",
                (asrt_id, ev_id),
            )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    return conn





