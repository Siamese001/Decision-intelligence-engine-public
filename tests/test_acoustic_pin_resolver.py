import unittest
from unittest.mock import patch

from dual_pipeline.model_registry import (
    AcousticPinViolationError,
    resolve_acoustic_asr,
    resolve_acoustic_diarizer,
)


class AcousticPinResolverTest(unittest.TestCase):
    def test_resolver_returns_valid_pin(self):
        pin = resolve_acoustic_asr()
        self.assertEqual(pin.model_id, "mlx-community/whisper-large-v3-mlx")
        self.assertNotEqual(pin.snapshot_sha256, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_diarizer_returns_valid_pin_with_sub_models(self):
        pin = resolve_acoustic_diarizer()
        self.assertEqual(pin.model_id, "pyannote/speaker-diarization-3.1")
        self.assertGreater(len(pin.sub_models), 0)
        self.assertEqual(pin.sub_models[0].model_id, "pyannote/segmentation-3.0")

    @patch("dual_pipeline.model_registry._load_registry")
    def test_tampered_snapshot_raises(self, mock_load):
        # Return registry with empty-input SHA256
        mock_load.return_value = {
            "ACOUSTIC_MLX_WHISPER": {
                "default_model": "mlx-community/whisper-large-v3-mlx",
                "model_revision": "fake",
                "manifest_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            }
        }
        with self.assertRaises(AcousticPinViolationError) as cm:
            resolve_acoustic_asr()
        self.assertIn("Rejected empty-input SHA256", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
