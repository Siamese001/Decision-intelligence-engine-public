import unittest

from dual_pipeline.audio_ingestion_core.factory import get_default_transcriber
from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber


class TranscriberFactorySSOTTest(unittest.TestCase):
    def test_factory_returns_mlx(self):
        transcriber = get_default_transcriber(mock=True)
        self.assertIsInstance(transcriber, LocalMLXAudioTranscriber)

    def test_factory_auto_returns_mlx(self):
        transcriber = get_default_transcriber(mock=True, backend="auto")
        self.assertIsInstance(transcriber, LocalMLXAudioTranscriber)

    def test_factory_mlx_returns_mlx(self):
        transcriber = get_default_transcriber(mock=True, backend="mlx")
        self.assertIsInstance(transcriber, LocalMLXAudioTranscriber)

    def test_factory_raises_on_faster_whisper(self):
        with self.assertRaises(ValueError):
            get_default_transcriber(mock=True, backend="faster_whisper")

    def test_legacy_files_removed(self):
        from pathlib import Path
        base = Path("dual_pipeline/audio_ingestion_core")
        self.assertFalse((base / "cloud_transcriber.py").exists())
        self.assertFalse((base / "local_whisper.py").exists())

if __name__ == "__main__":
    unittest.main()
