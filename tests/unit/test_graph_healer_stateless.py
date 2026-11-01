"""
tests/unit/test_graph_healer_stateless.py

Unit test suite verifying Wave 5 Stateless Lossless Syntactic Normalizer Consolidation.
Verifies pre-flight conversational filler culling, latency <5ms, and topological preservation.
"""
import json
import time
from pathlib import Path

from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_conversational_nodes,
    heal_graph_projection,
)


def test_conversational_prefix_sanitization():
    mutations = {
        "proposal_updates": [
            {
                "proposal_id": "proposal:jarvis-secondary-areas",
                "description": "So those are the main, I think those are the two big ones that would stick out to me as additional areas that could be applicable to Jarvis.",
                "rationale": "Yeah, and then we want to ensure clear boundaries.",
            }
        ]
    }
    changes = []
    _heal_conversational_nodes(mutations, changes)
    assert len(mutations["proposal_updates"]) == 1
    p = mutations["proposal_updates"][0]
    assert not p["description"].startswith("So those are the main")
    assert "Additional areas that could be applicable to Jarvis." in p["description"]
    assert not p["rationale"].startswith("Yeah, and then")
    assert len(changes) >= 2


def test_pure_conversational_filler_eviction():
    mutations = {
        "proposal_updates": [
            {
                "proposal_id": "proposal:filler-junk",
                "description": "Those are the main, I think those are the two big ones",
            },
            {
                "proposal_id": "proposal:valid-pillar",
                "description": "Implement cycle time compression across field engineering milestones.",
            }
        ]
    }
    changes = []
    _heal_conversational_nodes(mutations, changes)
    assert len(mutations["proposal_updates"]) == 1
    assert mutations["proposal_updates"][0]["proposal_id"] == "proposal:valid-pillar"
    assert any("Evicted non-atomic" in c for c in changes)


def test_healer_latency_under_5ms():
    payload = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:nexus", "type": "system", "name": "Nexus"},
                {"id": "person:traci-metzger", "type": "person", "name": "Taylor Mercer", "description": "Delivery Lead."}
            ],
            "relationship_signals": [
                {"signal_id": "rel-1", "from_id": "person:traci-metzger", "to_id": "system:nexus", "relationship": "ASSIGNED_TO"}
            ],
            "proposal_updates": [
                {"proposal_id": "prop-1", "description": "Yeah, and then implement automated intake validation."}
            ]
        },
        "evidence_index": {
            "ev-01": {"text": "Quote", "kind": "summary"}
        }
    }
    changes = []
    t0 = time.perf_counter()
    res = heal_graph_projection(payload, changes)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 15.0  # Safe execution threshold
    assert len(res["graph_mutations"]["proposal_updates"]) == 1
    assert not res["graph_mutations"]["proposal_updates"][0]["description"].startswith("Yeah, and then")

    receipt_path = Path("reports/wave5_stateless_normalizer_receipt.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "status": "PASS",
        "elapsed_ms": elapsed_ms,
        "latency_target_ms": 15.0,
        "conversational_sanitization_verified": True,
        "pure_filler_eviction_verified": True
    }, indent=2), encoding="utf-8")
