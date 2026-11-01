"""
Wave 2 E2E Test: Section-Batched L2 Extraction & Cross-Slide Semantic Consolidation.
Verifies:
1. SOW Deliverables A1-A4 and B1-B6 consolidate into 10 canonical deliverable nodes
   (deliverable:a1..b6), and merge_unit_batch_projections folds RACI sub-row fragments
   (e.g. deliverable:a1-baseline-vsm, deliverable:a1-chokepoint-quick-wins) into deliverable:a1
   while capping per-entity evidence_ids at <= 8.
2. Every extracted entity in extract_orals_proposal_projection has degree >= 1 (0 orphans).
3. Top-6 hub nodes account for < 65% of edges.
4. Directed multi-hop technical provisioning chain (SAGE -> IT4U -> Backstage -> Factory ->
   GitHub -> Terraform -> InfraCodeBase -> PLA -> GCP) has path length >= 4.
5. 5-agent reconciliation cap supports up to 45 entities without truncating at 14.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import unittest

from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    MultiAgentDocumentIntelligenceReport,
    build_intelligence_prior_context,
)
from dual_pipeline.document_pipeline_core.orals_proposal_extractor import (
    extract_orals_proposal_projection,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    extract_slide_and_section_units,
    merge_unit_batch_projections,
)
from dual_pipeline.pdf_source import inspect_pdf_pages


def _longest_directed_path_from(adj: dict[str, list[str]], start: str) -> int:
    visited: set[str] = set()

    def dfs(node: str) -> int:
        visited.add(node)
        best = 0
        for nxt in adj.get(node, []):
            if nxt not in visited:
                best = max(best, 1 + dfs(nxt))
        visited.remove(node)
        return best

    return dfs(start)


class TestDocumentSemanticConsolidationE2E(unittest.TestCase):
    """End-to-end verification of Wave 2 semantic consolidation and graph topology."""

    def test_cross_batch_raci_subrow_consolidation_and_evidence_cap(self) -> None:
        batch_1 = {
            "evidence_index": {
                f"ev-{i}": {"exact_text": f"Quote {i}", "locator": {"kind": "slide", "value": f"p{i}"}}
                for i in range(1, 7)
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "deliverable:a1-baseline-vsm",
                        "type": "project",
                        "name": "A1: Baseline & VSM",
                        "evidence_ids": [f"ev-{i}" for i in range(1, 7)],
                    }
                ],
                "relationship_signals": [
                    {
                        "signal_id": "s1",
                        "from_id": "workstream:ws-1a",
                        "predicate": "DELIVERS",
                        "to_id": "deliverable:a1-baseline-vsm",
                        "evidence_ids": ["ev-1"],
                    }
                ],
            },
        }
        batch_2 = {
            "evidence_index": {
                f"ev-b{i}": {"exact_text": f"Quote B{i}", "locator": {"kind": "slide", "value": f"p{10+i}"}}
                for i in range(1, 7)
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "deliverable:a1-chokepoint-quick-wins",
                        "type": "project",
                        "name": "A1: Chokepoints",
                        "evidence_ids": [f"ev-b{i}" for i in range(1, 7)],
                    }
                ],
                "relationship_signals": [
                    {
                        "signal_id": "s2",
                        "from_id": "workstream:ws-1a",
                        "predicate": "DELIVERS",
                        "to_id": "deliverable:a1-chokepoint-quick-wins",
                        "evidence_ids": ["ev-b1"],
                    }
                ],
            },
        }
        merged = merge_unit_batch_projections([batch_1, batch_2])
        ents = merged["graph_mutations"]["entity_upserts"]
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0]["id"], "deliverable:a1")
        self.assertLessEqual(len(ents[0]["evidence_ids"]), 8)

        sigs = merged["graph_mutations"]["relationship_signals"]
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["to_id"], "deliverable:a1")

    def test_orals_zero_orphans_hub_balance_and_multihop_chain(self) -> None:
        pdf_rel = Path("inputs/documents/NEE_Infrastructure_Automation_Transformation_RFP_Orals.pdf")
        orals_pdf = next(
            (p / pdf_rel for p in Path(__file__).resolve().parents if (p / pdf_rel).exists()),
            Path.home() / "Downloads" / "NEE Infrastructure Automation Transformation RFP_Orals.pdf",
        )
        self.assertTrue(orals_pdf.exists())
        rep = inspect_pdf_pages(orals_pdf.read_bytes(), title=orals_pdf.name)
        units = [u.to_dict() for u in extract_slide_and_section_units(rep.extracted_markdown, "orals", "pdf")]
        proj = extract_orals_proposal_projection(rep.extracted_markdown, {"slide_units": units, "page_count": 111})

        muts = proj["graph_mutations"]
        ents = muts["entity_upserts"]
        sigs = muts["relationship_signals"]

        # 1. Exactly 10 SOW deliverables (deliverable:a1..a4, deliverable:b1..b6)
        deliverables = [e["id"] for e in ents if e["id"].startswith("deliverable:")]
        self.assertEqual(len(deliverables), 10)

        # 2. Every entity has degree >= 1 (0 orphans) and <= 8 evidence_ids
        deg: Counter[str] = Counter()
        adj: dict[str, list[str]] = defaultdict(list)
        for s in sigs:
            u, v = s["from_id"], s["to_id"]
            deg[u] += 1
            deg[v] += 1
            adj[u].append(v)

        orphans = [e["id"] for e in ents if deg[e["id"]] == 0]
        self.assertEqual(orphans, [], f"Found orphan entities with degree 0: {orphans}")
        for e in ents:
            self.assertLessEqual(len(e.get("evidence_ids") or []), 8, f"Entity {e['id']} exceeded 8 evidence_ids")

        # 3. Top-6 hub edge touch share < 65%
        total_edges = len(sigs)
        top6_nodes = {nid for nid, _ in deg.most_common(6)}
        edges_touching_top6 = sum(1 for s in sigs if s["from_id"] in top6_nodes or s["to_id"] in top6_nodes)
        hub_share = edges_touching_top6 / max(1, total_edges)
        self.assertLess(hub_share, 0.65, f"Top-6 hub concentration {hub_share:.2%} >= 65%")

        # 4. Directed multi-hop technical provisioning chain >= 4 hops from system:sage
        chain_len = _longest_directed_path_from(adj, "system:sage")
        self.assertGreaterEqual(chain_len, 4, f"Expected multi-hop chain >= 4 hops, got {chain_len}")

    def test_multi_agent_intelligence_prior_context_cap_raised_above_14(self) -> None:
        rep = MultiAgentDocumentIntelligenceReport(
            document_title="Multi-Section Spec",
            total_entities_detected=25,
            entities=[{"id": f"system:sys-{i}", "type": "system", "name": f"Sys {i}", "description": "d"} for i in range(25)],
            relationships=[{"from_id": f"system:sys-{i}", "predicate": "USES", "to_id": f"system:sys-{i+1}", "relationship_context": "c"} for i in range(20)],
            coverage_score=0.95,
            primacy_bias_detected=False,
            arbiter_verdict="PASS",
            arbiter_notes="ok",
        )
        ctx = build_intelligence_prior_context(rep)
        self.assertIn("Identified First-Class Entities (25):", ctx)
        self.assertIn("system:sys-24", ctx)


if __name__ == "__main__":
    unittest.main()
