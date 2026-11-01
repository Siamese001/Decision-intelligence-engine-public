import unittest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import _align_segments_with_diarization


class MockTurn:
    def __init__(self, start, end):
        self.start = start
        self.end = end

class MockDiarization:
    def __init__(self, turns):
        self.turns = turns
    def itertracks(self, yield_label=True):
        for start, end, spk in self.turns:
            yield MockTurn(start, end), None, spk

class MLXAlignmentE2ETest(unittest.TestCase):
    def test_word_level_alignment_splits_segment(self):
        sanitized_segments = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "Hello world how are you",
                "words": [
                    {"start": 0.0, "end": 0.4, "word": "Hello "},
                    {"start": 0.4, "end": 0.8, "word": "world "},
                    {"start": 1.0, "end": 1.4, "word": "how "},
                    {"start": 1.4, "end": 1.7, "word": "are "},
                    {"start": 1.7, "end": 2.0, "word": "you"},
                ]
            }
        ]

        # Speaker A speaks 0 to 0.9, Speaker B speaks 0.9 to 2.1
        diarization = MockDiarization([
            (0.0, 0.9, "Speaker A"),
            (0.9, 2.1, "Speaker B")
        ])

        aligned = _align_segments_with_diarization(sanitized_segments, diarization)
        self.assertEqual(len(aligned), 2)

        self.assertEqual(aligned[0]["speaker"], "Speaker A")
        self.assertEqual(aligned[0]["text"], "Hello world")
        self.assertEqual(len(aligned[0]["words"]), 2)

        self.assertEqual(aligned[1]["speaker"], "Speaker B")
        self.assertEqual(aligned[1]["text"], "how are you")
        self.assertEqual(len(aligned[1]["words"]), 3)

    def test_unknown_speaker_collapse(self):
        sanitized_segments = [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "foo",
                "words": [{"start": 0.0, "end": 1.0, "word": "foo"}]
            },
            {
                "start": 1.0,
                "end": 2.0,
                "text": "bar",
                "words": [{"start": 1.0, "end": 2.0, "word": "bar"}]
            }
        ]
        diarization = MockDiarization([])
        with self.assertRaises(RuntimeError) as ctx:
            _align_segments_with_diarization(sanitized_segments, diarization)
        self.assertIn(">50%", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
