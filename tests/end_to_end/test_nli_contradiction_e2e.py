from dual_pipeline.validation.graph_topology_filter import GraphTopologyFilter
from dual_pipeline.validation.nli_contradiction_gate import NLIContradictionGate


class MockReranker:
    def predict_nli(self, pairs):
        results = []
        for a, b in pairs:
            # Simple mock logic based on text
            if "not" in b.lower() and "not" not in a.lower():
                results.append({"contradiction": 0.9, "entailment": 0.1})
            else:
                results.append({"contradiction": 0.1, "entailment": 0.9})
        return results

def test_topology_filter():
    edges = [
        {"source": "fact-1", "target": "fact-2", "relation": "SUPERSEDED_BY"}
    ]
    filter_ = GraphTopologyFilter(edges)

    facts = [
        {"id": "fact-1", "text": "Feature X is planned for Q1."},
        {"id": "fact-2", "text": "Feature X is delayed to Q2."}
    ]

    active = filter_.filter_active_facts(facts)
    assert len(active) == 1
    assert active[0]["id"] == "fact-2"

def test_nli_contradiction_e2e():
    active_facts = [
        {"id": "fact-1", "text": "The database migration is approved."}
    ]

    gate = NLIContradictionGate(reranker_service=MockReranker())

    # Claim that contradicts active fact
    candidate_claim = "The database migration is not approved."
    res = gate.evaluate_conflict(active_facts, candidate_claim)

    assert res["has_contradiction"] is True
    assert res["max_conflict_score"] > 0.40
    assert res["conflicting_fact"]["id"] == "fact-1"
