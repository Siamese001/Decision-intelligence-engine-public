from dual_pipeline.validation.inverse_coverage_auditor import InverseCoverageAuditor
from tests.end_to_end.test_inverse_coverage_e2e import DummyCoverageBackend


def test_omission_adversarial(monkeypatch):
    import dual_pipeline.validation.inverse_coverage_auditor
    monkeypatch.setattr(dual_pipeline.validation.inverse_coverage_auditor, "InternalOperationalFlagEmbeddingBackend", lambda device: DummyCoverageBackend())

    auditor = InverseCoverageAuditor(coverage_threshold=0.90)

    transcript_turns = [
        {"turn_id": 1, "text": "We agreed to use Postgres for feature X."},
        {"turn_id": 2, "text": "What about the UI?"},
        {"turn_id": 3, "text": "The deployment is blocked due to a security risk."}
    ]

    # Recap omitting the security risk
    recap = [
        "The team agreed to adopt Postgres for feature X."
    ]

    res = auditor.audit_coverage(transcript_turns, recap)

    # Coverage should be 0.5 (1 out of 2 salient turns covered)
    assert res["passed"] is False
    assert res["coverage_ratio"] == 0.5
    assert len(res["omitted_turns"]) == 1
    assert "blocked" in res["omitted_turns"][0]
