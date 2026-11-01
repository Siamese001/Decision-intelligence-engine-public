from typing import Any

from dual_pipeline.grounding.chunking_engine import ChunkingEngine
from dual_pipeline.grounding.colbert_maxsim_auditor import ColbertMaxSimAuditor


class DummyBackend:
    def encode(self, text: str) -> dict[str, Any]:
        # Return a deterministic matrix based on text length to simulate ColBERT
        import numpy as np
        tokens = text.split()
        num_tokens = max(1, len(tokens))
        mat = np.ones((num_tokens, 32), dtype=np.float32)
        # Normalize to length 1
        mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        return {"colbert": mat.tolist(), "dense": [0.1]*1024, "sparse": {}}

def test_chunking_engine_parity():
    engine = ChunkingEngine(fine_min=2, fine_max=4, coarse_min=5, coarse_max=10)
    transcript = "**Speaker A:** [00:00:00] We will deploy on Tuesday.\n**Speaker B:** [00:00:05] Agreed."
    result = engine.chunk(transcript)
    assert len(result["turns"]) == 2
    assert len(result["fine_chunks"]) > 0

def test_colbert_grounding_parity(monkeypatch):
    import dual_pipeline.grounding.colbert_maxsim_auditor
    monkeypatch.setattr(dual_pipeline.grounding.colbert_maxsim_auditor, "InternalOperationalFlagEmbeddingBackend", lambda device: DummyBackend())

    auditor = ColbertMaxSimAuditor()
    transcript_chunks = [
        "Speaker A: We will deploy on Tuesday.",
        "Speaker B: Agreed."
    ]

    # Grounded claim
    claim = "The team agreed to deploy on Tuesday."
    res = auditor.audit_claim(claim, transcript_chunks)
    # The dummy backend returns dot product of 1.0 for all token pairs because they are identical normalized vectors
    # So maxsim score will be exactly 1.0, which is >= 0.72
    assert res["grounded"] is True
    assert res["best_score"] > 0.72

