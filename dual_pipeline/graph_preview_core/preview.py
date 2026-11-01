"""
dual_pipeline/graph_preview_core/preview.py

Generates dry-run graph delta preview artifacts (graph_delta_preview.json and graph_delta_preview.md).
Evaluates proposed entity, assertion, and relationship changes against the active GraphDB without mutating SQLite or vector indices.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.node_type_policy import NON_CREATABLE_NODE_TYPES
from storage.connection import create_sqlite_conn

logger = logging.getLogger(__name__)


def _extract_entities(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract proposed entity records from Output 03 payload."""
    if "entity_upserts" in data and isinstance(data["entity_upserts"], list):
        return data["entity_upserts"]
    if "entities" in data and isinstance(data["entities"], list):
        return data["entities"]
    return []


def _extract_assertions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract proposed assertion records from Output 03 payload."""
    assertions: List[Dict[str, Any]] = []
    if "assertions" in data and isinstance(data["assertions"], list):
        assertions.extend(data["assertions"])

    for k in ("action_updates", "decision_updates", "risk_updates", "proposal_updates", "strategic_intent_signals"):
        if k in data and isinstance(data[k], list):
            for item in data[k]:
                if isinstance(item, dict):
                    copied = dict(item)
                    copied.setdefault("item_type", k.split("_")[0])
                    assertions.append(copied)

    return assertions


def _extract_relationships(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract proposed relationship records from Output 03 payload."""
    if "relationship_signals" in data and isinstance(data["relationship_signals"], list):
        return data["relationship_signals"]
    if "relationships" in data and isinstance(data["relationships"], list):
        return data["relationships"]
    return []


def _check_table_exists(conn: Any, table_name: str) -> bool:
    res = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return res is not None


def _classify_existing_node(
    ent_id: str,
    name: str,
    ent_type: str,
    desc: str,
    conn: Any,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT canonical_name, description, node_type FROM node WHERE node_id = ?",
        (ent_id,),
    ).fetchone()
    if row is None:
        return {"id": ent_id, "name": name, "type": ent_type, "description": desc, "status": "added"}
    existing_name = str(row["canonical_name"] or "")
    existing_desc = str(row["description"] or "")
    if (name and name != existing_name) or (desc and desc != existing_desc):
        return {"id": ent_id, "name": name, "prior_name": existing_name, "type": ent_type, "status": "modified"}
    return {"id": ent_id, "name": name, "type": ent_type, "status": "unchanged"}


def _diff_single_node(
    ent: Dict[str, Any],
    conn: Optional[Any],
    has_node_table: bool,
) -> Tuple[Dict[str, Any], Optional[str]]:
    ent_id = str(ent.get("entity_id") or ent.get("id") or "").strip()
    name = str(ent.get("name") or ent.get("canonical_name") or ent_id).strip()
    ent_type = str(ent.get("entity_type") or ent.get("type") or "").strip()
    if not ent_type and ":" in ent_id:
        ent_type = ent_id.split(":", 1)[0]
    desc = str(ent.get("description") or "").strip()

    violation = None
    if ent_type in NON_CREATABLE_NODE_TYPES:
        violation = f"Entity '{ent_id}' has prohibited non-creatable node type '{ent_type}'."

    if has_node_table and conn is not None and ent_id:
        classified = _classify_existing_node(ent_id, name, ent_type, desc, conn)
    else:
        classified = {"id": ent_id, "name": name, "type": ent_type, "description": desc, "status": "added"}
    return classified, violation


def _diff_nodes(
    raw_entities: List[Dict[str, Any]],
    conn: Optional[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Partition entities into additions, modifications, unchanged, and violations."""
    additions: List[Dict[str, Any]] = []
    modifications: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    violations: List[str] = []

    has_node_table = conn is not None and _check_table_exists(conn, "node")
    for ent in raw_entities:
        classified, viol = _diff_single_node(ent, conn, has_node_table)
        if viol:
            violations.append(viol)
        st = classified["status"]
        if st == "added":
            additions.append(classified)
        elif st == "modified":
            modifications.append(classified)
        else:
            unchanged.append(classified)

    return additions, modifications, unchanged, violations


def _diff_single_assertion(
    ast: Dict[str, Any],
    conn: Optional[Any],
    has_ast_table: bool,
) -> Tuple[str, Dict[str, Any]]:
    ast_id = str(
        ast.get("assertion_id") or ast.get("action_id") or ast.get("decision_id")
        or ast.get("proposal_id") or ast.get("risk_id") or ast.get("signal_id") or ast.get("id") or ""
    ).strip()
    claim = str(ast.get("claim_text") or ast.get("description") or ast.get("text") or "").strip()
    predicate = str(ast.get("predicate") or ast.get("item_type") or "CLAIM").strip()
    mut = str(ast.get("mutation") or "").upper()
    ast_status = str(ast.get("status") or "").lower()

    if mut in ("RETIRE", "SUPERSEDE") or ast_status in ("superseded", "retired", "closed"):
        return "superseded", {"id": ast_id, "claim_text": claim, "predicate": predicate, "status": "superseded"}

    if has_ast_table and conn is not None and ast_id:
        row = conn.execute("SELECT assertion_id FROM assertion WHERE assertion_id = ?", (ast_id,)).fetchone()
        if row is not None:
            return "unchanged", {"id": ast_id, "claim_text": claim, "predicate": predicate, "status": "unchanged"}

    return "new", {"id": ast_id, "claim_text": claim, "predicate": predicate, "status": "new"}


def _diff_assertions(
    raw_assertions: List[Dict[str, Any]],
    conn: Optional[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition assertions into new, superseded, and unchanged."""
    new_ast: List[Dict[str, Any]] = []
    superseded_ast: List[Dict[str, Any]] = []
    unchanged_ast: List[Dict[str, Any]] = []

    has_ast_table = conn is not None and _check_table_exists(conn, "assertion")
    for ast in raw_assertions:
        category, record = _diff_single_assertion(ast, conn, has_ast_table)
        if category == "superseded":
            superseded_ast.append(record)
        elif category == "unchanged":
            unchanged_ast.append(record)
        else:
            new_ast.append(record)

    return new_ast, superseded_ast, unchanged_ast


def _diff_relationships(
    raw_relationships: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition relationships into new edges and retired edges."""
    new_edges: List[Dict[str, Any]] = []
    retired_edges: List[Dict[str, Any]] = []

    for rel in raw_relationships:
        from_id = str(rel.get("from_id") or rel.get("source") or rel.get("from_node_id") or "").strip()
        to_id = str(rel.get("to_id") or rel.get("target") or rel.get("to_node_id") or "").strip()
        edge_type = str(rel.get("relationship") or rel.get("edge_type") or rel.get("predicate") or "RELATED_TO").strip()
        mut = str(rel.get("mutation") or "").upper()
        r_status = str(rel.get("status") or "").lower()

        if mut in ("RETIRE", "DELETE") or r_status in ("retired", "deleted"):
            retired_edges.append({"from_id": from_id, "to_id": to_id, "relationship": edge_type, "status": "retired"})
        else:
            new_edges.append({"from_id": from_id, "to_id": to_id, "relationship": edge_type, "status": "new"})

    return new_edges, retired_edges


def _render_node_diff_section(node_diff: Dict[str, Any]) -> List[str]:
    lines = ["## 2. Node Diff Preview", ""]
    if node_diff["additions"]:
        lines.extend(["### Proposed New Nodes", "| Node ID | Type | Canonical Name |", "| :--- | :--- | :--- |"])
        for n in node_diff["additions"][:25]:
            lines.append(f"| `{n['id']}` | `{n['type']}` | {n['name']} |")
        if len(node_diff["additions"]) > 25:
            lines.append(f"| ... and {len(node_diff['additions']) - 25} more nodes | | |")
        lines.append("")
    if node_diff["modifications"]:
        lines.extend(["### Proposed Node Modifications", "| Node ID | Type | Prior Name | New Name |", "| :--- | :--- | :--- | :--- |"])
        for m_item in node_diff["modifications"][:25]:
            lines.append(f"| `{m_item['id']}` | `{m_item['type']}` | {m_item.get('prior_name', '')} | {m_item['name']} |")
        lines.append("")
    return lines


def _render_assertion_diff_section(assertion_diff: Dict[str, Any]) -> List[str]:
    lines = ["## 3. Assertion & Claim Diff Preview", ""]
    if assertion_diff["new_assertions"]:
        lines.extend(["### New Assertions", "| Assertion ID | Predicate / Type | Summary Claim |", "| :--- | :--- | :--- |"])
        for a in assertion_diff["new_assertions"][:25]:
            snip = (a["claim_text"][:80] + "...") if len(a["claim_text"]) > 80 else a["claim_text"]
            lines.append(f"| `{a['id']}` | `{a['predicate']}` | {snip} |")
        lines.append("")
    if assertion_diff["superseded_assertions"]:
        lines.extend(["### Superseded / Retired Assertions", "| Assertion ID | Predicate | Claim Text |", "| :--- | :--- | :--- |"])
        for s in assertion_diff["superseded_assertions"][:25]:
            snip = (s["claim_text"][:80] + "...") if len(s["claim_text"]) > 80 else s["claim_text"]
            lines.append(f"| `{s['id']}` | `{s['predicate']}` | {snip} |")
        lines.append("")
    return lines


def _render_rel_and_conformance_section(rel_diff: Dict[str, Any], violations: List[str]) -> List[str]:
    lines = ["## 4. Relationship Diff Preview", ""]
    if rel_diff["new_edges"]:
        lines.extend(["### Proposed New Edges", "| Source Node | Relationship | Target Node |", "| :--- | :--- | :--- |"])
        for e in rel_diff["new_edges"][:25]:
            lines.append(f"| `{e['from_id']}` | `{e['relationship']}` | `{e['to_id']}` |")
        lines.append("")

    status_str = "PASS" if not violations else "VIOLATIONS_DETECTED"
    lines.extend([
        "## 5. Schema Conformance & Type Policy Audit",
        "",
        f"- **Policy Status**: `{status_str}`",
        f"- **Violations Count**: `{len(violations)}`",
    ])
    if violations:
        lines.append("- **Violations**:")
        for v in violations:
            lines.append(f"  - ⚠️ {v}")
    else:
        lines.append("- **Verification**: All proposed entity types conform to active creatable node type policies.")
    lines.append("")
    return lines


def _render_preview_markdown(preview_dict: Dict[str, Any]) -> str:
    """Render markdown representation of the graph delta preview."""
    counts = preview_dict["counts"]
    lines = [
        "# Graph Delta Preview (Dry-Run Mode)",
        "",
        f"> **Meeting**: `{preview_dict['meeting_name']}` | **Date**: `{preview_dict['date']}` | **Project**: `{preview_dict['project']}`",
        f"> **Target Graph**: `{preview_dict['target_graph']}`",
        "> **Mutation Posture**: `PREVIEW_ONLY` (0 SQLite writes, 0 vector index updates committed)",
        "",
        "## 1. Planned Delta Counts Summary",
        "",
        "| Component | Planned Delta | Category Breakdown |",
        "| :--- | :--- | :--- |",
        f"| **Entities / Nodes** | **+{counts['nodes_added']}** | {counts['nodes_added']} new, {counts['nodes_modified']} modified, {counts['nodes_unchanged']} unchanged |",
        f"| **Assertions / Claims** | **+{counts['assertions_new']}** | {counts['assertions_new']} new, {counts['assertions_superseded']} superseded, {counts['assertions_unchanged']} unchanged |",
        f"| **Relationships / Edges** | **+{counts['relationships_new']}** | {counts['relationships_new']} new, {counts['relationships_retired']} retired |",
        "",
    ]
    lines.extend(_render_node_diff_section(preview_dict["node_diff"]))
    lines.extend(_render_assertion_diff_section(preview_dict["assertion_diff"]))
    lines.extend(_render_rel_and_conformance_section(preview_dict["relationship_diff"], preview_dict["schema_conformance"]["violations"]))
    return "\n".join(lines)


def _load_output3_dict(output_3: Union[Dict[str, Any], str, Path]) -> Dict[str, Any]:
    if isinstance(output_3, dict):
        return output_3
    p = Path(output_3) if isinstance(output_3, Path) or (isinstance(output_3, str) and not output_3.strip().startswith("{")) else None
    if p and p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    if isinstance(output_3, str) and output_3.strip().startswith("{"):
        try:
            return json.loads(output_3.strip())
        except Exception:
            return {}
    return {}


def resolve_preview_graph_path(repository_root: Path, metadata: Dict[str, Any]) -> Path:
    """Bind an implicit preview baseline to the active runtime graph."""
    if metadata.get("graph_path"):
        return Path(metadata["graph_path"]).resolve()
    from dual_pipeline.canonical_pipeline.publication_bindings import canonical_publication_runtime
    from dual_pipeline.runtime_evidence_core.inventory_cache import resolve_pair_and_root

    runtime = Path(metadata.get("runtime_root") or canonical_publication_runtime(repository_root)).resolve()
    pair_path = runtime / "active_pair.json"
    if not pair_path.is_file():
        return (repository_root / "graph" / "nee_graph.sqlite").resolve()
    pair, pair_root = resolve_pair_and_root(pair_path)
    graph_ref = pair.get("graph_path")
    if not isinstance(graph_ref, str) or not graph_ref:
        raise ValueError("Active runtime pair has no graph path for preview")
    graph = Path(graph_ref)
    graph = graph.resolve() if graph.is_absolute() else (pair_root / graph).resolve()
    if not graph.is_file():
        raise FileNotFoundError(f"Active runtime graph is missing for preview: {graph}")
    expected = pair.get("graph_sha256")
    if expected:
        import hashlib
        if hashlib.sha256(graph.read_bytes()).hexdigest() != expected:
            raise ValueError("Active runtime graph digest mismatch for preview")
    return graph


def generate_graph_delta_preview(
    output_3: Union[Dict[str, Any], str, Path],
    graph_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Computes graph delta preview and produces structured JSON and Markdown artifacts.

    Returns:
        Tuple of (preview_dict, preview_markdown_string).
    """
    meta = metadata or {}
    data = _load_output3_dict(output_3)
    mutations = data.get("graph_mutations") if isinstance(data.get("graph_mutations"), dict) else data

    conn: Optional[Any] = None
    if graph_path and Path(graph_path).is_file():
        try:
            conn = create_sqlite_conn(Path(graph_path).resolve(), readonly=True)
        except Exception as e:
            logger.warning("Failed to open readonly graph connection to %s: %s", graph_path, e)
            conn = None

    raw_entities = _extract_entities(mutations)
    raw_assertions = _extract_assertions(mutations)
    raw_relationships = _extract_relationships(mutations)

    node_additions, node_modifications, node_unchanged, schema_violations = _diff_nodes(raw_entities, conn)
    new_ast, superseded_ast, unchanged_ast = _diff_assertions(raw_assertions, conn)
    new_edges, retired_edges = _diff_relationships(raw_relationships)

    if conn is not None:
        conn.close()

    preview_dict: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "preview_type": "graph_delta_preview",
        "meeting_name": meta.get("meeting_name") or data.get("meeting_id") or "Meeting",
        "date": meta.get("date") or "01-01-2026",
        "project": meta.get("project") or "internal_consulting",
        "target_graph": str(graph_path) if graph_path else "memory",
        "graph_mutated": False,
        "counts": {
            "nodes_added": len(node_additions),
            "nodes_modified": len(node_modifications),
            "nodes_unchanged": len(node_unchanged),
            "assertions_new": len(new_ast),
            "assertions_superseded": len(superseded_ast),
            "assertions_unchanged": len(unchanged_ast),
            "relationships_new": len(new_edges),
            "relationships_retired": len(retired_edges),
        },
        "node_diff": {
            "additions": node_additions,
            "modifications": node_modifications,
            "unchanged": node_unchanged,
        },
        "assertion_diff": {
            "new_assertions": new_ast,
            "superseded_assertions": superseded_ast,
            "unchanged_assertions": unchanged_ast,
        },
        "relationship_diff": {
            "new_edges": new_edges,
            "retired_edges": retired_edges,
        },
        "schema_conformance": {
            "valid": len(schema_violations) == 0,
            "violations": schema_violations,
        },
    }

    preview_md = _render_preview_markdown(preview_dict)

    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "graph_delta_preview.json").write_text(json.dumps(preview_dict, indent=2), encoding="utf-8")
        (out_path / "graph_delta_preview.md").write_text(preview_md, encoding="utf-8")

    return preview_dict, preview_md
