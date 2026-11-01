import pytest

from dual_pipeline.validation.nli_contradiction_gate import NLIContradictionGate


class AdversarialMockReranker:
    def predict_nli(self, pairs):
        results = []
        for a, b in pairs:
            # Subtle negations mapping
            a_lower = a.lower()
            b_lower = b.lower()

            if "approved" in a_lower and "rejected" in b_lower:
                results.append({"contradiction": 0.85, "entailment": 0.05})
            elif "funded" in a_lower and "canceled" in b_lower:
                results.append({"contradiction": 0.90, "entailment": 0.02})
            elif "deployed" in a_lower and "blocked" in b_lower:
                results.append({"contradiction": 0.88, "entailment": 0.04})
            else:
                results.append({"contradiction": 0.1, "entailment": 0.8})
        return results

@pytest.mark.parametrize("active_text, candidate_text", [
    ("The project is approved.", "The project is rejected."),
    ("The initiative is funded.", "The initiative was canceled."),
    ("The service is deployed.", "The service deployment is blocked.")
])
def test_subtle_negations(active_text, candidate_text):
    gate = NLIContradictionGate(reranker_service=AdversarialMockReranker())
    active_facts = [{"id": "fact-1", "text": active_text}]

    res = gate.evaluate_conflict(active_facts, candidate_text)

    assert res["has_contradiction"] is True
    # Margin Δ > 0.40
    assert res["max_conflict_score"] > 0.40
