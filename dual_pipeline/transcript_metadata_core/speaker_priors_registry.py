"""
dual_pipeline/transcript_metadata_core/speaker_priors_registry.py

Governed runtime accessor and atomic mutator for learned speaker priors,
acoustic SNR thresholds, and extractor chunk-routing weights.
Supports closed-loop system learning across Intake (Tier 1) and Extraction (Tier 2).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SPEAKER_PRIORS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "runtime"
    / "learned_speaker_priors.json"
)

DEFAULT_BOOTSTRAP_PRIORS: Dict[str, Any] = {
    "schema_version": "1.0.0",
    "updated_at": "2026-10-22T00:00:00Z",
    "description": "Governed runtime parameter store for multi-confirmed acoustic SNR thresholds, speaker alias priors, and extractor routing weights.",
    "speaker_aliases": {
        "Speaker 1": "Alex Sanchez",
        "Speaker 2": "harper Hoffman",
    },
    "acoustic_priors": {
        "min_snr_db": 12.0,
        "max_speech_rate_wpm": 220,
        "clipping_threshold_db": -0.5,
    },
    "extractor_routing_weights": {
        "TECH_ARCH": 1.0,
        "STRATEGIC_COMMITMENTS": 1.0,
        "STAKEHOLDER_DYNAMICS": 1.0,
        "FRICTION_BLOCKER": 1.0,
    },
    "co_attendance_priors": {},
    "turn_transition_priors": {},
    "speaker_cadence_priors": {},
}


def _merge_dict_field(target: Dict[str, Any], source: Dict[str, Any], field_name: str) -> None:
    val = source.get(field_name)
    if isinstance(val, dict):
        target.setdefault(field_name, {}).update(val)


def _merge_priors_data(merged: Dict[str, Any], data: Any) -> None:
    if not isinstance(data, dict):
        return
    for key in (
        "speaker_aliases",
        "acoustic_priors",
        "extractor_routing_weights",
        "co_attendance_priors",
        "turn_transition_priors",
        "speaker_cadence_priors",
    ):
        _merge_dict_field(merged, data, key)
    if "updated_at" in data:
        merged["updated_at"] = data["updated_at"]


def load_learned_speaker_priors(priors_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads learned speaker priors merged on top of bootstrap defaults."""
    merged = json.loads(json.dumps(DEFAULT_BOOTSTRAP_PRIORS))
    path = priors_path or DEFAULT_SPEAKER_PRIORS_PATH
    if not path.exists():
        return merged
    try:
        with open(path, "r", encoding="utf-8") as f:
            _merge_priors_data(merged, json.load(f))
    except Exception as e:
        logger.warning("Failed to load learned speaker priors from %s: %s", path, e)
    return merged


def load_extractor_routing_weights(priors_path: Optional[Path] = None) -> Dict[str, float]:
    """Loads extractor routing weights with default 1.0 fallback."""
    priors = load_learned_speaker_priors(priors_path)
    weights = priors.get("extractor_routing_weights", {})
    return {
        "TECH_ARCH": float(weights.get("TECH_ARCH", 1.0)),
        "STRATEGIC_COMMITMENTS": float(weights.get("STRATEGIC_COMMITMENTS", 1.0)),
        "STAKEHOLDER_DYNAMICS": float(weights.get("STAKEHOLDER_DYNAMICS", 1.0)),
        "FRICTION_BLOCKER": float(weights.get("FRICTION_BLOCKER", 1.0)),
    }


def load_acoustic_priors(priors_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads acoustic prior thresholds."""
    priors = load_learned_speaker_priors(priors_path)
    return priors.get("acoustic_priors", dict(DEFAULT_BOOTSTRAP_PRIORS["acoustic_priors"]))


def register_learned_speaker_alias(
    from_speaker: str,
    to_speaker: str,
    priors_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Atomically registers an approved speaker alias into learned_speaker_priors.json."""
    path = priors_path or DEFAULT_SPEAKER_PRIORS_PATH
    data = load_learned_speaker_priors(path)
    data["speaker_aliases"][from_speaker.strip()] = to_speaker.strip()
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)
    return data


def update_extractor_routing_weights(
    weights: Dict[str, float],
    priors_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Atomically updates extractor routing weights into learned_speaker_priors.json."""
    path = priors_path or DEFAULT_SPEAKER_PRIORS_PATH
    data = load_learned_speaker_priors(path)
    for k, v in weights.items():
        data["extractor_routing_weights"][k] = float(v)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)
    return data


def update_diarization_thresholds(
    thresholds: Dict[str, float],
    priors_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Atomically updates acoustic and diarization thresholds in learned_speaker_priors.json."""
    path = priors_path or DEFAULT_SPEAKER_PRIORS_PATH
    data = load_learned_speaker_priors(path)
    for k, v in thresholds.items():
        data.setdefault("acoustic_priors", {})[k] = float(v)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)
    return data


def _atomic_save_priors(path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)
    return data


def _update_co_attendance_counts(co_att: Dict[str, Any], clean_speakers: List[str]) -> None:
    for i, spk_a in enumerate(clean_speakers):
        for spk_b in clean_speakers[i + 1 :]:
            pair_key = "|".join(sorted((spk_a, spk_b)))
            co_att[pair_key] = int(co_att.get(pair_key, 0)) + 1


def _update_transition_counts(trans: Dict[str, Any], ordered_turns: Sequence[Tuple[str, str]], spk_set: set[str]) -> None:
    prev_s: Optional[str] = None
    for raw_s, _ in ordered_turns:
        s_clean = str(raw_s).strip()
        if s_clean in spk_set:
            if prev_s and prev_s != s_clean:
                t_key = f"{prev_s}->{s_clean}"
                trans[t_key] = int(trans.get(t_key, 0)) + 1
            prev_s = s_clean


def record_verified_session_dynamics(
    verified_speakers: Sequence[str],
    ordered_turns: Sequence[Tuple[str, str]] = (),
    speaker_wpm: Optional[Dict[str, float]] = None,
    priors_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Records verified meeting co-attendance, Markov turn transitions, and speaker WPM cadence priors."""
    path = priors_path or DEFAULT_SPEAKER_PRIORS_PATH
    data = load_learned_speaker_priors(path)
    clean_spks = sorted(list(dict.fromkeys(str(s).strip() for s in verified_speakers if str(s).strip())))
    if len(clean_spks) >= 2:
        _update_co_attendance_counts(data.setdefault("co_attendance_priors", {}), clean_spks)
    if ordered_turns and clean_spks:
        _update_transition_counts(data.setdefault("turn_transition_priors", {}), ordered_turns, set(clean_spks))
    if isinstance(speaker_wpm, dict):
        cadence = data.setdefault("speaker_cadence_priors", {})
        for spk, wpm in speaker_wpm.items():
            if spk in clean_spks and 40.0 <= float(wpm) <= 300.0:
                old = float(cadence.get(spk, wpm))
                cadence[spk] = round(0.70 * old + 0.30 * float(wpm), 1)
    return _atomic_save_priors(path, data)


def _score_transition_prior(trans: Any, cand: str, prev_speaker: Optional[str]) -> Tuple[float, List[str]]:
    if not prev_speaker or not isinstance(trans, dict):
        return 0.0, []
    p_clean = prev_speaker.strip()
    t_cnt = int(trans.get(f"{p_clean}->{cand}", 0))
    total_out = sum(int(v) for k, v in trans.items() if str(k).startswith(f"{p_clean}->"))
    if t_cnt >= 2 and total_out > 0 and (t_cnt / total_out) >= 0.30:
        return 0.08, [f"Learned Markov turn-transition prior from {p_clean} ({t_cnt}/{total_out}) (+0.08)"]
    return 0.0, []


def _score_co_attendance_prior(co_att: Any, cand: str, roster_peers: Sequence[str]) -> Tuple[float, List[str]]:
    if not roster_peers or not isinstance(co_att, dict):
        return 0.0, []
    peer_hits = sum(
        1 for p in roster_peers
        if str(p).strip() != cand and int(co_att.get("|".join(sorted((cand, str(p).strip()))), 0)) >= 2
    )
    if peer_hits > 0:
        return 0.06, [f"Learned cross-meeting co-attendance prior ({peer_hits} peer(s)) (+0.06)"]
    return 0.0, []


def score_learned_conversational_priors(
    candidate_name: str,
    prev_speaker: Optional[str] = None,
    roster_peers: Sequence[str] = (),
    observed_wpm: float = 0.0,
    priors_path: Optional[Path] = None,
) -> Tuple[float, List[str]]:
    """Computes bounded Bayesian attribution boost from learned Markov transitions, co-attendance, and cadence."""
    data = load_learned_speaker_priors(priors_path)
    cand = candidate_name.strip()
    t_sc, t_pts = _score_transition_prior(data.get("turn_transition_priors"), cand, prev_speaker)
    c_sc, c_pts = _score_co_attendance_prior(data.get("co_attendance_priors"), cand, roster_peers)
    score, points = t_sc + c_sc, t_pts + c_pts
    cadence = data.get("speaker_cadence_priors") or {}
    if observed_wpm > 0 and isinstance(cadence, dict) and cand in cadence:
        learned_wpm = float(cadence[cand])
        if learned_wpm > 0 and (min(observed_wpm, learned_wpm) / max(observed_wpm, learned_wpm)) >= 0.80:
            score += 0.06
            points.append(f"Learned speaking cadence prior match ({observed_wpm:.0f} vs {learned_wpm:.0f} WPM) (+0.06)")
    return round(score, 3), points


def record_audio_voice_learning_event(event: Dict[str, Any], runtime_root: Optional[Path] = None) -> Path:
    """Appends a structured audio voice recognition learning event to audio_voice_learning_events.jsonl."""
    root = runtime_root or (DEFAULT_SPEAKER_PRIORS_PATH.parent.parent.parent / ".local" / "learning")
    root.mkdir(parents=True, exist_ok=True)
    ledger_file = root / "audio_voice_learning_events.jsonl"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parameter_name": event.get("parameter_name") or "audio_ingestion.voiceprint_similarity_threshold",
        **event,
    }
    with open(ledger_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")
    return ledger_file


def load_audio_voice_learning_events(runtime_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads persisted audio voice recognition learning events from audio_voice_learning_events.jsonl."""
    root = runtime_root or (DEFAULT_SPEAKER_PRIORS_PATH.parent.parent.parent / ".local" / "learning")
    ledger_file = root / "audio_voice_learning_events.jsonl"
    if not ledger_file.is_file():
        return []
    events: List[Dict[str, Any]] = []
    for line in ledger_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return events


class SpeakerPriorsRegistry:
    """Object-oriented governed interface for learned speaker aliases and diarization priors."""

    def __init__(self, priors_path: Optional[Path] = None) -> None:
        self.priors_path = priors_path or DEFAULT_SPEAKER_PRIORS_PATH

    def load(self) -> Dict[str, Any]:
        return load_learned_speaker_priors(self.priors_path)

    def register_alias(self, from_speaker: str, to_speaker: str) -> Dict[str, Any]:
        return register_learned_speaker_alias(from_speaker, to_speaker, priors_path=self.priors_path)

    def update_thresholds(self, thresholds: Dict[str, float]) -> Dict[str, Any]:
        return update_diarization_thresholds(thresholds, priors_path=self.priors_path)

    def record_session_dynamics(
        self, verified_speakers: Sequence[str], ordered_turns: Sequence[Tuple[str, str]] = (), speaker_wpm: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        return record_verified_session_dynamics(verified_speakers, ordered_turns, speaker_wpm=speaker_wpm, priors_path=self.priors_path)

