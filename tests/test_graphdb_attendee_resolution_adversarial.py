"""
tests/test_graphdb_attendee_resolution_adversarial.py

Adversarial and boundary test suite for GraphDB attendee resolution covering:
1. Boundary payloads (empty inputs, 2-character names, whitespace, None).
2. Malformed inputs (HTML tags, embedded email headers, semicolons, unclosed brackets).
3. Concurrency & race conditions (parallel multi-threaded resolution across workers).
4. Invariant bypass & homonym disambiguation (project context isolation).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import unittest

from dual_pipeline.live_meeting_recorder_core.attendees import parse_attendees
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_attendee_list,
)


class TestGraphdbAttendeeResolutionAdversarial(unittest.TestCase):
    def test_category_1_boundary_payloads(self):
        """Boundary category: empty, whitespace, 2-character names, None."""
        # Empty string
        res_empty = parse_attendees("")
        self.assertEqual(len(res_empty), 1)
        self.assertEqual(res_empty[0]["name"], "Amit Ayer")

        # None input
        res_none = parse_attendees(None)
        self.assertEqual(len(res_none), 1)
        self.assertEqual(res_none[0]["name"], "Amit Ayer")

        # Pure whitespace and punctuation
        res_punct = parse_attendees("   , ; ,   ")
        self.assertEqual(len(res_punct), 1)
        self.assertEqual(res_punct[0]["name"], "Amit Ayer")

        # 2-character boundary names
        res_2char = parse_attendees("Ed", project_name="project_lens")
        self.assertEqual(len(res_2char), 1)
        self.assertEqual(res_2char[0]["name"], "Eduardo More")

    def test_category_2_malformed_inputs(self):
        """Malformed category: HTML tags, email headers, semicolons, invalid JSON."""
        # HTML tags
        html_input = "<b>Ed</b>, <i>Alex</i>, <span>PartnerAdvisory team</span>"
        res_html = parse_attendees(html_input, project_name="project_lens")
        names_html = {a["name"] for a in res_html}
        self.assertIn("Eduardo More", names_html)
        self.assertIn("Jennifer Pan", names_html)

        # Embedded email addresses
        email_input = "Eduardo More <ed.more@nee.com>, Alex Sanchez <alex.sanchez@consulting.example.com>"
        res_email = parse_attendees(email_input, project_name="project_lens")
        names_email = {a["name"] for a in res_email}
        self.assertIn("Eduardo More", names_email)
        self.assertIn("Alex Sanchez", names_email)

        # Truncated / malformed JSON
        malformed_json = '[{"name": "Taylor Mercer", "role": "PM"'
        res_json = parse_attendees(malformed_json)
        self.assertTrue(len(res_json) >= 1)

    def test_category_3_concurrency_and_race(self):
        """Concurrency category: 16 parallel threads querying resolver simultaneously."""
        resolver = PreSynthesisEntityResolver()
        inputs = [
            ("Ed, Alex, PartnerAdvisory team", "project_lens"),
            ("full PartnerAdvisory Team (Jen, Amit, Alex, Jake, Morgan Vance)", "project_lens"),
            ("Taylor Mercer, Justin M", "project_nexus"),
            ("Ed More", "project_lens"),
        ] * 4  # 16 concurrent executions

        def _resolve_task(item):
            raw, proj = item
            return resolve_attendee_list(raw, project_name=proj, resolver=resolver)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_resolve_task, item) for item in inputs]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), 16)
        for r in results:
            self.assertTrue(len(r) >= 1)
            for ent in r:
                self.assertTrue(bool(ent.canonical_name))

    def test_category_4_invariant_bypass_and_homonyms(self):
        """Invariant bypass category: homonym disambiguation under project context."""
        # In project_lens with PartnerAdvisory cohort, "Alex" resolves to Alex Sanchez and Alex Corrow
        res_lens = resolve_attendee_list("Alex, PartnerAdvisory team", project_name="project_lens")
        names_lens = {e.canonical_name for e in res_lens}
        self.assertIn("Alex Sanchez", names_lens)

        # Explicit last initial overrides homonym
        res_coro = resolve_attendee_list("Alex C", project_name="project_lens")
        self.assertEqual(res_coro[0].canonical_name, "Alex Corrow")

        res_sanchez = resolve_attendee_list("Alex S", project_name="project_lens")
        self.assertEqual(res_sanchez[0].canonical_name, "Alex Sanchez")


if __name__ == "__main__":
    unittest.main()
