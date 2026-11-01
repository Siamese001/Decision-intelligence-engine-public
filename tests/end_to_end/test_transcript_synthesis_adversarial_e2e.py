import unittest
from typing import Any, Dict

from dual_pipeline.prompts.contracts.output2_contract import validate_c21_depth_floors_and_anchors
from dual_pipeline.transcript_qa_core.gate2_advisories import check_gate_2_11_numeric_tracing


class TestTranscriptSynthesisAdversarialE2E(unittest.TestCase):
    def test_intra_appendix_copy_pasting_rejected(self):
        """Ensure identical boilerplate copy-pasting across speakers is rejected."""
        malformed_f2 = """
# Meeting Objective
Objective.
# Key Discussion Points & Findings
- **Topic**: Blah blah.
# Action & Governance Ledger
| 1 | Action | Description | Amit | TBD | [12:00:00] | Proposed |
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Amit
- **Architecture**: We need to leverage BGE-M3 to fix the retrieval latency issues. This is a primary driver of cost.
- **Cost**: The cost is too high and we need to fix it.

### Jack
- **Architecture**: We need to leverage BGE-M3 to fix the retrieval latency issues. This is a primary driver of cost.
- **Cost**: The cost is too high and we need to fix it.
"""
        meta: Dict[str, Any] = {"attendees": [{"name": "Amit"}, {"name": "Jack"}], "meeting_duration_minutes": 30}
        passed, failures = validate_c21_depth_floors_and_anchors(malformed_f2, meta)
        self.assertFalse(passed)
        self.assertTrue(any("INTRA_APPENDIX_REDUNDANCY" in f for f in failures))

    def test_empty_appendix_rejected(self):
        """Ensure empty appendix is rejected for >15min meetings."""
        malformed_f2 = """
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Amit
"""
        meta: Dict[str, Any] = {"attendees": [{"name": "Amit"}], "meeting_duration_minutes": 30}
        passed, failures = validate_c21_depth_floors_and_anchors(malformed_f2, meta)
        print(f"DEBUG app failures: {failures}")
        self.assertFalse(passed)
        self.assertTrue(any("SECTION_7_DEPTH_FLOOR_VIOLATION" in f for f in failures))

    def test_numeric_hallucination_tracing(self):
        """Test numeric hallucination checks."""
        f2_text = "We processed $1.2M records today without citation.\n\nThen we saw 30% things.\n\nThen 90 days people showed up.\n\nFinally 1,200,000 items failed."
        passed, failures = check_gate_2_11_numeric_tracing(f2_text)
        print(f"DEBUG numeric failures: {failures}")
        # Should flag as untraced because there are >3 unanchored numbers
        self.assertFalse(passed)
        self.assertTrue(any("$1.2M" in f for f in failures))

if __name__ == "__main__":
    unittest.main()
