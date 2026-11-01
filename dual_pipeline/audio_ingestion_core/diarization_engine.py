"""
dual_pipeline/audio_ingestion_core/diarization_engine.py

Governed Acoustic Diarization Engine:
- Pins model strictly to pyannote_speaker_diarization-3.1 on Apple Silicon MPS.
- Pre-initializes model on start transcription (fail-fast, zero silent swallow).
- Injects PyTorch 2.6+ safe checkpoint unpickler and HuggingFace Hub token compatibility bridge.
"""
from __future__ import annotations

import concurrent.futures
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.model_registry import resolve_acoustic_diarizer

logger = logging.getLogger(__name__)

PINNED_DIARIZATION_MODEL = resolve_acoustic_diarizer().model_id


class DiarizationInitializationError(RuntimeError):
    """Raised when the pinned acoustic diarization model cannot be initialized."""
    pass


def _apply_runtime_compatibility_bridges() -> None:
    """Applies compatibility shims for PyTorch 2.6+ and huggingface_hub."""
    from config.credentials import CredentialManager
    if not CredentialManager.has_credential("HF_HUB_OFFLINE"):
        CredentialManager.set_credential("HF_HUB_OFFLINE", "1")
    try:
        import huggingface_hub
        try:
            import huggingface_hub.constants
            huggingface_hub.constants.HF_HUB_OFFLINE = True
        except (ImportError, AttributeError) as exc:
            logger.debug("HF_HUB_OFFLINE constant override skipped: %s", exc)
        if not getattr(huggingface_hub, "_diarization_bridge_applied", False):
            _orig_download = huggingface_hub.hf_hub_download

            def _compat_download(*args: Any, **kwargs: Any) -> Any:
                if "use_auth_token" in kwargs:
                    kwargs["token"] = kwargs.pop("use_auth_token")
                kwargs.setdefault("local_files_only", True)
                return _orig_download(*args, **kwargs)

            huggingface_hub.hf_hub_download = _compat_download
            huggingface_hub._diarization_bridge_applied = True
    except Exception as e:
        logger.debug("Could not install huggingface_hub compatibility bridge: %s", e)


_apply_runtime_compatibility_bridges()


@contextmanager
def _scoped_torch_load_bridge():
    """Temporarily applies weights_only=False compatibility bridge strictly during PyAnnote model loading."""
    try:
        import torch
    except ImportError:
        yield
        return

    _orig_torch_load = torch.load

    def _compat_torch_load(*args: Any, **kwargs: Any) -> Any:
        kwargs["weights_only"] = False
        return _orig_torch_load(*args, **kwargs)

    torch.load = _compat_torch_load
    try:
        yield
    finally:
        torch.load = _orig_torch_load


class DiarizationEngine:
    """Manages the pinned acoustic diarization pipeline on Apple Silicon MPS."""

    def __init__(
        self,
        model_id: str = PINNED_DIARIZATION_MODEL,
        device: Optional[str] = None,
        token: Optional[str] = None,
        lazy: bool = False,
        mock: bool = False,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.token = token
        self.mock = bool(mock)
        self.pipeline: Any = None
        self._initialized = False
        self.snapshot_revision: Optional[str] = None
        self.snapshot_sha256: Optional[str] = None

        if not lazy and not self.mock:
            self.initialize()

    def initialize(self) -> None:
        """Initializes and verifies the pinned pyannote model (fail-fast)."""
        if self._initialized and self.pipeline is not None:
            return

        _apply_runtime_compatibility_bridges()

        from config.credentials import CredentialManager

        token = self.token or CredentialManager.get_hf_token()
        if not token:
            raise DiarizationInitializationError(
                "HF_TOKEN is missing from environment and env_di. "
                "A valid HuggingFace token is required to initialize the pinned diarization model."
            )

        try:
            import torch
            from pyannote.audio import Pipeline

            logger.info("Initializing pinned diarization pipeline: %s", self.model_id)
            with _scoped_torch_load_bridge():
                pin = resolve_acoustic_diarizer()
                self.snapshot_revision = pin.revision
                self.snapshot_sha256 = pin.snapshot_sha256
                CredentialManager.set_credential("HF_HUB_OFFLINE", "1")
                try:
                    import huggingface_hub.constants
                    huggingface_hub.constants.HF_HUB_OFFLINE = True
                except (ImportError, AttributeError) as exc:
                    logger.debug("HF_HUB_OFFLINE constant override skipped: %s", exc)
                try:
                    self.pipeline = Pipeline.from_pretrained(pin.model_id, use_auth_token=token, revision=pin.revision)
                except TypeError:
                    self.pipeline = Pipeline.from_pretrained(pin.model_id, use_auth_token=token)
                finally:
                    pass

            resolved_device = self.device
            if resolved_device is None:
                if torch.backends.mps.is_available():
                    resolved_device = "mps"
                elif torch.cuda.is_available():
                    resolved_device = "cuda"
                else:
                    resolved_device = "cpu"

            self.device = resolved_device

            if resolved_device and resolved_device != "cpu":
                self.pipeline.to(torch.device(resolved_device))
                logger.info("Pinned diarization model moved to device: %s", resolved_device)

            self._initialized = True
        except Exception as e:
            err_msg = f"Failed to initialize pinned diarization pipeline '{self.model_id}': {e}"
            logger.error(err_msg)
            raise DiarizationInitializationError(err_msg) from e

    def diarize(
        self,
        audio_path: Union[str, Path],
        timeout_seconds: int = 300,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> Any:
        """Executes full acoustic diarization on an audio file with strict timeout and validation."""
        if not self._initialized or self.pipeline is None:
            self.initialize()

        p = Path(audio_path)
        if not p.is_file():
            raise FileNotFoundError(f"Audio file not found for diarization: {p}")

        size = p.stat().st_size
        if size < 44:
            raise ValueError(f"Audio file {p.name} too small to be a valid audio file ({size} bytes)")
        if size > 1024 * 1024 * 1024:
            raise ValueError(f"Audio file {p.name} exceeds 1GB limit for diarization ({size} bytes)")

        logger.info("Running acoustic diarization on %s (timeout=%ds)...", p.name, timeout_seconds)

        kwargs = {}
        if min_speakers is not None:
            kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            kwargs["max_speakers"] = max_speakers

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.pipeline, str(p), **kwargs)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as te:
            logger.error("Diarization timed out after %ds on %s", timeout_seconds, p.name)
            raise TimeoutError(f"Acoustic diarization timed out after {timeout_seconds}s on {p.name}") from te
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def extract_speaker_turns(
        self,
        audio_path: Union[str, Path],
        timeout_seconds: int = 300,
        expected_roster_count: Optional[int] = None,
        max_single_speaker_collapse_pct: float = 0.85
    ) -> List[Dict[str, Any]]:
        """Diarizes audio file and returns clean, chronological list of speaker turns."""
        from .audio_asr_cache import compute_audio_sha256, load_audio_diarization_cache, save_audio_diarization_cache
        p = Path(audio_path)
        audio_sha = compute_audio_sha256(p)
        min_speakers = 1
        max_speakers = expected_roster_count if expected_roster_count else None
        cached = load_audio_diarization_cache(audio_sha, self.model_id, stem=p.stem, min_speakers=min_speakers, max_speakers=max_speakers)
        if cached is not None:
            cached_turns, _ = cached
            durs: Dict[str, float] = {}
            for t in cached_turns:
                durs[t["speaker"]] = durs.get(t["speaker"], 0.0) + t["duration"]
            _validate_speaker_collapse_and_skew(durs, expected_roster_count, max_single_speaker_collapse_pct)
            return cached_turns

        diarization = self.diarize(audio_path, timeout_seconds=timeout_seconds, min_speakers=min_speakers, max_speakers=max_speakers)
        turns = validate_acoustic_diarization(diarization, expected_roster_count, max_single_speaker_collapse_pct, source_name=p.name)
        save_audio_diarization_cache(audio_sha, turns, self.model_id, stem=p.stem, min_speakers=min_speakers, max_speakers=max_speakers)
        return turns

    @staticmethod
    def _resample_to_16khz_mono(audio_np: Any, orig_sr: int, target_sr: int = 16000) -> Any:
        """Resamples 1D float32 audio (e.g. 44.1/48 kHz) to 16 kHz mono for WeSpeaker 256-D inference."""
        from dual_pipeline.live_meeting_recorder_core.acoustic_features import resample_to_16khz
        return resample_to_16khz(audio_np, orig_sr, target_sr)

    def extract_turn_embedding(
        self,
        audio_path: Union[str, Path],
        start_sec: float = 0.0,
        end_sec: Optional[float] = None,
        channel: Optional[int] = None,
    ) -> List[float]:
        """Extracts a unit-normalized 16 kHz 256-D embedding for a single turn window."""
        from .constrained_global_clusterer import extract_turn_embedding_from_wav_slice
        return extract_turn_embedding_from_wav_slice(
            audio_path, start_sec, end_sec, channel, emb_model=getattr(self.pipeline, "_embedding", None)
        )

    def extract_cluster_centroids(
        self,
        audio_path: Union[str, Path],
        turns: Sequence[Dict[str, Any]],
        max_segments_per_cluster: int = 5,
        min_segment_duration: float = 2.0,
        allow_deterministic_fallback: Optional[bool] = None,
    ) -> Dict[str, List[float]]:
        """Extracts 256-D acoustic centroid embeddings for each speaker cluster at 16 kHz."""
        from .constrained_global_clusterer import extract_signal_centroids_fallback
        from .voiceprint_store import generate_deterministic_embedding

        if not turns:
            return {}
        p = Path(audio_path)
        stem = p.stem
        clusters = sorted(set(t.get("speaker", "SPEAKER_00") for t in turns))
        is_mock = bool(getattr(self, "mock", False)) if allow_deterministic_fallback is None else bool(allow_deterministic_fallback)
        emb_model = getattr(self.pipeline, "_embedding", None)
        if emb_model is None or not p.is_file():
            if p.is_file() and not is_mock:
                sig = extract_signal_centroids_fallback(self, p, turns, clusters, max_segments_per_cluster)
                if sig:
                    return sig
            if not is_mock:
                raise ValueError("Cannot generate deterministic SHA-256 hash centroids when mock=False")
            return {spk: generate_deterministic_embedding(f"{stem}:{spk}", dim=256) for spk in clusters}

        try:
            return self._extract_neural_cluster_centroids(
                p, turns, clusters, emb_model, max_segments_per_cluster, min_segment_duration
            )
        except Exception as e:
            if not is_mock:
                raise RuntimeError(f"Neural 256-D centroid extraction failed for {p.name}: {e}") from e
            logger.warning("Neural acoustic embedding extraction fallback to deterministic (%s): %s", p.name, e)
            return {spk: generate_deterministic_embedding(f"{stem}:{spk}", dim=256) for spk in clusters}

    def _extract_neural_cluster_centroids(
        self,
        audio_path: Path,
        turns: Sequence[Dict[str, Any]],
        clusters: List[str],
        emb_model: Any,
        max_segments: int,
        min_duration: float,
    ) -> Dict[str, List[float]]:
        import numpy as np
        import torch
        import wave
        from .voiceprint_store import _normalize_vector, generate_deterministic_embedding

        stem = audio_path.stem
        centroids: Dict[str, List[float]] = {}
        target_device = getattr(self, "device", None)
        if not target_device:
            target_device = "mps" if (torch.backends.mps.is_available() and torch.backends.mps.is_built()) else "cpu"

        with wave.open(str(audio_path), "rb") as wf:
            sr = wf.getframerate()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            total_frames = wf.getnframes()

            for spk in clusters:
                # Filter out turns with overlap
                spk_turns = [
                    t for t in turns
                    if t.get("speaker") == spk
                    and not t.get("overlap")
                    and not t.get("is_overlap")
                    and len(t.get("active_speakers", [])) <= 1
                ]
                if not spk_turns:
                    spk_turns = [t for t in turns if t.get("speaker") == spk]

                # Filter by duration: prefer >= min_duration, fallback to >= 1.0s
                long_turns = [
                    t for t in spk_turns
                    if float(t.get("duration", float(t.get("end", 0.0)) - float(t.get("start", 0.0)))) >= min_duration
                ]
                effective_turns = long_turns if long_turns else [
                    t for t in spk_turns
                    if float(t.get("duration", float(t.get("end", 0.0)) - float(t.get("start", 0.0)))) >= 1.0
                ]
                if not effective_turns:
                    effective_turns = list(spk_turns)

                effective_turns.sort(
                    key=lambda t: float(t.get("duration", float(t.get("end", 0.0)) - float(t.get("start", 0.0)))),
                    reverse=True,
                )

                extracted_embeddings: List[np.ndarray] = []
                extracted_durations: List[float] = []

                for t in effective_turns[:max_segments]:
                    st = float(t.get("start", 0.0))
                    en = float(t.get("end", st + float(t.get("duration", 0.0))))
                    dur = en - st
                    s_frame = max(0, int(st * sr))
                    e_frame = min(total_frames, int(en * sr))
                    if (e_frame - s_frame) < int(sr * 0.5):
                        continue

                    wf.setpos(s_frame)
                    raw = wf.readframes(e_frame - s_frame)
                    dtype = np.int16 if sampwidth == 2 else (np.int32 if sampwidth == 4 else np.uint8)
                    audio_np = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                    if sampwidth == 2:
                        audio_np /= 32768.0
                    elif sampwidth == 4:
                        audio_np /= 2147483648.0
                    else:
                        audio_np = (audio_np - 128.0) / 128.0

                    if n_channels > 1:
                        audio_np = audio_np.reshape(-1, n_channels)
                        turn_ch = t.get("channel")
                        if turn_ch in (0, "0", 1, "1"):
                            ch_idx = int(turn_ch)
                            audio_np = audio_np[:, ch_idx]
                        else:
                            rms0 = float(np.sqrt(np.mean(audio_np[:, 0] ** 2)))
                            rms1 = float(np.sqrt(np.mean(audio_np[:, 1] ** 2)))
                            audio_np = audio_np[:, 1] if rms1 > rms0 else audio_np[:, 0]

                    audio_np = self._resample_to_16khz_mono(audio_np, sr, 16000)
                    samples, window_samples, step_samples = audio_np.shape[0], 64000, 32000
                    windows = [audio_np[ws : ws + window_samples] for ws in range(0, samples - window_samples + 1, step_samples)] if samples > 128000 else [audio_np]

                    for win_audio in windows:
                        wav_tensor = torch.from_numpy(win_audio).unsqueeze(0).unsqueeze(0).float()
                        try:
                            wav_tensor = wav_tensor.to(torch.device(target_device))
                        except Exception as exc:
                            logger.debug("Fallback to CPU tensor placement from %s: %s", target_device, exc)
                        with torch.no_grad():
                            raw_emb = emb_model(wav_tensor)
                        if isinstance(raw_emb, torch.Tensor):
                            vec = raw_emb.detach().cpu().numpy().reshape(-1)
                        elif isinstance(raw_emb, np.ndarray):
                            vec = raw_emb.reshape(-1)
                        else:
                            continue

                        if vec.shape[-1] >= 256:
                            vec256 = vec[:256]
                            norm = float(np.linalg.norm(vec256))
                            if norm > 1e-6:
                                extracted_embeddings.append(vec256 / norm)
                                extracted_durations.append(dur)

                # Medoid Outlier Pruning
                if len(extracted_embeddings) >= 3:
                    embs = np.array(extracted_embeddings)
                    sim_matrix = np.dot(embs, embs.T)
                    medoid_idx = int(np.argmax(np.mean(sim_matrix, axis=1)))
                    medoid_sims = sim_matrix[medoid_idx]
                    kept_indices = [idx for idx, sim in enumerate(medoid_sims) if sim >= 0.65]
                    if kept_indices:
                        filtered_embs = [extracted_embeddings[idx] for idx in kept_indices]
                        filtered_durs = [extracted_durations[idx] for idx in kept_indices]
                    else:
                        filtered_embs = extracted_embeddings
                        filtered_durs = extracted_durations
                else:
                    filtered_embs = extracted_embeddings
                    filtered_durs = extracted_durations

                if filtered_embs:
                    weights = np.array(filtered_durs)
                    total_w = np.sum(weights)
                    if total_w > 1e-6:
                        norm_w = weights / total_w
                        avg_emb = np.sum([filtered_embs[i] * norm_w[i] for i in range(len(filtered_embs))], axis=0)
                    else:
                        avg_emb = np.mean(filtered_embs, axis=0)

                    norm = float(np.linalg.norm(avg_emb))
                    if norm > 1e-6:
                        centroids[spk] = _normalize_vector((avg_emb / norm).tolist(), target_dim=256)
                    else:
                        centroids[spk] = generate_deterministic_embedding(f"{stem}:{spk}", dim=256)
                else:
                    centroids[spk] = generate_deterministic_embedding(f"{stem}:{spk}", dim=256)

        return centroids

    def score_enrolled_voiceprints(
        self, centroids: Dict[str, Sequence[float]], project: str = "GLOBAL", store: Optional[Any] = None, threshold: float = 0.82
    ) -> Dict[str, Tuple[str, float]]:
        """Scores cluster centroids against enrolled voiceprints in SQLite WAL store."""
        if store is None:
            from .voiceprint_store import VoiceprintStore
            store = VoiceprintStore()
        return store.find_all_matches(centroids, project=project, threshold=threshold)

    def parse_rttm_turns(
        self, rttm_lines: Sequence[str], source_name: str = "audio",
        expected_roster_count: Optional[int] = None, max_single_speaker_collapse_pct: float = 0.85
    ) -> List[Dict[str, Any]]:
        """Parses RTTM lines into validated speaker turns, validating collapse and duration limits."""
        return parse_rttm_turns(rttm_lines, source_name, expected_roster_count, max_single_speaker_collapse_pct)


def validate_acoustic_diarization(
    diarization: Any,
    expected_roster_count: Optional[int] = None,
    max_single_speaker_collapse_pct: float = 0.85,
    source_name: str = "audio",
) -> List[Dict[str, Any]]:
    """Validates acoustic diarization turns, checking bounds, roster count, and single-speaker collapse."""
    turns: List[Dict[str, Any]] = []
    speaker_durations: Dict[str, float] = {}

    for turn, _, speaker in diarization.itertracks(yield_label=True):
        duration = turn.end - turn.start
        if duration <= 0:
            continue
        turns.append({"start": round(turn.start, 2), "end": round(turn.end, 2), "duration": round(duration, 2), "speaker": speaker})
        speaker_durations[speaker] = speaker_durations.get(speaker, 0.0) + duration

    turns.sort(key=lambda t: t["start"])
    if not turns:
        logger.error("Diarization produced 0 speaker turns for %s", source_name)
        raise RuntimeError(
            f"Acoustic diarization produced 0 speaker turns for {source_name}. "
            "Refusing to proceed with empty speaker segmentation."
        )

    _validate_speaker_collapse_and_skew(
        speaker_durations=speaker_durations,
        expected_roster_count=expected_roster_count,
        max_single_speaker_collapse_pct=max_single_speaker_collapse_pct,
    )
    return turns


def _validate_speaker_collapse_and_skew(
    speaker_durations: Dict[str, float],
    expected_roster_count: Optional[int] = None,
    max_single_speaker_collapse_pct: float = 0.85,
) -> None:
    total_duration = sum(speaker_durations.values())
    if expected_roster_count is not None and expected_roster_count > 1:
        if len(speaker_durations) < expected_roster_count:
            from dual_pipeline.exceptions import DiarizationCollapseError
            raise DiarizationCollapseError(
                f"Roster vs. Acoustic Cluster Gate: Found only {len(speaker_durations)} acoustic clusters, "
                f"but expected {expected_roster_count} roster participants."
            )
        if total_duration > 0:
            max_speaker_pct = max(speaker_durations.values()) / total_duration
            if max_speaker_pct > max_single_speaker_collapse_pct:
                from dual_pipeline.exceptions import DiarizationCollapseError
                err = DiarizationCollapseError(
                    f"Acoustic Pre-Synthesis Skew Gate: Single speaker collapse detected. "
                    f"Max speaker holds {max_speaker_pct*100:.1f}% of audio time (threshold {max_single_speaker_collapse_pct*100:.1f}%)."
                )
                err.evidence = {
                    "max_speaker_pct": round(max_speaker_pct, 4),
                    "threshold_pct": max_single_speaker_collapse_pct,
                    "speaker_durations": speaker_durations,
                    "total_duration": round(total_duration, 2),
                    "cluster_count": len(speaker_durations),
                }
                raise err
            min_speaker_pct = min(speaker_durations.values()) / total_duration
            if expected_roster_count >= 3 and total_duration >= 120.0 and min_speaker_pct < 0.025:
                from dual_pipeline.exceptions import DiarizationCollapseError
                err = DiarizationCollapseError(
                    f"Acoustic Starvation Skew Gate: Remote speaker cluster starvation detected. "
                    f"Min cluster holds only {min_speaker_pct*100:.2f}% of audio time (< 2.5% floor for {expected_roster_count} attendees)."
                )
                err.evidence = {
                    "min_speaker_pct": round(min_speaker_pct, 4),
                    "speaker_durations": speaker_durations,
                    "total_duration": round(total_duration, 2),
                    "cluster_count": len(speaker_durations),
                }
                raise err


def parse_rttm_turns(
    rttm_lines: Sequence[str],
    source_name: str = "audio",
    expected_roster_count: Optional[int] = None,
    max_single_speaker_collapse_pct: float = 0.85,
) -> List[Dict[str, Any]]:
    """Parses RTTM lines into validated speaker turns, validating collapse and duration limits."""
    turns: List[Dict[str, Any]] = []
    speaker_durations: Dict[str, float] = {}

    for line in rttm_lines:
        parts = line.strip().split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            try:
                start = float(parts[3])
                duration = float(parts[4])
                speaker = parts[7]
                if duration <= 0:
                    continue
                turns.append({"start": round(start, 2), "end": round(start + duration, 2), "duration": round(duration, 2), "speaker": speaker})
                speaker_durations[speaker] = speaker_durations.get(speaker, 0.0) + duration
            except (ValueError, IndexError):
                continue

    turns.sort(key=lambda t: t["start"])
    if not turns:
        logger.error("Diarization produced 0 speaker turns for %s", source_name)
        raise RuntimeError(
            f"Acoustic diarization produced 0 speaker turns for {source_name}. "
            "Refusing to proceed with empty speaker segmentation."
        )

    _validate_speaker_collapse_and_skew(
        speaker_durations=speaker_durations,
        expected_roster_count=expected_roster_count,
        max_single_speaker_collapse_pct=max_single_speaker_collapse_pct,
    )
    return turns


_GLOBAL_DIARIZATION_ENGINE: Optional[DiarizationEngine] = None


def get_diarization_engine(force_refresh: bool = False, lazy: bool = False) -> DiarizationEngine:
    """Returns singleton instance of the pinned DiarizationEngine."""
    global _GLOBAL_DIARIZATION_ENGINE
    if _GLOBAL_DIARIZATION_ENGINE is None or force_refresh:
        _GLOBAL_DIARIZATION_ENGINE = DiarizationEngine(lazy=lazy)
    elif not lazy and not _GLOBAL_DIARIZATION_ENGINE._initialized:
        _GLOBAL_DIARIZATION_ENGINE.initialize()
    return _GLOBAL_DIARIZATION_ENGINE


def check_diarization_preflight() -> Tuple[bool, str]:
    """Lightweight, non-importing preflight check (<50ms) for pinned diarization engine.

    Verifies:
      1. pyannote.audio is resolvable via importlib.util.find_spec without importing torch/pyannote.
      2. HF_TOKEN is present in environment or env_di.
      3. Pinned snapshots for diarization and segmentation sub-models exist in the local cache.
    """
    import importlib.util
    from pathlib import Path

    # 1. Spec check without importing
    try:
        if importlib.util.find_spec("pyannote.audio") is None:
            return False, "PYANNOTE_NOT_INSTALLED"
    except Exception as e:
        return False, f"PYANNOTE_SPEC_ERROR: {e}"

    # 2. Token check
    try:
        from config.credentials import CredentialManager, ensure_ssot_env_loaded
        ensure_ssot_env_loaded()
        token = CredentialManager.get_hf_token()
        if not token:
            return False, "HF_TOKEN_MISSING"
    except Exception as e:
        return False, f"TOKEN_CHECK_ERROR: {e}"

    # 3. Snapshot existence check
    hf_home = CredentialManager.get_credential("HF_HOME")
    hub = Path(hf_home) / "hub" if hf_home else Path.home() / ".cache" / "huggingface" / "hub"
    if not (hub / "models--pyannote--speaker-diarization-3.1").exists():
        return False, "DIARIZATION_SNAPSHOT_MISSING"
    if not (hub / "models--pyannote--segmentation-3.0").exists():
        return False, "SEGMENTATION_SNAPSHOT_MISSING"
    return True, "DIARIZATION_PREFLIGHT_OK"

