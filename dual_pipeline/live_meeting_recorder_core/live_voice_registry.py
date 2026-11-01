"""
dual_pipeline/live_meeting_recorder_core/live_voice_registry.py

Rolling Live Voice Diarization & Persistent Voice-Embedding Registry.
Maintains consistent voice cluster labels ('Voice A', 'Voice B', ...) across 15-second audio chunks.
Fuses acoustic feature profiles with operator speaker hints and initial speaker maps.
Runs off-poll-thread with bounded latency (budget < 250ms).
"""
from __future__ import annotations

import logging
import math
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.live_voice_registry")


@dataclass
class VoiceCluster:
    voice_id: str  # "Voice A", "Voice B", "Voice C", ...
    resolved_speaker: Optional[str] = None  # Operator-hinted or roster-mapped name
    centroid: Optional[List[float]] = None  # Running average of acoustic feature vectors
    sample_count: int = 0
    first_seen_sec: float = 0.0
    last_seen_sec: float = 0.0
    associated_quotes: List[str] = field(default_factory=list)
    confidence: float = 1.0
    is_pre_enrolled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VoiceCluster":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


from .acoustic_features import (
    _compute_mel_energies, _estimate_pitch, _read_wav_data, align_acoustic_dimension,
    benchmark_acoustic_representations, compute_channel_rms_ratio, detect_intra_chunk_switches,
    extract_256d_acoustic_embedding, extract_acoustic_signature, extract_subchunk_signatures,
)


class LiveVoiceRegistry:
    """Maintains active voice clusters and persistent speaker bindings for live recording sessions."""

    def __init__(
        self, similarity_threshold: float = 0.68,
        initial_speaker_map: Optional[Dict[str, str]] = None, attendees: Optional[List[Any]] = None,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.voice_clusters: Dict[str, VoiceCluster] = {}
        self.attendees = attendees or []
        self._next_voice_index = 0
        self._voice_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for k, v in (initial_speaker_map or {}).items():
            if k.startswith(("Voice ", "SPEAKER_")):
                v_id = k.replace("SPEAKER_", "Voice ")
                self.voice_clusters[v_id] = VoiceCluster(voice_id=v_id, resolved_speaker=v, confidence=1.0)
            else:
                v_id = self._generate_voice_id()
                self.voice_clusters[v_id] = VoiceCluster(voice_id=v_id, resolved_speaker=k, confidence=1.0)
        self._preseed_enrolled_voiceprints()

    def _bind_or_create_preseeded_cluster(self, clean_name: str, emb_list: Optional[List[float]]) -> None:
        existing = next(
            (c for c in self.voice_clusters.values() if c.resolved_speaker and c.resolved_speaker.lower() == clean_name.lower()),
            None,
        )
        if existing is not None:
            existing.centroid, existing.is_pre_enrolled = emb_list, True
            return
        v_id = self._generate_voice_id()
        self.voice_clusters[v_id] = VoiceCluster(
            voice_id=v_id, resolved_speaker=clean_name, centroid=emb_list, confidence=1.0, is_pre_enrolled=True,
        )

    def _try_seed_single_speaker(self, store: Any, raw_name: Any, seeded_names: set[str]) -> None:
        clean_name = str((raw_name.get("name") if isinstance(raw_name, dict) else raw_name) or "").strip()
        if not clean_name or clean_name.lower() in seeded_names:
            return
        vp = store.get_voiceprint(clean_name)
        raw_emb = vp.get("embedding") if vp else None
        if isinstance(raw_emb, (list, tuple)) and len(raw_emb) == 256:
            self._bind_or_create_preseeded_cluster(clean_name, [float(x) for x in raw_emb])
            seeded_names.add(clean_name.lower())

    def _preseed_enrolled_voiceprints(self) -> None:
        """Pre-seeds voice clusters with enrolled 256-D voiceprints from canonical VoiceprintStore (D-REC-02)."""
        try:
            from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore
            store = VoiceprintStore()
            seeded_names: set[str] = set()
            candidates = self.attendees or [m.get("canonical_name") for m in store.get_enrolled_speakers()]
            for item in candidates:
                self._try_seed_single_speaker(store, item, seeded_names)
        except Exception as exc:
            logger.debug("LiveVoiceRegistry voiceprint pre-seed note: %s", exc)

    def _generate_voice_id(self) -> str:
        idx = self._next_voice_index
        self._next_voice_index += 1
        if idx < len(self._voice_alphabet):
            return f"Voice {self._voice_alphabet[idx]}"
        p1 = self._voice_alphabet[(idx // len(self._voice_alphabet)) - 1]
        p2 = self._voice_alphabet[idx % len(self._voice_alphabet)]
        return f"Voice {p1}{p2}"

    @staticmethod
    def _align_cluster_centroid(cluster: VoiceCluster, feat: np.ndarray, strict_256d: bool) -> Optional[np.ndarray]:
        if cluster.centroid is None:
            return None
        c_vec = np.array(cluster.centroid, dtype=np.float32)
        if c_vec.shape != feat.shape:
            if strict_256d:
                raise ValueError(f"Cluster {cluster.voice_id} has {c_vec.size}-D centroid, expected 256-D")
            c_vec = align_acoustic_dimension(c_vec, int(feat.shape[0]), allow_legacy_projection=True)
        return c_vec

    def _find_matching_cluster(
        self, feat: np.ndarray, norm: float, exclude_host: bool = False, strict_256d: bool = False,
    ) -> Tuple[Optional[VoiceCluster], float]:
        if strict_256d and feat.size != 256:
            raise ValueError(f"Expected 256-D embedding for live voice matching, got {feat.size}-D")
        best_match: Optional[VoiceCluster] = None
        best_sim: float = -1.0
        for cluster in self.voice_clusters.values():
            if exclude_host and (cluster.voice_id == "Voice Host" or cluster.resolved_speaker in ("Host", "Operator")):
                continue
            c_vec = self._align_cluster_centroid(cluster, feat, strict_256d)
            c_norm = float(np.linalg.norm(c_vec)) if c_vec is not None else 0.0
            if c_vec is not None and c_norm > 1e-6:
                sim = float(np.dot(feat, c_vec) / (norm * c_norm))
                if sim > best_sim:
                    best_sim, best_match = sim, cluster
        return best_match, best_sim

    @staticmethod
    def _compute_updated_centroid_vec(cluster: VoiceCluster, feat: np.ndarray) -> np.ndarray:
        if cluster.centroid is None:
            return feat.copy()
        c_curr = np.array(cluster.centroid, dtype=np.float32)
        n = cluster.sample_count
        if c_curr.shape != feat.shape:
            c_curr = align_acoustic_dimension(c_curr, int(feat.shape[0]), allow_legacy_projection=True)
            return feat.copy() if n == 0 else (n * c_curr + feat) / (n + 1)
        return (0.5 * c_curr + 0.5 * feat) if n == 0 else (n * c_curr + feat) / (n + 1)

    def _update_cluster_centroid(
        self, cluster: VoiceCluster, feat: np.ndarray, best_sim: float, last_seen_sec: float,
    ) -> Tuple[str, str, float]:
        if cluster.is_pre_enrolled and cluster.centroid is not None and len(cluster.centroid) == 256:
            if feat.size == 256 and best_sim >= 0.85:
                c_curr = np.array(cluster.centroid, dtype=np.float32)
                c_adapt = 0.88 * c_curr + 0.12 * feat
                c_norm = float(np.linalg.norm(c_adapt))
                if c_norm > 1e-6:
                    cluster.centroid = (c_adapt / c_norm).tolist()
        else:
            c_new = self._compute_updated_centroid_vec(cluster, feat)
            c_new_norm = float(np.linalg.norm(c_new))
            cluster.centroid = (c_new / c_new_norm if c_new_norm > 1e-6 else c_new).tolist()
        cluster.sample_count += 1
        cluster.last_seen_sec = last_seen_sec
        cluster.confidence = round(best_sim, 3)
        return (cluster.resolved_speaker or cluster.voice_id), cluster.voice_id, best_sim

    def _resolve_host_cluster(
        self, feat: np.ndarray, norm: float, start_sec: float, duration_sec: float,
    ) -> Tuple[str, str, float]:
        host_cluster = next(
            (c for c in self.voice_clusters.values() if c.resolved_speaker in ("Host", "Operator", "Local Host", "Host Operator") or c.voice_id == "Voice Host"),
            None,
        )
        if host_cluster is None:
            host_id = "Voice Host"
            self.voice_clusters[host_id] = VoiceCluster(
                voice_id=host_id, resolved_speaker="Host", centroid=feat.tolist() if norm > 1e-6 else None,
                sample_count=1, first_seen_sec=start_sec, last_seen_sec=start_sec + duration_sec, confidence=1.0,
            )
            return "Host", host_id, 1.0
        return self._update_cluster_centroid(host_cluster, feat, 1.0, start_sec + duration_sec)

    @staticmethod
    def _detect_switches(
        wav_path: Union[str, Path], duration_sec: float, chunk_idx: int, sub_features: Optional[List[np.ndarray]],
    ) -> List[int]:
        switches: List[int] = []
        if sub_features:
            switches = detect_intra_chunk_switches(sub_features)
        elif wav_path and Path(wav_path).is_file() and duration_sec >= 6.0:
            try:
                sub_sigs = extract_subchunk_signatures(wav_path, window_sec=3.0, max_duration_sec=duration_sec)
                if len(sub_sigs) >= 2:
                    switches = detect_intra_chunk_switches(sub_sigs)
            except Exception as e:
                logger.debug("Intra-chunk switch detection error: %s", e)
        if switches:
            logger.info("Intra-chunk speaker switches detected at sub-window indices %s for chunk %d", switches, chunk_idx)
        return switches

    def _should_route_to_host(self, feat: np.ndarray, norm: float, rms_ch0: float, rms_ch1: float) -> bool:
        if not (rms_ch1 > 2.5 * max(rms_ch0, 1.0) and rms_ch1 >= 80.0):
            return False
        host_c = self.voice_clusters.get("Voice Host")
        if host_c is None or host_c.centroid is None or rms_ch0 >= 5.0:
            return True
        h_vec = self._align_cluster_centroid(host_c, feat, False)
        h_norm = float(np.linalg.norm(h_vec)) if h_vec is not None else 0.0
        return bool(h_vec is not None and h_norm > 1e-6 and float(np.dot(feat, h_vec) / (norm * h_norm)) >= self.similarity_threshold)

    def _allocate_or_bind_counterparty(
        self, feat: np.ndarray, start_sec: float, duration_sec: float, is_remote_dominant: bool
    ) -> Tuple[str, str, float]:
        counterparty = self._infer_1on1_counterparty() if is_remote_dominant else None
        if counterparty:
            preseeded = next(
                (c for c in self.voice_clusters.values() if c.sample_count == 0 and c.resolved_speaker and c.resolved_speaker.lower() == counterparty.lower()),
                None,
            )
            if preseeded is not None:
                preseeded.first_seen_sec = start_sec
                return self._update_cluster_centroid(preseeded, feat, 1.0, start_sec + duration_sec)
        new_voice_id = self._generate_voice_id()
        self.voice_clusters[new_voice_id] = VoiceCluster(
            voice_id=new_voice_id, resolved_speaker=counterparty, centroid=feat.tolist(),
            sample_count=1, first_seen_sec=start_sec, last_seen_sec=start_sec + duration_sec, confidence=1.0,
        )
        return counterparty or new_voice_id, new_voice_id, 1.0

    def _extract_chunk_feature_vector(
        self, wav_path: Union[str, Path], duration_sec: float,
        acoustic_vector: Optional[np.ndarray], strict_256d: bool,
    ) -> np.ndarray:
        if acoustic_vector is not None:
            feat = np.asarray(acoustic_vector, dtype=np.float32).ravel()
            if strict_256d and feat.size != 256:
                raise ValueError(f"Expected 256-D acoustic vector in resolve_chunk_speaker, got {feat.size}-D")
            return feat
        use_256d = strict_256d or any(c.centroid is not None and len(c.centroid) == 256 for c in self.voice_clusters.values())
        return extract_256d_acoustic_embedding(wav_path, max_duration_sec=duration_sec) if use_256d else extract_acoustic_signature(wav_path)

    def resolve_chunk_speaker(
        self, wav_path: Union[str, Path], chunk_idx: int = 0, text: str = "speech",
        start_sec: float = 0.0, duration_sec: float = 15.0, acoustic_vector: Optional[np.ndarray] = None,
        fallback_speaker: str = "Speaker", sub_features: Optional[List[np.ndarray]] = None,
        strict_256d: bool = False,
    ) -> Tuple[str, str, float]:
        """Identifies or allocates a VoiceCluster for the chunk and returns (display_speaker_name, voice_id, confidence)."""
        if not text or not text.strip():
            return fallback_speaker, fallback_speaker, 0.0
        self.last_detected_switches = self._detect_switches(wav_path, duration_sec, chunk_idx, sub_features)
        feat = self._extract_chunk_feature_vector(wav_path, duration_sec, acoustic_vector, strict_256d)
        norm = float(np.linalg.norm(feat))
        if norm <= 1e-6:
            if not self.voice_clusters:
                return fallback_speaker, fallback_speaker, 0.0
            last_c = max(self.voice_clusters.values(), key=lambda c: c.last_seen_sec)
            return (last_c.resolved_speaker or last_c.voice_id), last_c.voice_id, 0.5

        rms_ch0, rms_ch1 = compute_channel_rms_ratio(wav_path)
        if self._should_route_to_host(feat, norm, rms_ch0, rms_ch1):
            return self._resolve_host_cluster(feat, norm, start_sec, duration_sec)

        is_remote_dominant = (rms_ch0 > 2.5 * max(rms_ch1, 1.0)) or (rms_ch0 < 5.0 and "Voice Host" in self.voice_clusters)
        best_match, best_sim = self._find_matching_cluster(feat, norm, exclude_host=is_remote_dominant, strict_256d=strict_256d)
        if best_match is not None and best_sim >= self.similarity_threshold:
            if self.last_detected_switches and len(self.attendees) > 2 and best_match.sample_count >= 4 and best_sim < 0.88:
                return self._allocate_or_bind_counterparty(feat, start_sec, duration_sec, is_remote_dominant)
            return self._update_cluster_centroid(best_match, feat, best_sim, start_sec + duration_sec)
        return self._allocate_or_bind_counterparty(feat, start_sec, duration_sec, is_remote_dominant)

    def _infer_1on1_counterparty(self) -> Optional[str]:
        names = [str(a.get("name") if isinstance(a, dict) else a).strip() for a in self.attendees if a]
        names = [n for n in names if n]
        if len(names) != 2:
            return None
        host_name = (self.resolve_host_attendee_name() or "").lower()
        non_host = [
            n for n in names
            if (not host_name or n.lower() != host_name)
            and not n.lower().startswith("amit ") and n.lower() not in ("amit", "host", "operator")
        ]
        return non_host[0] if len(non_host) == 1 else None

    def _find_role_host_name(self) -> Optional[str]:
        for a in self.attendees:
            if isinstance(a, dict) and str(a.get("role") or "").lower() in ("host", "operator", "organizer"):
                return str(a.get("name") or "").strip() or None
        return None

    def resolve_host_attendee_name(self) -> Optional[str]:
        """Resolves the configured host attendee name from NEE_OPERATOR_NAME, roster role, or default host."""
        from storage.credentials import CredentialManager
        role_host = self._find_role_host_name()
        if role_host:
            return role_host
        op_env = (CredentialManager.get_credential("NEE_OPERATOR_NAME") or "").strip()
        names = [str(a.get("name") if isinstance(a, dict) else a).strip() for a in self.attendees if a]
        if op_env:
            op_prefix = op_env.lower().split()[0] + " "
            matched = next((n for n in names if n.lower() == op_env.lower() or n.lower().startswith(op_prefix)), None)
            return matched or op_env
        return next((n for n in names if n.lower().startswith("amit ") or n.lower() == "amit"), None)

    @staticmethod
    def _backfill_retroactive_segments(
        retroactive_segments: Optional[List[Dict[str, Any]]], voice_id: str, speaker_name: str, prev: Optional[str]
    ) -> None:
        if not isinstance(retroactive_segments, list):
            return
        for seg in retroactive_segments:
            if isinstance(seg, dict) and (seg.get("voice_id") == voice_id or seg.get("speaker") in (voice_id, prev)):
                seg["speaker"] = speaker_name
                seg["voice_id"] = voice_id
                seg["speaker_provenance"] = "live_operator_hint_backfill"

    @staticmethod
    def _persist_bound_voiceprint(
        cluster: VoiceCluster, speaker_name: str, persist_to_store: bool, voiceprint_store: Optional[Any], project: str
    ) -> None:
        if not ((persist_to_store or voiceprint_store is not None) and cluster.centroid and len(cluster.centroid) == 256):
            return
        try:
            from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore
            store = voiceprint_store or VoiceprintStore()
            store.enroll_voiceprint(
                speaker_name=speaker_name, embedding=cluster.centroid, project=project,
                is_verified=True, verification_source="live_operator_hint",
                clean_speech_sec=max(2.0, float(cluster.sample_count) * 3.0),
            )
        except Exception as exc:
            logger.debug("Live hint voiceprint persistence skipped: %s", exc)

    def bind_speaker_hint(
        self,
        voice_id: str,
        speaker_name: str,
        quote: Optional[str] = None,
        retroactive_segments: Optional[List[Dict[str, Any]]] = None,
        persist_to_store: bool = False,
        voiceprint_store: Optional[Any] = None,
        project: str = "GLOBAL",
    ) -> Optional[str]:
        """Binds a VoiceCluster to an operator-hinted speaker name, backfills segments, and optionally enrolls 256-D voiceprint."""
        cluster = self.voice_clusters.get(voice_id)
        if not cluster:
            cluster = VoiceCluster(voice_id=voice_id, resolved_speaker=speaker_name, sample_count=0, confidence=1.0)
            self.voice_clusters[voice_id] = cluster
            prev = None
        else:
            prev = cluster.resolved_speaker
            cluster.resolved_speaker = speaker_name
        if quote and quote not in cluster.associated_quotes:
            cluster.associated_quotes.append(quote)
        self._backfill_retroactive_segments(retroactive_segments, voice_id, speaker_name, prev)
        self._persist_bound_voiceprint(cluster, speaker_name, persist_to_store, voiceprint_store, project)
        return prev if (prev and prev != speaker_name) else None

    def get_speaker_map(self) -> Dict[str, str]:
        return {vid: (c.resolved_speaker or vid) for vid, c in self.voice_clusters.items()}

    def get_all_centroids(self) -> Dict[str, List[float]]:
        return {vid: list(c.centroid) for vid, c in self.voice_clusters.items() if c.centroid is not None}

    def detect_capture_collapse(
        self, min_chunks: int = 10, collapse_threshold: float = 0.85, attendee_count: Optional[int] = None
    ) -> Dict[str, Any]:
        """Detects single-speaker collapse during live audio capture."""
        total_samples = sum(c.sample_count for c in self.voice_clusters.values())
        actual_attendees = attendee_count if attendee_count is not None else len(self.attendees)
        if not self.voice_clusters or total_samples == 0:
            return {
                "is_collapsed": False, "total_chunks": total_samples, "dominant_voice_id": None,
                "dominant_share": 0.0, "threshold": collapse_threshold, "attendee_count": actual_attendees, "warning": None,
            }
        max_cluster = max(self.voice_clusters.values(), key=lambda c: c.sample_count)
        dominant_share = float(max_cluster.sample_count) / float(total_samples) if total_samples > 0 else 0.0
        is_collapsed = bool(actual_attendees >= 2 and total_samples >= min_chunks and dominant_share >= collapse_threshold)
        warning_msg = (
            f"Single-speaker collapse detected: voice '{max_cluster.voice_id}' accounts for "
            f"{dominant_share:.1%} of {total_samples} chunks in a multi-attendee session ({actual_attendees} attendees)."
        ) if is_collapsed else None
        return {
            "is_collapsed": is_collapsed, "total_chunks": total_samples, "dominant_voice_id": max_cluster.voice_id,
            "dominant_share": round(dominant_share, 4), "threshold": collapse_threshold, "attendee_count": actual_attendees, "warning": warning_msg,
        }

    def reconcile_cross_chunk_segments(
        self,
        segments: List[Dict[str, Any]],
        overlap_tolerance_sec: float = 0.20,
    ) -> List[Dict[str, Any]]:
        """
        Reconciles overlap and cross-chunk voice identifiers in segment lists.
        Sorts segments chronologically, identifies temporal overlap between adjacent segments,
        and synchronizes voice IDs with the active cluster registry.
        """
        if not segments:
            return []

        sorted_segs = sorted(
            [dict(s) for s in segments],
            key=lambda x: (x.get("start_sec", 0.0), x.get("end_sec", 0.0)),
        )

        for i in range(len(sorted_segs)):
            seg = sorted_segs[i]
            vid = seg.get("voice_id")
            if vid and vid in self.voice_clusters:
                cl = self.voice_clusters[vid]
                if cl.resolved_speaker and not seg.get("speaker_override"):
                    seg["speaker"] = cl.resolved_speaker
                seg["confidence"] = round(cl.confidence, 3)

            if i > 0:
                prev = sorted_segs[i - 1]
                prev_end = prev.get("end_sec", 0.0)
                curr_start = seg.get("start_sec", 0.0)
                if curr_start < (prev_end - overlap_tolerance_sec):
                    overlap_dur = round(prev_end - curr_start, 3)
                    seg["is_overlap"] = True
                    seg["overlap_duration_sec"] = overlap_dur
                    prev["has_overlap_following"] = True

        return sorted_segs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity_threshold": self.similarity_threshold,
            "next_voice_index": self._next_voice_index,
            "clusters": {vid: c.to_dict() for vid, c in self.voice_clusters.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LiveVoiceRegistry":
        reg = cls(similarity_threshold=data.get("similarity_threshold", 0.68))
        reg._next_voice_index = data.get("next_voice_index", 0)
        for vid, c_dict in data.get("clusters", {}).items():
            reg.voice_clusters[vid] = VoiceCluster.from_dict(c_dict)
        return reg
