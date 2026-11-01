import pytest
from dual_pipeline.output3_acceptance import verify_evidence

class TestOutput3ClaimSemanticsAdversarial:
    def test_missing_materiality_fails(self):
        evidence = {"locators": ["doc1#p1"]}
        assert not verify_evidence(evidence)

    def test_missing_locators_fails(self):
        evidence = {"materiality": "high"}
        assert not verify_evidence(evidence)

    def test_later_date_alone_cannot_override(self):
        # A later date alone cannot override evidence without locators and materiality
        evidence = {"date": "2026-10-10"}
        assert not verify_evidence(evidence)
