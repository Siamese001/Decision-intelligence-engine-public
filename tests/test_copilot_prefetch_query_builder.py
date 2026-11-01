"""
tests/test_copilot_prefetch_query_builder.py

Unit tests for PrefetchQueryBuilder and project terms normalization.
"""

from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.prefetch_query_builder import (
    build_prefetch_query_specs,
    normalize_project_terms,
)


class TestPrefetchQueryBuilder(unittest.TestCase):
    def test_normalize_project_terms_known_synonyms(self):
        terms = normalize_project_terms("project_nexus")
        self.assertIn("nexus", terms)
        self.assertIn("dociq", terms)

        terms_neet = normalize_project_terms("apex_trans")
        self.assertIn("apex_trans", terms_neet)
        self.assertIn("transmission", terms_neet)
        self.assertIn("insights hub", terms_neet)

    def test_build_prefetch_query_specs_with_attendees(self):
        attendees = [
            {"name": "harper Hoffman", "role": "VP", "org": "APEX_TRANS"},
            {"name": "Alex Rivera", "role": "Director", "org": "APEX_TRANS"},
            {"name": "Speaker", "role": "Unknown", "org": "Unknown"},
        ]
        specs = build_prefetch_query_specs(
            project_name="apex_trans",
            meeting_name="APEX_TRANS Insights Hub Architecture",
            attendees=attendees,
        )
        self.assertTrue(len(specs) >= 4)

        query_types = [s["query_type"] for s in specs]
        self.assertIn("DECISIONS", query_types)
        self.assertIn("RISKS", query_types)
        self.assertIn("ACTION_ITEMS", query_types)
        self.assertIn("MEETING_TOPIC", query_types)
        self.assertIn("ATTENDEES", query_types)

        # Check attendee spec
        att_spec = next(s for s in specs if s["query_type"] == "ATTENDEES")
        self.assertIn("harper Hoffman", att_spec["query_text"])
        self.assertIn("Alex Rivera", att_spec["query_text"])
        self.assertNotIn("Speaker", att_spec["query_text"])

    def test_build_prefetch_query_specs_empty_metadata(self):
        specs = build_prefetch_query_specs()
        self.assertTrue(len(specs) >= 3)
        query_types = [s["query_type"] for s in specs]
        self.assertIn("DECISIONS", query_types)
        self.assertIn("RISKS", query_types)
        self.assertIn("ACTION_ITEMS", query_types)


if __name__ == "__main__":
    unittest.main()
