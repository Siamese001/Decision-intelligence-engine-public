import pytest
from dual_pipeline.output3_acceptance import seal_candidate_report, verify_evidence

class TestOutput3ClaimSemanticsE2E:
    def test_seal_candidate_report_preserves_lineage(self):
        candidate = {"claim": "X is True"}
        sealed = seal_candidate_report(candidate)
        assert sealed.get("_sealed") is True
        assert sealed.get("_lineage_preserved") is True

    def test_zero_candidate_results_allowed(self):
        from dual_pipeline.output3_acceptance import allow_zero_candidate_results
        assert allow_zero_candidate_results([]) is True

    def test_evidence_materiality_check(self):
        evidence = {"locators": ["doc1#p1"], "materiality": "high"}
        assert verify_evidence(evidence) is True
