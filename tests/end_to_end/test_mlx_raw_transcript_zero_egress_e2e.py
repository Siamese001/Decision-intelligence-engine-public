import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path


class MLXRawTranscriptZeroEgressE2ETest(unittest.TestCase):
    def test_mlx_raw_transcript_zero_egress_e2e(self):
        # We must manually manage the patch since we're using unittest
        orig_env_hf = os.environ.get("HF_HUB_OFFLINE")
        orig_env_tr = os.environ.get("TRANSFORMERS_OFFLINE")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        orig_hf_offline_const = None
        try:
            import huggingface_hub.constants
            orig_hf_offline_const = getattr(huggingface_hub.constants, "HF_HUB_OFFLINE", None)
            huggingface_hub.constants.HF_HUB_OFFLINE = True
        except (ImportError, AttributeError):
            pass

        attempts = []
        _LOOPBACK = ("127.", "::1", "localhost", "0.0.0.0")
        _orig_connect = socket.socket.connect
        _orig_getaddrinfo = socket.getaddrinfo

        def _is_loopback(host: str) -> bool:
            return any(str(host).startswith(p) for p in _LOOPBACK)

        def _guard_connect(self_, address):
            host = address[0] if isinstance(address, tuple) else str(address)
            if self_.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(host):
                attempts.append({"op": "connect", "host": str(host)})
                raise ConnectionRefusedError(f"ZERO_EGRESS_PROBE blocked connect to {host}")
            return _orig_connect(self_, address)

        def _guard_getaddrinfo(host, *a, **k):
            if host and not _is_loopback(str(host)):
                attempts.append({"op": "dns", "host": str(host)})
                raise socket.gaierror(f"ZERO_EGRESS_PROBE blocked DNS for {host}")
            return _orig_getaddrinfo(host, *a, **k)

        socket.socket.connect = _guard_connect
        socket.getaddrinfo = _guard_getaddrinfo


        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp = Path(tmp_dir)
                aiff = tmp / "probe.aiff"
                wav = tmp / "probe.wav"

                subprocess.run(["say", "-o", str(aiff), "Hello team, this is a short zero egress probe."], check=True)
                import imageio_ffmpeg
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(wav)], check=True)

                from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
                    LocalMLXAudioTranscriber,
                    is_mlx_available,
                )
                if not is_mlx_available():
                    self.skipTest("MLX not available on this hardware")

                t = LocalMLXAudioTranscriber(model_name="mlx-community/whisper-tiny")
                text = t.transcribe(str(wav), metadata={"attendees": ["Probe Speaker"]})

                self.assertGreater(len(text), 0, "Transcript should not be empty")
                self.assertFalse(attempts, f"Network egress detected during local MLX transcribe: {attempts}")
        finally:
            socket.socket.connect = _orig_connect
            socket.getaddrinfo = _orig_getaddrinfo
            if orig_env_hf is not None:
                os.environ["HF_HUB_OFFLINE"] = orig_env_hf
            else:
                del os.environ["HF_HUB_OFFLINE"]
            if orig_env_tr is not None:
                os.environ["TRANSFORMERS_OFFLINE"] = orig_env_tr
            else:
                del os.environ["TRANSFORMERS_OFFLINE"]
            if orig_hf_offline_const is not None:
                try:
                    import huggingface_hub.constants
                    huggingface_hub.constants.HF_HUB_OFFLINE = orig_hf_offline_const
                except (ImportError, AttributeError):
                    pass

if __name__ == "__main__":
    unittest.main()
