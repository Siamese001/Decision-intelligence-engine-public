from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge


def test_master_vector_evaluator_e2e(monkeypatch):
    import dual_pipeline.grounding.colbert_maxsim_auditor
    import dual_pipeline.validation.inverse_coverage_auditor
    from tests.end_to_end.test_inverse_coverage_e2e import DummyCoverageBackend

    # Mock both backends
    monkeypatch.setattr(dual_pipeline.validation.inverse_coverage_auditor, "InternalOperationalFlagEmbeddingBackend", lambda device: DummyCoverageBackend())

    # For colbert we can use the same DummyCoverageBackend or write a specific mock
    class DummyColbertBackend:
        def encode(self, text):
            import numpy as np
            return {"colbert": np.ones((max(1, len(text.split())), 32), dtype=np.float32).tolist()}

    monkeypatch.setattr(dual_pipeline.grounding.colbert_maxsim_auditor, "InternalOperationalFlagEmbeddingBackend", lambda device: DummyColbertBackend())

    # We also need to mock the Reranker for the contradiction gate
    from dual_pipeline.validation.nli_contradiction_gate import BgeRerankerService
    class MockReranker(BgeRerankerService):
        def predict(self, pairs):
            return [0.9] * len(pairs) # High relevance -> Low contradiction

    import dual_pipeline.validation.nli_contradiction_gate
    monkeypatch.setattr(dual_pipeline.validation.nli_contradiction_gate, "BgeRerankerService", MockReranker)

    judge = DecomposedMultiJudge()

    transcript = "**Speaker A:** [00:00:00] We agreed to deploy the new DB.\n**Speaker B:** [00:00:05] Yes."
    recap = "The team agreed to deploy the new DB."
    claims = [{"text": "Deploy the new DB."}]
    active_facts = []

    res = judge.evaluate_all(transcript, recap, claims, active_facts)

    assert res["passed"] is True
    assert res["generative_tokens_used"] == 0
    assert res["evaluation_time_ms"] < 800
