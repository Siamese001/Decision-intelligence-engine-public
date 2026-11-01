import os
import unittest

from dual_pipeline.model_registry import (
    AcousticPinViolationError,
    resolve_acoustic_asr,
    resolve_acoustic_diarizer,
)


class AcousticPinEdgeCasesTest(unittest.TestCase):
    def test_mismatched_env_var_raises(self):
        orig_env = os.environ.get("ACOUSTIC_MLX_WHISPER_MODEL")
        os.environ["ACOUSTIC_MLX_WHISPER_MODEL"] = "whisper-tiny"

        try:
            with self.assertRaises(AcousticPinViolationError) as cm:
                resolve_acoustic_asr()
            self.assertIn("does not match SSOT pin", str(cm.exception))
        finally:
            if orig_env is not None:
                os.environ["ACOUSTIC_MLX_WHISPER_MODEL"] = orig_env
            else:
                del os.environ["ACOUSTIC_MLX_WHISPER_MODEL"]

    def test_mismatched_diarizer_env_var_raises(self):
        orig_env = os.environ.get("ACOUSTIC_DIARIZATION_MODEL")
        os.environ["ACOUSTIC_DIARIZATION_MODEL"] = "pyannote/speaker-diarization"

        try:
            with self.assertRaises(AcousticPinViolationError) as cm:
                resolve_acoustic_diarizer()
            self.assertIn("does not match SSOT pin", str(cm.exception))
        finally:
            if orig_env is not None:
                os.environ["ACOUSTIC_DIARIZATION_MODEL"] = orig_env
            else:
                del os.environ["ACOUSTIC_DIARIZATION_MODEL"]

if __name__ == "__main__":
    unittest.main()
