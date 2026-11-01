"""
tests/test_vtt_cue_id_parsing.py

Validates that WebVTT cue identifiers (UUIDs, sequence numbers) preceding timestamp lines
are properly ignored and do not leak into speaker names or dialogue turns.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt


class TestVttCueIdParsing(unittest.TestCase):
    def test_cue_ids_do_not_leak(self) -> None:
        raw_vtt = """WEBVTT

e6ab7ee8-2c24-4ea7-b6f4-6daa04e3cbd6/8-0
00:00:03.309 --> 00:00:06.225
<v Kannan Chandraprakash>Yeah, the interview went well.
Should we get started?</v>

e6ab7ee8-2c24-4ea7-b6f4-6daa04e3cbd6/8-1
00:00:06.225 --> 00:00:07.629
<v Kannan Chandraprakash>Are we waiting for anyone?</v>

e6ab7ee8-2c24-4ea7-b6f4-6daa04e3cbd6/9-0
00:00:07.229 --> 00:00:08.669
<v Drew Oakley>Yeah, everyone's here.</v>

e6ab7ee8-2c24-4ea7-b6f4-6daa04e3cbd6/10-0
00:00:09.149 --> 00:00:10.429
<v Amit Ayer>Yeah, we're good.</v>
"""
        parsed_obj = parse_vtt_or_srt(raw_vtt)
        parsed = parsed_obj.text
        self.assertNotIn("e6ab7ee8", parsed)
        self.assertNotIn("8-0", parsed)
        self.assertNotIn("8-1", parsed)
        self.assertNotIn("9-0", parsed)
        self.assertNotIn("10-0", parsed)
        self.assertIn("**Kannan Chandraprakash:** Yeah, the interview went well. Should we get started? Are we waiting for anyone?", parsed)
        self.assertIn("**Drew Oakley:** Yeah, everyone's here.", parsed)
        self.assertIn("**Amit Ayer:** Yeah, we're good.", parsed)

    def test_kannan_vtt_file_parses_cleanly(self) -> None:
        vtt_path = Path("documents/Kannan - Welcome & Review NEA Interviews (1).vtt")
        if not vtt_path.exists():
            self.skipTest("documents/Kannan - Welcome & Review NEA Interviews (1).vtt not found")

        content = vtt_path.read_text(encoding="utf-8")
        parsed_obj = parse_vtt_or_srt(content)
        parsed = parsed_obj.text
        self.assertNotIn("e6ab7ee8", parsed)
        self.assertIn("**Kannan Chandraprakash:**", parsed)
        self.assertIn("**Drew Oakley:**", parsed)
        self.assertIn("**Amit Ayer:**", parsed)
        # Verify turns count
        turns = [l for l in parsed.splitlines() if l.startswith("### [")]
        self.assertGreaterEqual(len(turns), 100)


if __name__ == "__main__":
    unittest.main()
