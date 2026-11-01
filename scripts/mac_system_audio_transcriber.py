#!/usr/bin/env python3
"""
scripts/mac_system_audio_transcriber.py

Production CLI for real-time silent macOS meeting & system audio transcription.
Captures internal macOS system audio digitally (via ScreenCaptureKit or BlackHole loopback)
WITHOUT playing sound through laptop speakers and WITHOUT physical microphone pickup.

Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, Apple Silicon Metal GPU)
Inference: `mlx-whisper` (whisper_large_v3-turbo) hardware-accelerated on Apple Metal.

Usage:
  # Auto-detect best zero-speaker source and start live transcription:
  python scripts/mac_system_audio_transcriber.py --project project_nexus --meeting-name "Nexus Architecture Review"

  # List all CoreAudio & AVFoundation devices:
  python scripts/mac_system_audio_transcriber.py --list-devices

  # Test in deterministic headless mock mode with live ASCII VU meter:
  python scripts/mac_system_audio_transcriber.py --mode mock --duration 10
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Union

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from pipeline_a_intake.mac_system_audio import (  # noqa: E402
    MacSystemAudioEngine,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [MAC-TRANSCRIBER] %(message)s",
)
logger = logging.getLogger("mac_system_audio_transcriber")


def render_vu_meter(dbfs: float, width: int = 30) -> str:
    """Renders a dynamic ASCII VU meter with color thresholds."""
    # Scale from -60 dBFS to 0 dBFS
    clamped = max(-60.0, min(0.0, dbfs))
    ratio = (clamped + 60.0) / 60.0
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)

    if clamped > -12.0:
        color = "\033[91m"  # Red (hot/clipping)
    elif clamped > -30.0:
        color = "\033[92m"  # Green (speech zone)
    else:
        color = "\033[90m"  # Dim (noise floor / silence)
    reset = "\033[0m"

    return f"{color}[{bar}] {dbfs:5.1f} dBFS{reset}"


class MacSystemAudioTranscriberApp:
    def __init__(
        self,
        project_name: Optional[str] = None,
        meeting_name: str = "Mac System Audio Session",
        capture_mode: str = "auto",
        device_name: Optional[str] = None,
        whisper_model: str = "mlx-community/whisper_large_v3-turbo",  # noqa: SSOT_EXEMPT
        mock_mode: bool = False,
        enable_ws: bool = True,
        ws_port: int = 8765,
        duration: Optional[float] = None,
        staging_dir: Optional[Union[str, Path]] = None,
    ):
        if not project_name or project_name.lower() == "auto":
            try:
                from dual_pipeline.query_router_core.project_router import (
                    determine_project_directory,
                )
                cand = determine_project_directory({"meeting_name": meeting_name}, allow_llm=False)
                project_name = cand or "UNRESOLVED"
            except Exception:
                project_name = "UNRESOLVED"
        self.project_name = project_name
        self.meeting_name = meeting_name
        self.capture_mode = capture_mode
        self.device_name = device_name
        self.whisper_model = whisper_model
        self.mock_mode = mock_mode
        self.enable_ws = enable_ws
        self.ws_port = ws_port
        self.duration = duration

        from pipeline_a_intake.local_stt import LocalSTTEngine
        from pipeline_a_intake.rolling_persistence import AtomicRollingFileWriter
        from storage.database import DatabaseManager

        self.meeting_date_str = datetime.now().strftime("%m-%d-%Y")
        if staging_dir:
            self.staging_dir = Path(staging_dir).resolve()
        else:
            self.staging_dir = (ROOT_DIR / "output" / self.project_name / self.meeting_date_str).resolve()
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        filename = f"01_{self.meeting_date_str}_system_audio_transcript.md"
        self.rolling_writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=filename,
            meeting_name=self.meeting_name,
            project_name=self.project_name,
            capture_mode=self.capture_mode,
            whisper_model=self.whisper_model,
            classification="internal",
            custom_date=datetime.now().strftime("%m/%d/%Y"),
        )
        self.transcript_path = self.rolling_writer.target_path

        # Initialize persistence & dual-artery fast-path components
        try:
            self.db = DatabaseManager()
        except RuntimeError:
            logger.warning("SQLCipher driver unavailable; enabling dev unencrypted fallback for local transcription session.")
            self.db = DatabaseManager(allow_unencrypted_dev_fallback=True)
        self.ws_server = None
        self.stt_engine = LocalSTTEngine(
            db_manager=self.db,
            model_name=self.whisper_model,
            mock_mode=self.mock_mode,
            ws_server=None,
            session_id=f"{self.project_name}_{self.meeting_date_str}",
        )
        self.splicer = self.stt_engine.audio_splicer

        # Wire live transcript events to terminal and persistence
        self.stt_engine.subscribe(self._on_fast_path_event)

        # Wire Mac system audio engine
        self.audio_engine = MacSystemAudioEngine(
            capture_mode=self.capture_mode,
            target_splicer=self.splicer,
            device_name=self.device_name,
        )

        self._running = False
        self._confirmed_paragraphs: List[str] = []

    def _on_fast_path_event(self, event: Any) -> None:
        """Callback invoked whenever LocalSTTEngine produces a FastPathTranscriptEvent."""
        text = getattr(event, "text", "")
        speaker = getattr(event, "speaker", "Speaker")
        if text:
            self._confirmed_paragraphs.append(text)
            self.rolling_writer.add_segment(text=text, speaker=speaker)
            print(f"\n\033[96m[TRANSCRIPT]\033[0m {text}")

    async def run(self) -> None:
        self._running = True


        print("=" * 70)
        print("  🎙️  macOS Zero-Speaker System Audio Whisper Transcriber")
        print(f"  Target Mode:    {self.capture_mode.upper()}")
        print(f"  Model:          {self.whisper_model}")
        print(f"  Output Path:    {self.transcript_path}")
        print("=" * 70)

        await self.audio_engine.start()

        start_time = time.time()
        try:
            while self._running:
                # Update terminal status line with dynamic VU meter
                dbfs = self.audio_engine.current_rms_dbfs
                vu = render_vu_meter(dbfs, width=24)
                elapsed = time.time() - start_time
                elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))

                sys.stdout.write(f"\r[STATUS] {elapsed_str} | {vu} | Samples: {self.audio_engine.total_samples_captured:,} ")
                sys.stdout.flush()

                if self.duration and elapsed >= self.duration:
                    print("\n[INFO] Configured session duration reached.")
                    break

                await asyncio.sleep(0.1)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        await self.audio_engine.stop()
        self.rolling_writer.close()
        print(f"\n[INFO] Session ended. Full transcript recorded to {self.transcript_path}")



def main() -> None:
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] scripts/mac_system_audio_transcriber.py has been decommissioned.\n"
        "Direct script execution is strictly prohibited by Master Invariant 10.\n"
        "Use the canonical CLI entrypoint instead: 'python nee.py record-meeting ...'\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
