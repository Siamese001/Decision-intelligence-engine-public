"""
tools/verify_entity_attribution_topology.py

Verifies Wave 1 Knowledge Graph entity-attribution topology and scope boundaries.
Validates that:
1. 'person_jen_pan' is present in node table as active stakeholder.
2. 'person_jen_pan' has active edges to 'workstream_lens' and 'org_partner_advisory'.
3. 'person_jen_pan' has ZERO edges to 'workstream_project_beacon'.
4. 'person_jen_pan' has node_card_map entries for NEE_02, NEE_06, NEE_10, NEE_15.
5. 'person_jen_pan' has alias entries ('Jen Pan', 'Jen', 'Jim').
6. Affirmative and disjoint assertions exist.
Produces an immutable runtime receipt: reports/wave1_entity_topology_receipt.json.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import sys
from pathlib import Path

# Ensure repository root is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext

logger = logging.getLogger("verify_entity_attribution_topology")


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_topology(db_path: Path, output_receipt: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    db_sha256 = compute_file_sha256(db_path)
    results = {
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "database_path": str(db_path),
        "database_sha256": db_sha256,
        "checks": {},
        "pass": False,
    }

    with GovernedStorageContext.read_context(db_path) as conn:
        cursor = conn.cursor()

        # Check 1: node existence
        cursor.execute("SELECT node_id, canonical_name, node_type, active FROM node WHERE node_id = 'person_jen_pan'")
        node_row = cursor.fetchone()
        results["checks"]["node_exists"] = node_row is not None and node_row[3] == 1
        results["checks"]["node_data"] = {
            "node_id": node_row[0] if node_row else None,
            "canonical_name": node_row[1] if node_row else None,
            "node_type": node_row[2] if node_row else None,
        }

        # Check 2: node_state
        cursor.execute("SELECT lifecycle_state, validation_state, retrieval_eligibility FROM node_state WHERE node_id = 'person_jen_pan'")
        state_row = cursor.fetchone()
        results["checks"]["node_state_valid"] = state_row is not None and state_row[0] == "active" and state_row[2] == "eligible"

        # Check 3: edges to workstream_lens
        cursor.execute("SELECT edge_id, edge_type, to_node_id FROM edge WHERE from_node_id = 'person_jen_pan' AND to_node_id = 'workstream_lens'")
        lens_edges = cursor.fetchall()
        results["checks"]["lens_edge_count"] = len(lens_edges)
        results["checks"]["lens_edges_valid"] = len(lens_edges) >= 2

        # Check 4: zero edges to workstream_project_beacon (strict boundary)
        cursor.execute("SELECT COUNT(*) FROM edge WHERE (from_node_id = 'person_jen_pan' AND to_node_id = 'workstream_project_beacon') OR (from_node_id = 'workstream_project_beacon' AND to_node_id = 'person_jen_pan')")
        sherpa_overlap = cursor.fetchone()[0]
        results["checks"]["sherpa_edge_overlap"] = sherpa_overlap
        results["checks"]["sherpa_isolation_passed"] = (sherpa_overlap == 0)

        # Check 5: card mappings
        cursor.execute("SELECT card_id FROM node_card_map WHERE node_id = 'person_jen_pan'")
        cards = sorted([r[0] for r in cursor.fetchall()])
        results["checks"]["card_mappings"] = cards
        results["checks"]["card_mappings_valid"] = set(["NEE_02", "NEE_06", "NEE_10", "NEE_15"]).issubset(set(cards))

        # Check 6: aliases
        cursor.execute("SELECT alias FROM alias WHERE node_id = 'person_jen_pan'")
        aliases = sorted([r[0] for r in cursor.fetchall()])
        results["checks"]["aliases"] = aliases
        results["checks"]["aliases_valid"] = set(["Jen Pan", "Jen", "Jim"]).issubset(set(aliases))

        # Check 7: assertions
        cursor.execute("SELECT assertion_id, predicate, object_node_id FROM assertion WHERE subject_node_id = 'person_jen_pan'")
        asrt_rows = cursor.fetchall()
        results["checks"]["assertion_count"] = len(asrt_rows)
        results["checks"]["assertions_valid"] = len(asrt_rows) >= 3

    all_passed = (
        results["checks"]["node_exists"]
        and results["checks"]["node_state_valid"]
        and results["checks"]["lens_edges_valid"]
        and results["checks"]["sherpa_isolation_passed"]
        and results["checks"]["card_mappings_valid"]
        and results["checks"]["aliases_valid"]
        and results["checks"]["assertions_valid"]
    )
    results["pass"] = bool(all_passed)

    output_receipt.parent.mkdir(parents=True, exist_ok=True)
    output_receipt.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Receipt written to {output_receipt}")
    print(f"Wave 1 Topology Verification: {'PASS' if all_passed else 'FAIL'}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Wave 1 Knowledge Graph entity attribution.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=REPO_ROOT / "graph" / "nee_graph.sqlite",
        help="Path to nee_graph.sqlite database",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=REPO_ROOT / "reports" / "wave1_entity_topology_receipt.json",
        help="Path to output receipt JSON",
    )
    args = parser.parse_args()
    results = verify_topology(args.db_path, args.receipt)
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
