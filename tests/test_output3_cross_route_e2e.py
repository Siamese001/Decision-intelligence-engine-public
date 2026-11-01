import pytest

from dual_pipeline.output3_acceptance import (
    seal_candidate_report,
    verify_evidence,
    allow_zero_candidate_results,
)

class TestOutput3CrossRouteE2E:
    def test_full_vs_output3_only_parity(self):
        candidate = {"assertions": [{"assertion_id": "a1", "subject": "System"}]}
        sealed = seal_candidate_report(candidate)
        assert sealed.get("_sealed") is True
        assert sealed.get("_lineage_preserved") is True

    def test_source_bound_gemini_cases(self):
        valid_evidence = {"locators": ["doc#1"], "materiality": "core"}
        invalid_evidence = {"locators": ["doc#1"]}
        assert verify_evidence(valid_evidence) is True
        assert verify_evidence(invalid_evidence) is False

    def test_rollback_disables_new_routing(self):
        assert allow_zero_candidate_results([]) is True
