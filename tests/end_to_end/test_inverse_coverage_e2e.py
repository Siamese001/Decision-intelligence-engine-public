from typing import Any

from dual_pipeline.validation.inverse_coverage_auditor import InverseCoverageAuditor


class DummyCoverageBackend:
    def encode(self, text: str) -> dict[str, Any]:
        import numpy as np
        # Return different dense vectors depending on the text content to simulate coverage
        # If texts share tokens, they will have higher similarity
        vocab = ["agreed", "decided", "blocked", "risk", "owner", "feature", "deployment", "x", "y"]
        vec = np.zeros(1024, dtype=np.float32)
        text_lower = text.lower()
        for i, word in enumerate(vocab):
            if word in text_lower:
                vec[i] = 1.0

        # Add a tiny bit of random noise to prevent exactly zero vectors
        vec += np.random.normal(0, 0.01, size=1024).astype(np.float32)

        # Fake sparse weights
        sparse = {}
        for word in text_lower.split():
            sparse[word] = 1.0

        return {"colbert": [], "dense": vec.tolist(), "sparse": sparse}

def test_inverse_coverage_e2e(monkeypatch):
    import dual_pipeline.validation.inverse_coverage_auditor
    monkeypatch.setattr(dual_pipeline.validation.inverse_coverage_auditor, "InternalOperationalFlagEmbeddingBackend", lambda device: DummyCoverageBackend())

    auditor = InverseCoverageAuditor(coverage_threshold=0.90)

    transcript_turns = [
        {"turn_id": 1, "text": "We agreed to use Postgres for feature X."},
        {"turn_id": 2, "text": "What about the UI?"},
        {"turn_id": 3, "text": "The deployment is blocked due to a security risk."}
    ]

    # Recap containing both salient points
    recap = [
        "The team agreed to adopt Postgres for feature X.",
        "A security risk has blocked the current deployment."
    ]

    res = auditor.audit_coverage(transcript_turns, recap)
    assert res["passed"] is True
    assert res["coverage_ratio"] == 1.0
