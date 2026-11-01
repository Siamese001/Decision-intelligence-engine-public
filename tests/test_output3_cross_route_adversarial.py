import pytest

from dual_pipeline.output3_acceptance import verify_evidence, seal_candidate_report

class TestOutput3CrossRouteAdversarial:
    def test_stale_seals_and_interrupted_runs(self):
        empty_evidence = {}
        assert not verify_evidence(empty_evidence)
        partial_evidence = {"materiality": "critical"}
        assert not verify_evidence(partial_evidence)

    def test_unsupported_raci_generalizations_rejected(self):
        candidate = {"unsupported_raci": True}
        sealed = seal_candidate_report(candidate)
        assert "_sealed" in sealed
        assert "_lineage_preserved" in sealed
