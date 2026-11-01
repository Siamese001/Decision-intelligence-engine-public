"""Decision Witness Subgraph Engine.

Extracts and cryptographically seals the minimal evidence-bearing subgraph
(G_witness) connecting decision criteria, constraints, and options to underlying
source evidence, binding the inference chain to the immutable active generation pair.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any, Mapping, Sequence

from .contracts import create_artifact
from .core import stable_id
from .graph_retrieval_core.capability_bridge import (
    SHARED_CAPABILITY_RELATIONSHIP,
    collect_capability_bridge_pairs,
    resolve_bridge_edge_type,
)
from .in_memory_graph import InMemoryGraphTopology


def _match_nodes_by_label(
    text: str,
    nodes: Mapping[str, dict[str, Any]],
) -> list[str]:
    """Find nodes whose canonical or display name appears in the given text."""
    normalized = str(text or "").casefold()
    matched = []
    for nid, node in nodes.items():
        c_name = str(node.get("canonical_name", "")).casefold()
        d_name = str(node.get("display_name", "")).casefold()
        if (len(c_name) >= 3 and c_name in normalized) or (
            len(d_name) >= 3 and d_name in normalized
        ):
            matched.append(nid)
    return matched


def _collect_active_evidence_ids(
    assessments: Sequence[Mapping[str, Any]] | None,
    evidence_pack: Mapping[str, Any],
) -> dict[str, str | None]:
    active_evidence_ids: dict[str, str | None] = {}
    for assm in assessments or ():
        opt_id = assm.get("option_id")
        eids = list(assm.get("supporting_evidence_ids", ())) + list(assm.get("counterevidence_ids", ()))
        for eid in eids:
            active_evidence_ids[eid] = opt_id
    constraints = evidence_pack.get("constraints", ()) if evidence_pack else ()
    for item in constraints:
        eid = item.get("evidence_id") if isinstance(item, Mapping) else None
        if eid:
            active_evidence_ids.setdefault(str(eid), None)
    if not active_evidence_ids and evidence_pack:
        active_evidence_ids = {eid: None for eid in evidence_pack.get("source_evidence_ids", ())}
    return active_evidence_ids


def _map_evidence_and_primary_assertions(
    active_evidence_ids: dict[str, str | None],
    topology: InMemoryGraphTopology,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    evidence_to_assertions: dict[str, list[str]] = defaultdict(list)
    for assertion_id, ev_list in topology.supporting_evidence.items():
        for eid in ev_list:
            if eid in active_evidence_ids:
                evidence_to_assertions[eid].append(assertion_id)
    node_primary_assertion: dict[str, str] = {}
    for aid, assn in topology.assertions.items():
        sub = assn.get("subject_node_id")
        if sub and sub not in node_primary_assertion:
            node_primary_assertion[sub] = aid
    return evidence_to_assertions, node_primary_assertion


def _bind_evidence_to_topology(
    active_evidence_ids: dict[str, str | None],
    topology: InMemoryGraphTopology,
) -> tuple[list[dict[str, Any]], set[str], dict[str, str]]:
    evidence_to_assertions, node_primary_assertion = _map_evidence_and_primary_assertions(
        active_evidence_ids, topology
    )
    evidence_bindings: list[dict[str, Any]] = []
    evidence_node_ids: set[str] = set()
    for eid, opt_id in active_evidence_ids.items():
        for aid in evidence_to_assertions.get(eid, ()):
            evidence_bindings.append({"evidence_id": eid, "assertion_id": aid, "option_id": opt_id})
            assertion = topology.assertions.get(aid) or {}
            for nid in (assertion.get("subject_node_id"), assertion.get("object_node_id")):
                if nid and nid in topology.nodes:
                    evidence_node_ids.add(nid)
    return evidence_bindings, evidence_node_ids, node_primary_assertion


def _populate_seed_and_evidence_nodes(
    topology: InMemoryGraphTopology,
    decision_frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]] | None,
    evidence_node_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], set[str], set[str], set[str]]:
    witness_nodes_map: dict[str, dict[str, Any]] = {}
    option_seeds: set[str] = set()
    for opt in options or ():
        for nid in _match_nodes_by_label(str(opt.get("label", "")), topology.nodes):
            option_seeds.add(nid)
            witness_nodes_map[nid] = {
                "node_id": nid, "canonical_name": topology.nodes[nid]["canonical_name"],
                "node_type": topology.nodes[nid]["node_type"], "role": "seed_option",
            }
    criteria_seeds: set[str] = set()
    for crit in decision_frame.get("criteria", ()):
        for nid in _match_nodes_by_label(str(crit.get("label", "")), topology.nodes):
            criteria_seeds.add(nid)
            witness_nodes_map.setdefault(nid, {
                "node_id": nid, "canonical_name": topology.nodes[nid]["canonical_name"],
                "node_type": topology.nodes[nid]["node_type"], "role": "seed_criterion",
            })
    constraint_seeds: set[str] = {
        nid for nid, n in topology.nodes.items() if n.get("node_type") == "constraint"
    }
    for c_item in evidence_pack.get("constraints", ()) if evidence_pack else ():
        if isinstance(c_item, Mapping):
            text = f"{c_item.get('title', '')} {c_item.get('summary', '')}"
            constraint_seeds.update(_match_nodes_by_label(text, topology.nodes))
    for nid in sorted(constraint_seeds):
        witness_nodes_map.setdefault(nid, {
            "node_id": nid, "canonical_name": topology.nodes[nid]["canonical_name"],
            "node_type": topology.nodes[nid]["node_type"], "role": "seed_criterion",
        })
    for nid in evidence_node_ids:
        witness_nodes_map.setdefault(nid, {
            "node_id": nid, "canonical_name": topology.nodes[nid]["canonical_name"],
            "node_type": topology.nodes[nid]["node_type"], "role": "evidence_entity",
        })
    return witness_nodes_map, option_seeds, criteria_seeds, constraint_seeds


def _append_topology_causal_paths(
    topology: InMemoryGraphTopology,
    seed_sources: list[str],
    target_nodes: list[str],
    witness_nodes_map: dict[str, dict[str, Any]],
    witness_edges_map: dict[str, dict[str, Any]],
    causal_paths_list: list[dict[str, Any]],
) -> None:
    if not (seed_sources and target_nodes):
        return
    for path in topology.find_causal_paths(source_ids=seed_sources, target_ids=target_nodes, max_hops=3, direction="any"):
        causal_paths_list.append({
            "source_id": path["source_id"], "target_id": path["target_id"],
            "hop_count": path["hop_count"], "node_ids": path["node_ids"], "assertion_ids": path["assertion_ids"],
        })
        for n_dict in path["nodes"]:
            witness_nodes_map.setdefault(n_dict["node_id"], {
                "node_id": n_dict["node_id"], "canonical_name": n_dict["canonical_name"],
                "node_type": n_dict["node_type"], "role": "intermediate_bridge",
            })
        for e_dict in path["edges"]:
            witness_edges_map.setdefault(e_dict["edge_id"], {
                "edge_id": e_dict["edge_id"], "from_node_id": e_dict["from_node_id"],
                "to_node_id": e_dict["to_node_id"], "relationship_family": e_dict["relationship_family"],
                "edge_type": e_dict["edge_type"] or "RELATES_TO", "assertion_id": e_dict["assertion_id"],
                "materiality": e_dict.get("materiality", "medium"),
            })


def _append_discovered_paths_and_bridges(
    topology: InMemoryGraphTopology,
    seed_sources: list[str],
    target_nodes: list[str],
    node_primary_assertion: dict[str, str],
    witness_nodes_map: dict[str, dict[str, Any]],
    witness_edges_map: dict[str, dict[str, Any]],
    causal_paths_list: list[dict[str, Any]],
) -> None:
    _append_topology_causal_paths(
        topology, seed_sources, target_nodes, witness_nodes_map, witness_edges_map, causal_paths_list
    )
    allowed_targets = set(target_nodes) | set(witness_nodes_map)
    seed_set = set(seed_sources)
    directed_pairs = [
        (src_id, tgt_id)
        for u_id, v_id in sorted(collect_capability_bridge_pairs(topology.nodes, topology.assertions))
        for src_id, tgt_id in ((u_id, v_id), (v_id, u_id))
        if src_id in seed_set and tgt_id in allowed_targets
    ]
    for src_id, tgt_id in directed_pairs:
        aid = node_primary_assertion.get(tgt_id) or node_primary_assertion.get(src_id) or f"cap-bridge:{src_id}:{tgt_id}"
        etype = resolve_bridge_edge_type(src_id, tgt_id, topology.nodes, topology.assertions)
        edge_id = f"{aid}:{src_id}->{tgt_id}"
        witness_nodes_map.setdefault(tgt_id, {
            "node_id": tgt_id, "canonical_name": topology.nodes[tgt_id]["canonical_name"],
            "node_type": topology.nodes[tgt_id]["node_type"], "role": "evidence_entity",
        })
        witness_edges_map.setdefault(edge_id, {
            "edge_id": edge_id, "from_node_id": src_id, "to_node_id": tgt_id,
            "relationship_family": SHARED_CAPABILITY_RELATIONSHIP, "edge_type": etype,
            "assertion_id": aid, "materiality": "high",
        })
        causal_paths_list.append({
            "source_id": src_id, "target_id": tgt_id, "hop_count": 1,
            "node_ids": [src_id, tgt_id], "assertion_ids": [aid],
        })


class DecisionWitnessEngine:
    """Constructs, seals, and exports minimal Decision Witness Subgraphs."""

    @staticmethod
    def build_witness_subgraph(
        *,
        decision_frame: Mapping[str, Any],
        evidence_pack: Mapping[str, Any],
        topology: InMemoryGraphTopology,
        active_pair: Mapping[str, Any],
        options: Sequence[Mapping[str, Any]] | None = None,
        assessments: Sequence[Mapping[str, Any]] | None = None,
        recommended_option: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build and cryptographically seal a Decision Witness Subgraph."""
        frame_id = str(decision_frame.get("artifact_id", "unknown-frame"))
        gen_id = str(active_pair.get("generation_id", "unknown-generation"))
        graph_sha = str(active_pair.get("graph_sha256", topology.graph_sha256))
        sidecar_sha = str(active_pair.get("sidecar_sha256", ""))

        active_evidence_ids = _collect_active_evidence_ids(assessments, evidence_pack)
        evidence_bindings, evidence_node_ids, node_primary_assertion = _bind_evidence_to_topology(
            active_evidence_ids, topology
        )
        witness_nodes_map, option_seeds, criteria_seeds, constraint_seeds = _populate_seed_and_evidence_nodes(
            topology, decision_frame, evidence_pack, options, evidence_node_ids
        )
        witness_edges_map: dict[str, dict[str, Any]] = {}
        causal_paths_list: list[dict[str, Any]] = []

        seed_sources = sorted(option_seeds | criteria_seeds | constraint_seeds)
        target_nodes = sorted(evidence_node_ids | constraint_seeds)
        _append_discovered_paths_and_bridges(
            topology, seed_sources, target_nodes, node_primary_assertion,
            witness_nodes_map, witness_edges_map, causal_paths_list,
        )

        all_witness_ids = set(witness_nodes_map.keys())
        if all_witness_ids:
            for e_dict in topology.extract_induced_subgraph(all_witness_ids)["edges"]:
                witness_edges_map.setdefault(e_dict["edge_id"], {
                    "edge_id": e_dict["edge_id"], "from_node_id": e_dict["from_node_id"],
                    "to_node_id": e_dict["to_node_id"], "relationship_family": e_dict["relationship_family"],
                    "edge_type": e_dict["edge_type"] or "RELATES_TO", "assertion_id": e_dict["assertion_id"],
                    "materiality": e_dict.get("materiality", "medium"),
                })

        dedup_paths = {
            (p["source_id"], p["target_id"], tuple(p["node_ids"])): p for p in causal_paths_list
        }
        sorted_paths = sorted(dedup_paths.values(), key=lambda p: (p["hop_count"], p["source_id"], p["target_id"]))
        return create_artifact(
            "decision-witness-subgraph",
            stable_id("decision-witness-subgraph", frame_id, gen_id, graph_sha, length=26),
            decision_frame_id=frame_id,
            active_generation_id=gen_id,
            graph_sha256=graph_sha,
            sidecar_sha256=sidecar_sha,
            witness_nodes=sorted(witness_nodes_map.values(), key=lambda n: n["node_id"]),
            witness_edges=sorted(witness_edges_map.values(), key=lambda e: e["edge_id"]),
            causal_paths=sorted_paths,
            evidence_bindings=sorted(evidence_bindings, key=lambda b: (b["evidence_id"], b["assertion_id"])),
            extensions={"com.partner_advisory.nee.constraint-seed-node-ids": sorted(constraint_seeds)},
        )

    @staticmethod
    def export_cytoscape(witness_subgraph: Mapping[str, Any]) -> dict[str, Any]:
        """Convert witness subgraph to Cytoscape.js elements format for web/UI rendering."""
        elements: dict[str, list[dict[str, Any]]] = {"nodes": [], "edges": []}
        for node in witness_subgraph.get("witness_nodes", []):
            elements["nodes"].append({
                "data": {
                    "id": node["node_id"], "label": node["canonical_name"],
                    "type": node["node_type"], "role": node["role"],
                }
            })
        for edge in witness_subgraph.get("witness_edges", []):
            elements["edges"].append({
                "data": {
                    "id": edge["edge_id"], "source": edge["from_node_id"], "target": edge["to_node_id"],
                    "label": edge["edge_type"], "family": edge["relationship_family"],
                    "materiality": edge["materiality"], "assertion_id": edge["assertion_id"],
                }
            })
        return {"elements": elements}

    @staticmethod
    def export_graphml(witness_subgraph: Mapping[str, Any]) -> str:
        """Export witness subgraph to standard GraphML XML."""
        root = ET.Element(
            "graphml",
            {
                "xmlns": "http://graphml.graphdrawing.org/xmlns",
                "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            },
        )
        for key_id, target, attr_name in (
            ("d0", "node", "name"), ("d1", "node", "type"), ("d2", "node", "role"),
            ("d3", "edge", "label"), ("d4", "edge", "materiality"),
        ):
            ET.SubElement(root, "key", {"id": key_id, "for": target, "attr.name": attr_name, "attr.type": "string"})
        graph = ET.SubElement(root, "graph", {"id": "G_witness", "edgedefault": "directed"})
        for node in witness_subgraph.get("witness_nodes", []):
            n_elem = ET.SubElement(graph, "node", {"id": node["node_id"]})
            for key_id, field in (("d0", "canonical_name"), ("d1", "node_type"), ("d2", "role")):
                elem = ET.SubElement(n_elem, "data", {"key": key_id})
                elem.text = node[field]
        for edge in witness_subgraph.get("witness_edges", []):
            e_elem = ET.SubElement(
                graph, "edge", {"id": edge["edge_id"], "source": edge["from_node_id"], "target": edge["to_node_id"]}
            )
            for key_id, field in (("d3", "edge_type"), ("d4", "materiality")):
                elem = ET.SubElement(e_elem, "data", {"key": key_id})
                elem.text = edge[field]
        return ET.tostring(root, encoding="utf-8").decode("utf-8")


__all__ = ["DecisionWitnessEngine"]
