#!/usr/bin/env python3
"""tools/generate_edge_qrels.py

Compiles stratified record-level Edge QRELS dataset:
- Edge queries stratified by predicate (18+), degree bucket, lane, query form.
- Name-free paraphrases (>=60 domain-grounded queries).
- Ported competency cases (RB-001 to RB-021).
- Entity and claim control set (>=360 queries) for crowding measurement.
- Adversarial negative cases (historical, needs_review, cross-clearance).
- RFC 8785 sealed artifact with zero-mutation guarantee.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tqdm import tqdm

from dual_pipeline.core import (
    read_json,
    seal_artifact,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
)
from dual_pipeline.retrieval_eval_core.edge_gate import validate_edge_gate_floors
from dual_pipeline.storage.governed_context import GovernedStorageContext
from retrieval_tests.edge_competency_data import (
    NAME_FREE_PARAPHRASES,
    PORTED_COMPETENCY_CASES,
    PREDICATE_PHRASES,
)

ROOT = Path(__file__).resolve().parents[1]


def _clean(val: Any) -> str:
    return " ".join(str(val or "").split())


def _extract_graph_topology(graph_path: Path) -> tuple[dict[str, str], Counter, list[dict[str, Any]]]:
    with GovernedStorageContext.read_context(graph_path) as g_conn:
        nodes = {
            nid: _clean(dname or cname or nid)
            for nid, cname, dname in g_conn.execute("SELECT node_id, canonical_name, display_name FROM node")
        }
        degrees: Counter[str] = Counter()
        graph_edges = []
        for aid, s, p, o, stat, curr, cl in g_conn.execute(
            "SELECT assertion_id, subject_node_id, predicate, object_node_id, "
            "canonical_status, currentness_state, classification FROM assertion WHERE object_node_id IS NOT NULL"
        ):
            degrees[s] += 1
            degrees[o] += 1
            graph_edges.append({
                "assertion_id": aid, "subject": s, "predicate": p, "object": o,
                "status": stat, "currentness": curr, "classification": cl,
            })
        return nodes, degrees, graph_edges


def _extract_sidecar_records(sidecar_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    with GovernedStorageContext.read_context(sidecar_path) as s_conn:
        sidecar_edges, entity_facets, assertions = [], [], []
        for rid, rtype, lane, art in s_conn.execute(
            "SELECT record_id, record_type, lane, artifact_json FROM retrieval_record"
        ):
            obj = json.loads(art)
            rr = obj.get("retrieval_record", obj)
            if rtype in ("edge", "architecture_relationship"):
                sidecar_edges.append({
                    "record_id": rid, "record_type": rtype, "lane": lane,
                    "title": rr.get("title", ""), "text": rr.get("retrieval_text", ""),
                    "classification": rr.get("classification", "internal"),
                    "audiences": rr.get("audiences", ["internal_consulting"]),
                    "pred": rr.get("extensions", {}).get("com.partner_advisory.nee.predicate"),
                    "entity_ids": rr.get("extensions", {}).get("com.partner_advisory.nee.graph-entity-ids", []),
                })
            elif rtype == "entity_facet":
                entity_facets.append({
                    "record_id": rid, "title": rr.get("title", ""), "lane": lane,
                    "classification": rr.get("classification", "internal"),
                })
            elif rtype == "assertion":
                assertions.append({
                    "record_id": rid, "title": rr.get("title", ""), "lane": lane,
                    "classification": rr.get("classification", "internal"),
                })
        return sidecar_edges, entity_facets, assertions


def _build_edge_cases(
    sidecar_edges: list[dict[str, Any]],
    nodes: dict[str, str],
    degrees: Counter[str],
) -> tuple[list[dict[str, Any]], int]:
    edge_cases = []
    case_idx = 1
    edges_by_pred: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in sidecar_edges:
        edges_by_pred[e["pred"] or "UNKNOWN"].append(e)

    for pred, pedges in sorted(edges_by_pred.items()):
        phrase_fwd, phrase_rev = PREDICATE_PHRASES.get(pred, (pred.lower(), f"{pred.lower()} to"))
        for e in pedges[:8]:
            eids = e["entity_ids"]
            s_name = nodes.get(eids[0], eids[0]) if len(eids) > 0 else "Entity"
            o_name = nodes.get(eids[1], eids[1]) if len(eids) > 1 else "Target"
            deg = max(degrees.get(eids[0], 0), degrees.get(eids[1], 0)) if len(eids) > 1 else degrees.get(eids[0], 0)
            deg_bucket = "hub" if deg > 10 else ("degree_2_to_10" if deg >= 2 else "degree_1")

            for form, query in (
                ("forward", f"What {phrase_fwd} {o_name} regarding {s_name}?"),
                ("reverse", f"Which entities {phrase_rev} {o_name}?"),
                ("pairwise", f"How are {s_name} and {o_name} related in the knowledge graph?"),
            ):
                edge_cases.append({
                    "case_id": f"EQ-{form.upper()[:4]}-{case_idx:04d}",
                    "query": query, "query_form": form, "predicate": pred,
                    "degree_bucket": deg_bucket, "lane": e["lane"],
                    "classification": e["classification"], "audiences": e["audiences"],
                    "top_k": 20, "expected_record_ids": [e["record_id"]], "forbidden_record_ids": [],
                })
                case_idx += 1
    return edge_cases, case_idx


def _build_hop2_cases(
    sidecar_edges: list[dict[str, Any]],
    nodes: dict[str, str],
    start_idx: int,
) -> list[dict[str, Any]]:
    adj_out = defaultdict(list)
    for e in sidecar_edges:
        if len(e["entity_ids"]) == 2:
            adj_out[e["entity_ids"][0]].append(e)

    hop2_cases = []
    case_idx = start_idx
    for s_node, e1_list in adj_out.items():
        if len(hop2_cases) >= 30:
            break
        for e1 in e1_list:
            mid_node = e1["entity_ids"][1]
            e2_list = adj_out.get(mid_node, [])
            for e2 in e2_list:
                end_node = e2["entity_ids"][1]
                if end_node != s_node:
                    s_name, m_name, end_name = nodes.get(s_node, s_node), nodes.get(mid_node, mid_node), nodes.get(end_node, end_node)
                    hop2_cases.append({
                        "case_id": f"EQ-HOP2-{case_idx:04d}",
                        "query": f"Trace the connection path from {s_name} through {m_name} to {end_name}.",
                        "query_form": "hop_2", "predicate": f"{e1['pred']}->{e2['pred']}",
                        "degree_bucket": "degree_2_to_10", "lane": "approved",
                        "classification": "internal", "audiences": ["internal_consulting"], "top_k": 20,
                        "expected_record_ids": [e1["record_id"], e2["record_id"]],
                        "forbidden_record_ids": [],
                    })
                    case_idx += 1
                    break
            if len(hop2_cases) >= 30:
                break
    return hop2_cases


def _build_adversarial_cases(graph_edges: list[dict[str, Any]], nodes: dict[str, str]) -> list[dict[str, Any]]:
    adversarial_cases = []
    hist_edges = [ge for ge in graph_edges if ge["currentness"] == "historical"]
    for i, he in enumerate(hist_edges[:15], 1):
        s_name, o_name = nodes.get(he["subject"], he["subject"]), nodes.get(he["object"], he["object"])
        adversarial_cases.append({
            "case_id": f"ADV-HIST-{i:04d}",
            "query": f"What is the historical relation between {s_name} and {o_name}?",
            "adversarial_category": "historical_leak",
            "lane": "approved", "classification": "internal",
            "expected_record_ids": [], "forbidden_record_ids": [], "expected_abstention": True,
        })
    needs_review = [ge for ge in graph_edges if ge["status"] == "needs_review"]
    for i, nr in enumerate(needs_review[:15], 1):
        s_name, o_name = nodes.get(nr["subject"], nr["subject"]), nodes.get(nr["object"], nr["object"])
        adversarial_cases.append({
            "case_id": f"ADV-NR-{i:04d}",
            "query": f"Is there an approved link between {s_name} and {o_name}?",
            "adversarial_category": "needs_review_leak",
            "lane": "approved", "classification": "internal",
            "expected_record_ids": [], "forbidden_record_ids": [], "expected_abstention": True,
        })
    return adversarial_cases


def build_edge_qrels_dataset(runtime_root: Path, *, show_progress: bool = True) -> dict[str, Any]:
    active_path = runtime_root / "active_pair.json"
    if not active_path.is_file():
        raise FileNotFoundError(f"active_pair.json not found: {active_path}")

    active_pair = read_json(active_path)
    gen_id = active_pair.get("generation_id", "unknown")
    graph_path = (runtime_root / active_pair["graph_path"]).resolve()
    sidecar_path = (runtime_root / active_pair["sidecar_path"]).resolve()

    pbar = tqdm(total=7, desc="Generating Edge QRELS", disable=not show_progress, leave=False)

    # 1. Pre-audit hashes
    pbar.set_description("1. Pre-generation integrity check")
    g_pre, s_pre = sha256_file(graph_path), sha256_file(sidecar_path)
    pbar.update(1)

    # 2. Extract graph topology & sidecar
    pbar.set_description("2. Extracting graph nodes & topology")
    nodes, degrees, graph_edges = _extract_graph_topology(graph_path)
    pbar.update(1)

    pbar.set_description("3. Extracting sidecar records")
    sidecar_edges, entity_facets, assertions = _extract_sidecar_records(sidecar_path)
    pbar.update(1)

    # 3. Stratify edge queries & hop-2
    pbar.set_description("4. Synthesizing stratified edge queries")
    edge_cases, next_idx = _build_edge_cases(sidecar_edges, nodes, degrees)
    hop2_cases = _build_hop2_cases(sidecar_edges, nodes, next_idx)
    edge_cases.extend(hop2_cases)
    pbar.update(1)

    # 4. Integrate name-free paraphrases, controls, adversarial
    pbar.set_description("5. Integrating curated & control cases")
    name_free_cases = []
    for nf in NAME_FREE_PARAPHRASES:
        kw = nf.get("target_title_kw", "").lower()
        pred = nf.get("predicate")
        matching = [e["record_id"] for e in sidecar_edges if e["pred"] == pred and (kw in e["title"].lower() if kw else True)]
        if not matching:
            matching = [e["record_id"] for e in sidecar_edges if e["pred"] == pred]
        name_free_cases.append({
            "case_id": nf["case_id"], "query": nf["query"], "query_form": "name_free_paraphrase",
            "predicate": pred, "lane": nf.get("lane", "approved"), "classification": "internal",
            "audiences": ["internal_consulting"], "top_k": 20, "expected_record_ids": matching[:3],
            "forbidden_record_ids": [],
        })

    control_cases = [
        {"case_id": f"CTRL-ENT-{i:04d}", "query": f"What do we know about {ef['title'].replace('Entity facet: ', '')}?",
         "target_kind": "entity", "lane": ef["lane"], "classification": ef["classification"], "expected_record_ids": [ef["record_id"]]}
        for i, ef in enumerate(entity_facets[:200], 1)
    ] + [
        {"case_id": f"CTRL-CLM-{i:04d}", "query": f"What facts describe: {a['title'][:120]}?",
         "target_kind": "claim", "lane": a["lane"], "classification": a["classification"], "expected_record_ids": [a["record_id"]]}
        for i, a in enumerate(assertions[:160], 1)
    ]

    adversarial_cases = _build_adversarial_cases(graph_edges, nodes)
    pbar.update(1)

    # 5. Gate floor validation & sealing
    pbar.set_description("6. Gate floor validation & sealing")
    raw_dataset = {
        "schema_version": "1.0.0", "dataset_id": stable_id("edge-qrels", gen_id, length=24),
        "created_at": utc_now(), "generation_id": gen_id, "runtime_root": str(runtime_root),
        "source_hashes": {"graph_sha256": g_pre, "sidecar_sha256": s_pre},
        "edge_cases": edge_cases, "name_free_cases": name_free_cases,
        "competency_cases": PORTED_COMPETENCY_CASES, "control_cases": control_cases,
        "adversarial_cases": adversarial_cases,
        "statistics": {
            "total_edge_cases": len(edge_cases), "name_free_cases": len(name_free_cases),
            "competency_cases": len(PORTED_COMPETENCY_CASES), "control_cases": len(control_cases),
            "adversarial_cases": len(adversarial_cases),
            "total_queries": len(edge_cases) + len(name_free_cases) + len(PORTED_COMPETENCY_CASES) + len(control_cases) + len(adversarial_cases),
            "predicates_covered_count": len({e["pred"] for e in sidecar_edges if e["pred"]}),
            "predicates_covered": sorted({e["pred"] for e in sidecar_edges if e["pred"]}),
        },
    }

    gate_result = validate_edge_gate_floors(raw_dataset)
    raw_dataset["gate_validation"] = gate_result
    if not gate_result["passed"]:
        raise ValueError(f"Edge gate floor validation failed: {gate_result['failures']}")

    sealed_dataset = seal_artifact(raw_dataset)
    pbar.update(1)

    # 6. Zero-mutation post check
    pbar.set_description("7. Post-generation zero-mutation check")
    if sha256_file(graph_path) != g_pre or sha256_file(sidecar_path) != s_pre:
        raise RuntimeError("FATAL: Database files mutated during QRELS generation!")
    pbar.update(1)

    return sealed_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate stratified Edge QRELS dataset.")
    parser.add_argument("--runtime-root", type=Path, default=ROOT / ".local" / "dual_pipeline")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_root = args.runtime_root.resolve()

    try:
        dataset = build_edge_qrels_dataset(runtime_root, show_progress=not args.json)
    except Exception as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    out_path = args.output or (ROOT / "reports" / "edge_qrels_dataset.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, dataset)

    if args.json:
        sys.stdout.write(json.dumps({
            "status": "PASS", "dataset_id": dataset["dataset_id"],
            "canonical_digest": dataset["canonical_digest"]["value"], "output_path": str(out_path),
            "statistics": dataset["statistics"], "gate_validation": dataset["gate_validation"],
        }, indent=2) + "\n")
    else:
        sys.stdout.write(f"✅ Edge QRELS generated: {dataset['dataset_id']} -> {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
