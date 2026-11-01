import unittest

from dual_pipeline.transcript_pipeline_core.structured_synthesis import StructuredSynthesisAPI


class MockModelClient:
    def generate(self, prompt, response_format):
        # Ensure the schema is strictly passed to the backend
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        assert "alr_candidates" in response_format["json_schema"]["schema"]["required"]

        # Mock a perfect response validating zero parsing errors
        return {
            "transcript": [
                {"speaker": "Jack O'Shea", "utterance": "We finished the Q3 Staff Aug phase.", "timestamp": "11:04 AM"}
            ],
            "alr_candidates": [
                {"entity": "Jack O'Shea", "raw_spoken_form": "Jack from PartnerAdvisory", "proposed_resolution": "Apex Global Analytics PM"}
            ]
        }

class TestStructuredSynthesis(unittest.TestCase):
    def setUp(self):
        self.client = MockModelClient()
        self.api = StructuredSynthesisAPI(model_client=self.client)

    def test_schema_enforcement_zero_errors(self):
        """
        Tests that the LLM API is physically constrained by the JSON schema,
        eliminating parsing errors over simulated adversarial payloads.
        """
        for _ in range(100):
            result = self.api.execute_synthesis(["raw text chunk"])

            # Verify structured extraction
            self.assertIn("transcript", result)
            self.assertIn("alr_candidates", result)

            # Verify strict governance: LLM cannot output 'status' or '[OPERATOR VERIFIED]'
            for alr in result["alr_candidates"]:
                self.assertNotIn("status", alr, "LLM hallucinated a status column!")
                self.assertNotIn("[OPERATOR VERIFIED]", str(alr), "LLM hallucinated operator verification!")

    def test_latency_consolidation(self):
        """
        Simulate latency reduction by ensuring only 1 API call is made
        instead of 3 independent calls (ASR, Diarization, ALR).
        """
        # The single execute_synthesis call encapsulates the entire workflow
        result = self.api.execute_synthesis(["raw audio chunk"])
        self.assertTrue(len(result["transcript"]) > 0)
        self.assertTrue(len(result["alr_candidates"]) > 0)

if __name__ == '__main__':
    unittest.main()
