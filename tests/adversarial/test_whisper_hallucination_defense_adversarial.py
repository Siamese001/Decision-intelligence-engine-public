"""
tests/adversarial/test_whisper_hallucination_defense_adversarial.py

Adversarial Test Suite for Whisper VAD, Subtitle Watermark, URL Hallucination,
and Generative Lexical Priming Defenses.

Attacks:
  ADV-1: Subtitle watermark & closed caption injection ('Tyler at GPKnives', 'Closed Captioning by').
  ADV-2: Acoustic non-speech vocalizations and bracketed subtitle tags ('Sigh.', '[Music]', '(Applause)').
  ADV-3: Generative prompt branching & naked URLs ('Apex GlobalVictory.com', 'Apex GlobalPlay').
  ADV-4: Acoustic VAD and low-RMS noise floor gating (ambient noise floor vs genuine speech).
  ADV-5: Whisper decoder no_speech_prob override bug neutralization and repetition compression ratio.
  ADV-6: Cross-chunk repetitive hallucination cascades and backchannel boundary preservation.
"""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    AudioChunkValidator,
)
from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    get_audio_rms,
    is_cross_chunk_repetition,
)
from dual_pipeline.live_meeting_recorder_core.audio_vad import AudioVADSegmenter
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _filter_hallucinations,
    _filter_segment_metadata,
)


def _synthesize_wav(
    path: Path,
    duration_sec: float,
    amplitude: float = 0.0,
    freq_hz: float = 0.0,
    sample_rate: int = 16000,
) -> Path:
    """Helper to synthesize test WAV with exact acoustic parameters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            if amplitude > 0.0:
                t = float(i) / sample_rate
                val = int(amplitude * math.sin(2.0 * math.pi * freq_hz * t)) if freq_hz > 0 else int(amplitude)
            else:
                val = 0
            frames.extend(struct.pack("<h", max(-32768, min(32767, val))))
        wf.writeframes(frames)
    return path


class TestWhisperHallucinationDefenseAdversarial(unittest.TestCase):
    """Adversarial test suite validating Whisper VAD, watermark filtering, and priming defenses."""

    # =========================================================================
    # ADV-1: Subtitle Watermarks & Closed Caption Injection
    # =========================================================================
    def test_adv1_subtitle_watermark_suppression(self):
        """ADV-1: Subtitle credit lines scraped from YouTube must be strictly suppressed."""
        samples = [
            "Closed Captioning provided by Tyler at GPKnives.",
            "Closed captions by Tyler at GPKnives",
            "Subtitles by the Amara.org community",
            "Captions provided by StreamText.Net",
            "English subtitles provided by OpenSubtitles",
            "Transcribed by rev.com",
        ]
        for text in samples:
            filtered = _filter_hallucinations(text)
            self.assertEqual(filtered, "", f"Failed to suppress subtitle watermark: '{text}'")

    # =========================================================================
    # ADV-2: Acoustic Non-Speech Vocalizations & Bracketed Subtitle Tags
    # =========================================================================
    def test_adv2_acoustic_vocalizations_and_brackets(self):
        """ADV-2: Standalone vocalizations ('Sigh.') and bracketed tags ('[Music]') must be suppressed."""
        samples = [
            "Sigh.",
            "Sigh",
            "[Sigh]",
            "(sigh)",
            "[Music]",
            "(Music)",
            "[Applause]",
            "Gasp.",
            "[Laughter]",
            "[Silence]",
        ]
        for text in samples:
            filtered = _filter_hallucinations(text)
            self.assertEqual(filtered, "", f"Failed to suppress non-speech artifact: '{text}'")

    def test_adv2_genuine_speech_containing_vocalization_word_preserved(self):
        """ADV-2b: Legitimate conversational use of words like 'sigh' or 'music' must NOT be dropped."""
        legitimate_utterances = [
            "We let out a sigh of relief when the deployment succeeded.",
            "The background music at the venue was too loud for the microphone.",
            "Dan gave a presentation on the music streaming infrastructure.",
        ]
        for text in legitimate_utterances:
            filtered = _filter_hallucinations(text)
            self.assertEqual(filtered, text, f"Falsely suppressed legitimate speech: '{text}'")

    # =========================================================================
    # ADV-3: Generative Lexical Priming Branching & Naked Web URLs
    # =========================================================================
    def test_adv3_generative_prompt_branch_and_urls(self):
        """ADV-3: Generative branching from prompt brands ('Apex GlobalVictory.com') and URLs must be suppressed."""
        prompt = "Amit Ayer, Drew Oakley discussing APEX_TRANS architecture. Domain terms: Apex Global Enterprises, Apex Power & Grid, ROCC."

        hallucinations = [
            "Apex GlobalVictory.com",
            "Apex GlobalPlay",
            "www.apex_globalplay.com",
            "Apex Global Enterprises Victory",
            "https://www.enterprise.example.com",
            "consulting.example.com",
        ]
        for text in hallucinations:
            filtered = _filter_hallucinations(text, whisper_prompt=prompt)
            self.assertEqual(filtered, "", f"Failed to suppress prompt-branched hallucination or URL: '{text}'")

    def test_adv3_legitimate_domain_discussion_preserved(self):
        """ADV-3b: Legitimate multi-word conversational discussion must not be falsely dropped."""
        prompt = "Amit Ayer discussing APEX_TRANS architecture."
        real_speech = "We need to coordinate with the Apex Global energy team regarding the substation cutover schedule."
        filtered = _filter_hallucinations(real_speech, whisper_prompt=prompt)
        self.assertEqual(filtered, real_speech)

    # =========================================================================
    # ADV-4: Acoustic VAD and Low-RMS Noise Floor Gating
    # =========================================================================
    def test_adv4_ambient_noise_floor_gating(self):
        """ADV-4: Ambient laptop microphone room noise (RMS 65) must be gated before Whisper."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ambient_wav = _synthesize_wav(tmp_path / "ambient_noise.wav", duration_sec=15.0, amplitude=90.0, freq_hz=60.0)
            rms = get_audio_rms(ambient_wav)
            self.assertTrue(55.0 <= rms <= 75.0, f"Unexpected RMS: {rms}")

            # Validator must flag as silence
            val = AudioChunkValidator.validate_chunk(ambient_wav)
            self.assertTrue(val.is_silence)

            # AudioVADSegmenter must find 0 speech boundaries
            vad = AudioVADSegmenter(energy_threshold=0.0037, min_speech_duration_ms=240)
            boundaries = vad.detect_speech_boundaries(ambient_wav)
            self.assertEqual(len(boundaries), 0, f"Expected 0 speech intervals in ambient room noise, got {boundaries}")

    def test_adv4_transient_click_impulse_rejected(self):
        """ADV-4b: Isolated brief impulses (< 100ms click) must not trigger speech validation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            click_wav = Path(tmp_dir) / "click_impulse.wav"
            with wave.open(str(click_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                # 5 seconds silence
                wf.writeframes(b"\x00\x00" * 80000)
                # 50ms impulse click (800 samples)
                wf.writeframes(struct.pack("<h", 8000) * 800)
                # 9.95 seconds silence
                wf.writeframes(b"\x00\x00" * 159200)

            vad = AudioVADSegmenter(energy_threshold=0.0037, min_speech_duration_ms=240)
            boundaries = vad.detect_speech_boundaries(click_wav)
            self.assertEqual(len(boundaries), 0, "Brief 50ms click should be rejected by min_speech_duration filter")

    def test_adv4_genuine_speech_audio_accepted(self):
        """ADV-4c: Genuine sustained speech audio (RMS 400, 2s duration) must clear VAD."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            speech_wav = _synthesize_wav(Path(tmp_dir) / "speech.wav", duration_sec=3.0, amplitude=500.0, freq_hz=300.0)
            vad = AudioVADSegmenter(energy_threshold=0.0037, min_speech_duration_ms=240)
            self.assertTrue(vad.is_speech_chunk(speech_wav))
            boundaries = vad.detect_speech_boundaries(speech_wav)
            self.assertGreaterEqual(len(boundaries), 1)

    # =========================================================================
    # ADV-5: Segment Telemetry & Whisper `no_speech_prob` Override Bug Neutralization
    # =========================================================================
    def test_adv5_whisper_no_speech_override_bug_neutralized(self):
        """ADV-5: High no_speech_prob must discard segment even if avg_logprob is artificially high."""
        hallucinated_segments = [
            {
                "id": 0,
                "text": "Closed Captioning provided by Tyler at GPKnives.",
                "no_speech_prob": 0.88,
                "avg_logprob": -0.25,
                "compression_ratio": 1.2,
            }
        ]
        filtered_text = _filter_segment_metadata(hallucinated_segments)
        self.assertEqual(filtered_text, "", "Failed to reject segment with no_speech_prob >= 0.60")

    def test_adv5_repetition_loop_compression_ratio_rejected(self):
        """ADV-5b: Autoregressive repetition loop with high compression ratio must be rejected."""
        loop_segments = [
            {
                "id": 0,
                "text": "and the decisions and the decisions and the decisions and the decisions",
                "no_speech_prob": 0.05,
                "avg_logprob": -0.4,
                "compression_ratio": 2.7,
            }
        ]
        filtered_text = _filter_segment_metadata(loop_segments)
        self.assertEqual(filtered_text, "", "Failed to reject segment with compression_ratio > 2.2")

    # =========================================================================
    # ADV-6: Cross-Chunk Deduplication & Backchannel Boundary Preservation
    # =========================================================================
    def test_adv6_cross_chunk_hallucination_cascade_suppressed(self):
        """ADV-6: Hallucinated phrase repeating across adjacent chunk boundaries must be halted."""
        history = ["We reviewed the architecture document."]
        hallucinated_phrase = "Apex GlobalVictory.com"

        history.append(hallucinated_phrase)
        self.assertTrue(is_cross_chunk_repetition(hallucinated_phrase, history))

    def test_adv6_conversational_backchannels_preserved_until_runaway(self):
        """ADV-6b: Natural backchannel ('Yeah.', 'Right.') allowed once, but blocked on runaway loop (>=3)."""
        self.assertFalse(is_cross_chunk_repetition("Yeah.", ["Yeah."]))
        self.assertFalse(is_cross_chunk_repetition("Right.", ["Right."]))

        self.assertTrue(is_cross_chunk_repetition("Yeah.", ["Yeah.", "Yeah."]))
        self.assertTrue(is_cross_chunk_repetition("Right.", ["Right.", "Right."]))


if __name__ == "__main__":
    unittest.main()
