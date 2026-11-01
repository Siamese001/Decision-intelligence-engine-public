from typing import Any

from dual_pipeline.grounding.colbert_maxsim_auditor import ColbertMaxSimAuditor


class AdversarialDummyBackend:
    def encode(self, text: str) -> dict[str, Any]:
        import numpy as np
        # Return random vectors so dot products are very small (simulating no match)
        num_tokens = max(1, len(text.split()))
        mat = np.random.randn(num_tokens, 32).astype(np.float32)
        mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        return {"colbert": mat.tolist(), "dense": [0.1]*1024, "sparse": {}}

def test_colbert_grounding_adversarial(monkeypatch):
    import dual_pipeline.grounding.colbert_maxsim_auditor
    monkeypatch.setattr(dual_pipeline.grounding.colbert_maxsim_auditor, "InternalOperationalFlagEmbeddingBackend", lambda device: AdversarialDummyBackend())

    auditor = ColbertMaxSimAuditor(threshold=0.72)

    # A totally unrelated transcript
    transcript_chunks = [
        "Speaker A: Let's focus on the frontend UI today.",
        "Speaker B: I will adjust the React components."
    ]

    # Adversarial hallucinated claim
    claim = "The backend database was migrated to PostgreSQL."

    res = auditor.audit_claim(claim, transcript_chunks)

    # With random orthogonal vectors, MaxSim score should be far below 0.72
    assert res["grounded"] is False
    assert res["best_score"] < 0.72

