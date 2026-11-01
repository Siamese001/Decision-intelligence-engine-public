# ruff: noqa: E402, I001
#!/usr/bin/env python3
"""Prepare an exact-scope, non-authorizing Clearance 5 approval packet."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, sha256_file, stable_id, write_json_exclusive


DEFAULT_BOUNDARY = (
    ROOT / "governance" / "decision_intelligence" / "W1_BOUNDARY_CONTRACT.json"
)
DEFAULT_SPINE = (
    ROOT / "governance" / "decision_intelligence" / "W2_SEMANTIC_SPINE_CONTRACT.json"
)
DEFAULT_TEMPORAL = (
    ROOT / "governance" / "decision_intelligence" / "W3_TEMPORAL_OBSERVATION_CONTRACT.json"
)
DEFAULT_OUTPUT_DIR = ROOT / ".local" / "clearance5-approval-packets"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_packet(
    proposal_path: Path,
    graph: Path,
    boundary: Path,
    spine: Path,
    temporal: Path,
    *,
    packet_id: str | None = None,
) -> dict[str, object]:
    proposal = _read(proposal_path)
    expected = proposal.get("canonical_digest", {}).get("value")
    actual = digest({key: value for key, value in proposal.items() if key != "canonical_digest"})
    if expected != actual:
        raise ValueError(f"proposal digest mismatch: expected {expected}, computed {actual}")
    graph_sha = sha256_file(graph)
    if proposal.get("base_graph_sha256") != graph_sha:
        raise ValueError("active graph no longer matches the proposal baseline")
    operations = proposal.get("operations", [])
    human = [op for op in operations if op.get("human_decision_required")]
    apply_ready = [op for op in operations if op.get("apply_ready")]
    if packet_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", packet_id):
        raise ValueError("packet_id must contain only letters, numbers, '.', '_', ':', or '-'")
    resolved_packet_id = packet_id or stable_id("clearance5", expected, graph_sha)
    packet: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_type": "clearance5_exact_scope_approval_packet",
        "packet_id": resolved_packet_id,
        "status": "AWAITING_EXACT_APPROVAL",
        "draft_only": True,
        "graph_mutation_authorized": False,
        "release_prepared": False,
        "release_activation": False,
        "proposal_path": str(proposal_path.resolve()),
        "proposal_file_sha256": sha256_file(proposal_path),
        "proposal_digest": expected,
        "base_graph_path": str(graph.resolve()),
        "base_graph_sha256": graph_sha,
        "contract_digests": {
            "w1_boundary": digest(_read(boundary)),
            "w2_semantic_spine": digest(_read(spine)),
            "w3_temporal_observation": digest(_read(temporal)),
        },
        "exact_scope": {
            "operation_count": len(operations),
            "apply_ready_operation_count": len(apply_ready),
            "human_decision_operation_count": len(human),
            "operation_ids": sorted(op["operation_id"] for op in operations),
            "human_decision_operation_ids": sorted(op["operation_id"] for op in human),
            "proposed_after_state_digests": {
                op["operation_id"]: op.get("proposed_after_state_digest")
                for op in operations
            },
        },
        "approval_requirements": [
            "Resolve every human-decision operation in the exact packet scope.",
            "Approve the proposal digest, base graph SHA, and all contract digests "
            "above as one binding.",
            "After approval, run the complete governed intake/release lifecycle "
            "and completion gate.",
            "Re-run W0, W1, W2, and W3 audits against the prepared candidate "
            "before any activation decision.",
        ],
        "blocked_reason": (
            "No exact-scope graph-mutation approval was supplied; preparation "
            "and activation are therefore withheld."
        ),
        "provider_calls": 0,
        "graph_mutations": 0,
    }
    packet["canonical_digest"] = {"algorithm": "sha256", "value": digest(packet)}
    return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--proposal",
        type=Path,
        required=True,
        help="Exact sealed migration proposal to bind; no historical default is allowed.",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        required=True,
        help="Exact graph snapshot that must match the proposal baseline.",
    )
    parser.add_argument("--boundary", type=Path, default=DEFAULT_BOUNDARY)
    parser.add_argument("--spine", type=Path, default=DEFAULT_SPINE)
    parser.add_argument("--temporal", type=Path, default=DEFAULT_TEMPORAL)
    parser.add_argument(
        "--packet-id",
        help="Optional stable identifier; defaults to one derived from proposal and graph digests.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Immutable output path; defaults to a scope-derived local packet path.",
    )
    args = parser.parse_args(argv)
    packet = build_packet(
        args.proposal,
        args.graph,
        args.boundary,
        args.spine,
        args.temporal,
        packet_id=args.packet_id,
    )
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else (DEFAULT_OUTPUT_DIR / f"{packet['packet_id']}.json").resolve()
    )
    write_json_exclusive(output, packet)
    print(
        json.dumps(
            {
                "status": packet["status"],
                "packet_id": packet["packet_id"],
                "packet_digest": packet["canonical_digest"]["value"],
                "output": str(output),
                "proposal_digest": packet["proposal_digest"],
                "human_decision_operation_count": packet["exact_scope"][
                    "human_decision_operation_count"
                ],
                "graph_mutations": packet["graph_mutations"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
