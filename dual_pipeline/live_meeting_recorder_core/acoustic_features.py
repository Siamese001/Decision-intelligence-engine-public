"""
dual_pipeline/live_meeting_recorder_core/acoustic_features.py

Signal processing and representation benchmarking for live meeting recording.
Extracts spectral, pitch, and mel filterbank acoustic signatures from WAV audio,
benchmarks learned speaker representations, and detects intra-chunk speaker switches.
"""
from __future__ import annotations

import logging
import math
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.acoustic_features")


def _read_wav_data(path: Path, max_duration_sec: float) -> Tuple[Optional[np.ndarray], int]:
    """Reads and normalizes raw float32 samples and sample rate from a WAV file."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = min(wf.getnframes(), int(sr * max_duration_sec))
        if n_frames <= 0:
            return None, sr
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw_bytes = wf.readframes(n_frames)

    if sampwidth == 2:
        data = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    elif sampwidth == 4:
        data = np.frombuffer(raw_bytes, dtype=np.int32).astype(np.float32)
    else:
        data = np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) - 128.0

    if n_channels > 1:
        reshaped = data.reshape(-1, n_channels)
        ch_rms = [float(np.sqrt(np.mean(reshaped[:, c] ** 2))) for c in range(n_channels)]
        dom_ch = int(np.argmax(ch_rms))
        data = reshaped[:, dom_ch]

    max_val = np.max(np.abs(data)) if len(data) > 0 else 0.0
    if max_val <= 0.0:
        return None, sr
    return data / max_val, sr


def _estimate_pitch(data: np.ndarray, sr: int) -> float:
    """Estimates fundamental frequency (F0) via autocorrelation on voiced window."""
    corr_len = min(len(data), 4000)
    corr_sig = data[:corr_len]
    corr = np.correlate(corr_sig, corr_sig, mode="full")[corr_len - 1 :]
    d = np.diff(corr)
    start = np.where(d > 0)[0]
    if len(start) == 0:
        return 0.0
    peak = int(np.argmax(corr[start[0] :])) + start[0]
    if peak <= 0:
        return 0.0
    pitch = float(sr / peak)
    return pitch if (50.0 <= pitch <= 600.0) else 0.0


def _compute_mel_energies(data: np.ndarray, fft: np.ndarray, sr: int) -> np.ndarray:
    """Computes 16-band mel filterbank energies."""
    low_mel = 2595.0 * math.log10(1.0 + 100.0 / 700.0)
    high_mel = 2595.0 * math.log10(1.0 + min(sr / 2, 6000.0) / 700.0)
    mel_points = np.linspace(low_mel, high_mel, 18)
    hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
    bin_indices = np.floor((len(data) + 1) * hz_points / sr).astype(int)

    mel_energies = np.zeros(16, dtype=np.float32)
    for m in range(1, 17):
        f_m_minus = bin_indices[m - 1]
        f_m = bin_indices[m]
        f_m_plus = bin_indices[m + 1]
        for k in range(f_m_minus, f_m):
            if k < len(fft):
                mel_energies[m - 1] += fft[k] * (k - f_m_minus) / max(1, f_m - f_m_minus)
        for k in range(f_m, f_m_plus):
            if k < len(fft):
                mel_energies[m - 1] += fft[k] * (f_m_plus - k) / max(1, f_m_plus - f_m)
    return mel_energies


def extract_acoustic_signature(
    wav_path: Union[str, Path],
    sr_target: int = 16000,
    max_duration_sec: float = 15.0,
) -> np.ndarray:
    """
    Extracts normalized multi-dimensional acoustic feature vector from a WAV file.
    Combines:
      - Spectral centroid, bandwidth, rolloff (FFT)
      - Zero crossing rate (ZCR)
      - Fundamental frequency / pitch estimation via autocorrelation
      - 16-band Mel-scale filterbank energy distribution
    Returns unit L2-normalized 21-dimensional feature vector.
    """
    path = Path(wav_path)
    if not path.is_file():
        return np.zeros(21, dtype=np.float32)

    try:
        data, sr = _read_wav_data(path, max_duration_sec)
        if data is None or float(np.sqrt(np.mean(data**2))) < 0.01:
            return np.zeros(21, dtype=np.float32)

        # Spectral analysis
        fft = np.abs(np.fft.rfft(data))
        freqs = np.fft.rfftfreq(len(data), 1.0 / sr)
        total_mag = float(np.sum(fft)) + 1e-9

        centroid = float(np.sum(freqs * fft) / total_mag)
        bandwidth = float(np.sqrt(np.sum(((freqs - centroid) ** 2) * fft) / total_mag))

        cumsum = np.cumsum(fft)
        rolloff_idx = np.searchsorted(cumsum, 0.85 * cumsum[-1])
        rolloff = float(freqs[min(rolloff_idx, len(freqs) - 1)])

        zcr = float(np.sum(np.abs(np.diff(np.sign(data)))) / (2 * len(data)))
        pitch = _estimate_pitch(data, sr)
        mel_energies = _compute_mel_energies(data, fft, sr)
        log_mel = np.log10(mel_energies + 1.0)
        log_mel = (log_mel - np.mean(log_mel)) / (np.std(log_mel) + 1e-6)

        scalar_feats = np.array([
            (centroid - 1000.0) / 500.0,
            (bandwidth - 800.0) / 400.0,
            (rolloff - 1500.0) / 800.0,
            (zcr - 0.05) * 20.0,
            (pitch - 200.0) / 100.0,
        ], dtype=np.float32)

        vec = np.concatenate([scalar_feats, log_mel[:16]])
        # Standardize features (zero-mean, unit variance) to prevent cosine similarity saturation across distinct speakers
        mean = float(np.mean(vec))
        std = float(np.std(vec))
        if std > 1e-6:
            vec = (vec - mean) / std
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)
    except Exception as exc:
        logger.debug("Acoustic signature extraction failed on %s: %s", path.name, exc)
        return np.zeros(21, dtype=np.float32)


def resample_to_16khz(data: np.ndarray, orig_sr: int, target_sr: int = 16000) -> np.ndarray:
    """Resamples 1D float32 audio (e.g. 44.1 kHz or 48 kHz) to 16 kHz mono via linear interpolation."""
    if orig_sr <= 0 or orig_sr == target_sr or len(data) == 0:
        return data.astype(np.float32)
    target_len = max(1, int(round(len(data) * float(target_sr) / float(orig_sr))))
    x_old = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, data).astype(np.float32)


def _load_16khz_samples_for_embedding(
    wav_path_or_samples: Union[str, Path, np.ndarray],
    sr: int,
    max_duration_sec: float,
) -> Optional[np.ndarray]:
    if isinstance(wav_path_or_samples, (str, Path)):
        path = Path(wav_path_or_samples)
        if not path.is_file():
            return None
        try:
            data, sr = _read_wav_data(path, max_duration_sec)
        except Exception:
            return None
        if data is None or len(data) == 0:
            return None
    else:
        data = np.asarray(wav_path_or_samples, dtype=np.float32).ravel()
        if len(data) == 0:
            return None
    return resample_to_16khz(data, sr, 16000) if sr != 16000 else data


def extract_256d_acoustic_embedding(
    wav_path_or_samples: Union[str, Path, np.ndarray],
    sr: int = 16000,
    max_duration_sec: float = 15.0,
) -> np.ndarray:
    """Extracts a unit-normalized 256-D acoustic embedding at 16 kHz for unified live/batch matching."""
    data = _load_16khz_samples_for_embedding(wav_path_or_samples, sr, max_duration_sec)
    if data is None or float(np.sqrt(np.mean(data ** 2))) < 0.005:
        return np.zeros(256, dtype=np.float32)

    n_win = max(1, len(data) // 512)
    framed = data[: n_win * 512].reshape(n_win, 512) if len(data) >= 512 else np.pad(data, (0, 512 - len(data))).reshape(1, 512)
    spec_mag = np.mean(np.abs(np.fft.rfft(framed, n=512, axis=1))[:, :256], axis=0).astype(np.float32)
    log_spec = np.log1p(spec_mag)
    std = float(np.std(log_spec))
    if std > 1e-6:
        log_spec = (log_spec - float(np.mean(log_spec))) / std
    norm = float(np.linalg.norm(log_spec))
    return (log_spec / norm).astype(np.float32) if norm > 1e-6 else np.zeros(256, dtype=np.float32)


def align_acoustic_dimension(
    vec: np.ndarray,
    target_dim: int = 256,
    allow_legacy_projection: bool = False,
) -> np.ndarray:
    """Validates or aligns an acoustic vector into target_dim unit-norm space without destructive 256D->21D pooling."""
    arr = np.asarray(vec, dtype=np.float32).ravel()
    if len(arr) == target_dim:
        norm = float(np.linalg.norm(arr))
        return (arr / norm).astype(np.float32) if norm > 1e-6 else arr
    if len(arr) == 0:
        return np.zeros(target_dim, dtype=np.float32)
    if not allow_legacy_projection and ((len(arr) == 256 and target_dim == 21) or (len(arr) == 21 and target_dim == 256)):
        raise ValueError(
            f"Acoustic dimension mismatch: refusing to project {len(arr)}-D vector into {target_dim}-D space"
        )
    if len(arr) > target_dim:
        proj = np.array([float(np.mean(b)) for b in np.array_split(arr, target_dim)], dtype=np.float32)
    else:
        proj = np.pad(arr, (0, target_dim - len(arr)), mode="wrap").astype(np.float32)
    std = float(np.std(proj))
    if std > 1e-6:
        proj = (proj - float(np.mean(proj))) / std
    norm = float(np.linalg.norm(proj))
    return (proj / norm).astype(np.float32) if norm > 1e-6 else proj


def extract_subchunk_signatures(
    wav_path: Union[str, Path],
    window_sec: float = 3.0,
    max_duration_sec: float = 15.0,
) -> List[np.ndarray]:
    """Extracts sequence of acoustic feature signatures over sliding sub-chunk windows."""
    path = Path(wav_path)
    if not path.is_file():
        return []
    try:
        data, sr = _read_wav_data(path, max_duration_sec)
        if data is None or len(data) == 0:
            return []
        window_samples = int(sr * window_sec)
        if window_samples <= 0:
            return []
        signatures: List[np.ndarray] = []
        n_samples = len(data)
        for start_idx in range(0, n_samples, window_samples):
            end_idx = min(start_idx + window_samples, n_samples)
            sub_data = data[start_idx:end_idx]
            if len(sub_data) < int(sr * 0.5):
                continue
            if float(np.sqrt(np.mean(sub_data**2))) < 0.01:
                continue
            fft = np.abs(np.fft.rfft(sub_data))
            freqs = np.fft.rfftfreq(len(sub_data), 1.0 / sr)
            total_mag = float(np.sum(fft)) + 1e-9
            centroid = float(np.sum(freqs * fft) / total_mag)
            bandwidth = float(np.sqrt(np.sum(((freqs - centroid) ** 2) * fft) / total_mag))
            cumsum = np.cumsum(fft)
            rolloff_idx = np.searchsorted(cumsum, 0.85 * cumsum[-1])
            rolloff = float(freqs[min(rolloff_idx, len(freqs) - 1)])
            zcr = float(np.sum(np.abs(np.diff(np.sign(sub_data)))) / (2 * len(sub_data)))
            pitch = _estimate_pitch(sub_data, sr)
            mel_energies = _compute_mel_energies(sub_data, fft, sr)
            log_mel = np.log10(mel_energies + 1.0)
            log_mel = (log_mel - np.mean(log_mel)) / (np.std(log_mel) + 1e-6)

            scalar_feats = np.array([
                (centroid - 1000.0) / 500.0,
                (bandwidth - 800.0) / 400.0,
                (rolloff - 1500.0) / 800.0,
                (zcr - 0.05) * 20.0,
                (pitch - 200.0) / 100.0,
            ], dtype=np.float32)

            vec = np.concatenate([scalar_feats, log_mel[:16]])
            mean = float(np.mean(vec))
            std = float(np.std(vec))
            if std > 1e-6:
                vec = (vec - mean) / std
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
            signatures.append(vec.astype(np.float32))
        return signatures
    except Exception as exc:
        logger.debug("extract_subchunk_signatures failed on %s: %s", path.name, exc)
        return []


def benchmark_acoustic_representations(
    wav_path: Union[str, Path],
    learned_vector: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Benchmarks learned high-dimensional speaker representations against the 21d handcrafted signature.
    Calculates cosine separation, energy variance, and feature dimension statistics.
    """
    handcrafted = extract_acoustic_signature(wav_path)
    norm_handcrafted = float(np.linalg.norm(handcrafted))

    result: Dict[str, Any] = {
        "handcrafted_dim": len(handcrafted),
        "handcrafted_norm": round(norm_handcrafted, 4),
        "handcrafted_mean_energy": round(float(np.mean(np.abs(handcrafted))), 4),
        "has_learned_vector": learned_vector is not None,
        "benchmark_status": "COMPLETED",
    }

    if learned_vector is not None:
        lv = np.array(learned_vector, dtype=np.float32)
        norm_lv = float(np.linalg.norm(lv))
        result["learned_dim"] = len(lv)
        result["learned_norm"] = round(norm_lv, 4)
        if len(lv) == len(handcrafted) and norm_lv > 1e-6 and norm_handcrafted > 1e-6:
            cos_sim = float(np.dot(handcrafted, lv) / (norm_handcrafted * norm_lv))
            result["cosine_similarity"] = round(cos_sim, 4)
        else:
            # Different dimensionality: report dimension projection ratio
            result["dim_expansion_ratio"] = round(len(lv) / len(handcrafted), 2)

    return result


def detect_intra_chunk_switches(
    sub_features: List[np.ndarray],
    distance_threshold: Optional[float] = None,
) -> List[int]:
    """
    Identifies acoustic switch points within a sequence of sub-chunk feature vectors.
    Returns indices of feature transitions where cosine distance exceeds distance_threshold.
    """
    from dual_pipeline.parameters import get_hitl_divergence_margin_threshold

    eff_threshold = distance_threshold if distance_threshold is not None else get_hitl_divergence_margin_threshold()
    switches: List[int] = []
    if len(sub_features) < 2:
        return switches

    for i in range(1, len(sub_features)):
        v_prev = sub_features[i - 1]
        v_curr = sub_features[i]
        n_prev = float(np.linalg.norm(v_prev))
        n_curr = float(np.linalg.norm(v_curr))
        if n_prev > 1e-6 and n_curr > 1e-6:
            sim = float(np.dot(v_prev, v_curr) / (n_prev * n_curr))
            dist = 1.0 - sim
            if dist >= eff_threshold:
                switches.append(i)

    return switches


def write_acoustic_evidence_receipt(
    *,
    voice_registry: Any,
    session_tag: str,
    clean_slug: str,
    processed_chunks_count: int,
    attendees: List[str],
    stop_timestamp: str,
    session_manifest_path: Path,
    receipt_path: Path,
    cache_dir: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generates and persists the acoustic evidence receipt and detects capture collapse."""
    import json
    collapse_info = (
        voice_registry.detect_capture_collapse()
        if voice_registry and hasattr(voice_registry, "detect_capture_collapse")
        else {"is_collapsed": False, "dominant_share": 0.0, "total_chunks": 0}
    )
    if collapse_info.get("is_collapsed"):
        logger.warning(
            "[COLLAPSE-WARNING] Single-speaker collapse detected during capture: %s",
            collapse_info.get("warning"),
        )

    receipt_data = {
        "receipt_type": "acoustic_evidence_receipt",
        "recording_completed": True,
        "attribution_completed": False,
        "attribution_status": (
            "COLLAPSED_PENDING_ATTRIBUTION"
            if collapse_info.get("is_collapsed")
            else "PENDING_ATTRIBUTION"
        ),
        "session_tag": session_tag,
        "clean_slug": clean_slug,
        "processed_chunks": processed_chunks_count,
        "attendees": attendees,
        "collapse_detection": collapse_info,
        "clusters": voice_registry.to_dict() if voice_registry and hasattr(voice_registry, "to_dict") else {},
        "timestamp": stop_timestamp,
        "manifest_path": str(session_manifest_path),
    }
    try:
        receipt_bytes = json.dumps(receipt_data, indent=2)
        receipt_path.write_text(receipt_bytes, encoding="utf-8")
        (cache_dir / "acoustic_evidence_receipt.json").write_text(receipt_bytes, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not persist acoustic evidence receipt: %s", exc)

    return collapse_info, receipt_data


def compute_channel_rms(
    wav_path: Union[str, Path],
    max_duration_sec: float = 15.0,
) -> Tuple[float, ...]:
    """Computes per-channel RMS for a WAV file.
    Returns tuple of RMS values: (rms_ch0, rms_ch1) for stereo, or (rms_ch0,) for mono.
    """
    path = Path(wav_path)
    if not path.exists():
        return (0.0,)

    try:
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            n_frames = min(wf.getnframes(), int(sr * max_duration_sec))
            if n_frames <= 0:
                return (0.0,)
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            raw_bytes = wf.readframes(n_frames)

        if sampwidth == 2:
            data = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
        elif sampwidth == 4:
            data = np.frombuffer(raw_bytes, dtype=np.int32).astype(np.float32)
        else:
            data = np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) - 128.0

        if n_channels <= 1:
            rms = float(np.sqrt(np.mean(data ** 2))) if len(data) > 0 else 0.0
            return (round(rms, 4),)

        reshaped = data.reshape(-1, n_channels)
        channel_rms: List[float] = []
        for ch in range(n_channels):
            ch_data = reshaped[:, ch]
            ch_rms = float(np.sqrt(np.mean(ch_data ** 2))) if len(ch_data) > 0 else 0.0
            channel_rms.append(round(ch_rms, 4))
        return tuple(channel_rms)
    except Exception as exc:
        logger.debug("compute_channel_rms failed on %s: %s", path.name, exc)
        return (0.0,)


def compute_channel_rms_ratio(
    wav_path: Union[str, Path],
    max_duration_sec: float = 15.0,
) -> Tuple[float, float]:
    """Computes (rms_ch0, rms_ch1) for stereo audio.
    In stereo capture:
      Channel 0 = Remote SCK (system / participants)
      Channel 1 = Local AVFoundation Microphone (operator / host)
    For mono audio, returns (rms, 0.0).
    """
    channels = compute_channel_rms(wav_path, max_duration_sec=max_duration_sec)
    if len(channels) >= 2:
        return (channels[0], channels[1])
    if len(channels) == 1:
        return (channels[0], 0.0)
    return (0.0, 0.0)
