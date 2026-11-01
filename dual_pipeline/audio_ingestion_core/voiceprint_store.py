"""
dual_pipeline/audio_ingestion_core/voiceprint_store.py

Governed SQLite WAL Voiceprint Store:
- Persists 256-D speaker embeddings from HITL-verified turns into SQLite WAL.
- Re-scores and matches acoustic clusters across meetings using cosine similarity (threshold >= 0.82).
- Zero-lock concurrency via storage.connection and PRAGMA tuning.
- Enforces strict verification gate: unverified turns are barred from enrollment.
"""
from __future__ import annotations

import array
import hashlib
import logging
import math
import os
import re
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from dual_pipeline.parameters import get_audio_streaming_parameters
from storage.connection import configure_pragmas, create_sqlite_conn, execute_with_retry, IntegrityError

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.voiceprint_store")

EMBEDDING_DIM = 256
DEFAULT_SIMILARITY_THRESHOLD = float(get_audio_streaming_parameters()["voiceprint_similarity_threshold"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_vector(vec: Sequence[float], target_dim: int = EMBEDDING_DIM) -> List[float]:
    """Validates dimension and normalizes vector to unit L2 norm."""
    if isinstance(vec, (str, bytes, bytearray)):
        raise ValueError("Invalid embedding type: hash strings or raw bytes are barred from 256-D voiceprint store")
    if len(vec) != target_dim:
        raise ValueError(f"Embedding dimension mismatch: expected {target_dim}, got {len(vec)}")
    norm = math.sqrt(sum(float(x) * float(x) for x in vec))
    if norm < 1e-9 or math.isnan(norm) or math.isinf(norm):
        raise ValueError("Cannot normalize near-zero or non-finite acoustic embedding vector")
    return [round(float(x) / norm, 6) for x in vec]


def generate_deterministic_embedding(seed_key: str, dim: int = EMBEDDING_DIM) -> List[float]:
    """Generates a deterministic unit-normalized 256-D embedding from a seed key."""
    values: List[float] = []
    hasher = hashlib.sha256(seed_key.encode("utf-8"))
    digest = hasher.digest()
    for i in range(dim):
        # Derive pseudo-random float from hash chain
        h = hashlib.sha256(digest + struct.pack("<I", i)).digest()
        val = (int.from_bytes(h[:4], "little") / 0xFFFFFFFF) * 2.0 - 1.0
        values.append(val)
    return _normalize_vector(values, dim)


def _resolve_primary_repo_root(anchor: Path) -> Path:
    """Resolves the primary repository root across main, .worktrees/<slug>, and external git worktrees."""
    from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import resolve_canonical_repo_root
    search_dir = anchor if anchor.is_dir() else anchor.parent
    return resolve_canonical_repo_root(search_dir)


def resolve_canonical_voiceprint_db_path(base_file: Optional[Union[str, Path]] = None) -> Path:
    """Resolves canonical <primary_root>/.local/voiceprint_memory.sqlite across all worktrees without touching output/."""
    from config.paths import get_voiceprint_sqlite_path

    return get_voiceprint_sqlite_path(Path(base_file) if base_file is not None else None)


def _check_existing_sample_idempotent(
    conn: Any, clean_proj: str, clean_name: str, audio_sha256: Optional[str], channel_profile: str, now_ts: str
) -> Optional[Dict[str, Any]]:
    if not audio_sha256:
        return None
    clean_sha = str(audio_sha256).strip()
    if not execute_with_retry(conn, "SELECT sample_id FROM speaker_voiceprint_samples WHERE project = ? AND canonical_name = ? AND audio_sha256 = ? AND channel_profile = ?;", (clean_proj, clean_name, clean_sha, channel_profile)).fetchone():
        return None
    row_ex = execute_with_retry(conn, "SELECT sample_count, updated_at FROM speaker_voiceprints WHERE project = ? AND canonical_name = ?;", (clean_proj, clean_name)).fetchone()
    return {"canonical_name": clean_name, "project": clean_proj, "sample_count": row_ex[0] if row_ex else 1, "updated_at": row_ex[1] if row_ex else now_ts, "action": "ALREADY_ENROLLED", "audio_sha256": clean_sha}


def _insert_sample_record(
    conn: Any, clean_proj: str, clean_name: str, audio_sha256: Optional[str],
    channel_profile: str, duration_sec: float, clean_speech_sec: float,
    norm_vec: Sequence[float], verification_source: Optional[str], now_ts: str,
) -> None:
    if not audio_sha256:
        return
    clean_sha = str(audio_sha256).strip()
    sample_id = f"smp_{clean_proj.lower()}_{hashlib.sha256((clean_name + clean_sha + channel_profile).encode()).hexdigest()[:12]}"
    execute_with_retry(
        conn,
        "INSERT OR REPLACE INTO speaker_voiceprint_samples (sample_id, project, canonical_name, audio_sha256, channel_profile, duration_sec, clean_speech_sec, embedding, verification_source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        (sample_id, clean_proj, clean_name, clean_sha, channel_profile, float(duration_sec), float(clean_speech_sec), array.array("f", norm_vec).tobytes(), verification_source or "hitl_verified", now_ts),
    )


_ORTHOGRAPHIC_MAP = {
    "stephan": "stefan", "bryan": "brian", "steven": "stephen",
    "kathryn": "catherine", "kristen": "kristin", "jon": "john",
}


def _canonical_alias_key(name: str) -> str:
    base = re.sub(r"\s*\([^)]*\)", "", (name or "").strip()).strip().lower()
    tokens = [_ORTHOGRAPHIC_MAP.get(t, t) for t in base.split() if t]
    return " ".join(tokens)


def _build_roster_lookup(roster_names: Sequence[str]) -> Dict[str, str]:
    roster_lookup: Dict[str, str] = {}
    first_counts: Dict[str, List[str]] = {}
    for r in roster_names:
        if not (r and r.strip()):
            continue
        clean_r = r.strip()
        base_r = re.sub(r"\s*\([^)]*\)", "", clean_r).strip() or clean_r
        alias_k = _canonical_alias_key(base_r)
        roster_lookup[clean_r.lower()] = base_r
        roster_lookup.setdefault(base_r.lower(), base_r)
        if alias_k:
            roster_lookup.setdefault(alias_k, base_r)
            first_tok = alias_k.split()[0]
            first_counts.setdefault(first_tok, []).append(base_r)
    for f_tok, cands in first_counts.items():
        if len(cands) == 1:
            roster_lookup.setdefault(f_tok, cands[0])
    return roster_lookup


def _filter_roster_profiles(
    profiles: List[Tuple[str, array.array, int]], roster_lookup: Dict[str, str]
) -> List[Tuple[str, array.array, int]]:
    roster_profiles: List[Tuple[str, array.array, int]] = []
    seen_canonical: set[str] = set()
    for name, arr, count in profiles:
        base_key = re.sub(r"\s*\([^)]*\)", "", name).strip().lower()
        alias_k = _canonical_alias_key(name)
        first_k = alias_k.split()[0] if alias_k else ""
        canonical = roster_lookup.get(name.lower()) or roster_lookup.get(base_key) or roster_lookup.get(alias_k) or (roster_lookup.get(first_k) if " " not in alias_k else None)
        if canonical and canonical.lower() not in seen_canonical:
            seen_canonical.add(canonical.lower())
            roster_profiles.append((canonical, arr, count))
    return roster_profiles


def compute_adaptive_threshold(base_threshold: float, sample_count: int, variance: float = 0.0) -> float:
    """Computes variance-calibrated per-speaker matching threshold."""
    maturity_discount = min(0.04, 0.01 * math.log1p(max(0, int(sample_count) - 1)))
    variance_penalty = min(0.08, max(0.0, float(variance)) * 0.25)
    return round(max(0.76, min(0.92, float(base_threshold) - maturity_discount + variance_penalty)), 4)


def _assign_bipartite_roster_matches(
    candidate_scores: Dict[str, Dict[str, float]], roster_profiles: List[Tuple[str, array.array, int]],
    threshold: float, margin_threshold: float, variance_map: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, Any]]:
    var_lookup = {k.lower(): v for k, v in (variance_map or {}).items()}
    cnt_lookup = {n.lower(): cnt for n, _, cnt in roster_profiles}
    candidate_pairs = sorted([(cid, cand_name, sc) for cid, scores in candidate_scores.items() for cand_name, sc in scores.items()], key=lambda x: x[2], reverse=True)
    assigned_clusters, assigned_roster, assignments = set(), set(), {}
    for cid, cand_name, score in candidate_pairs:
        c_low = cand_name.lower()
        if cid not in assigned_clusters and cand_name not in assigned_roster and score >= compute_adaptive_threshold(threshold, cnt_lookup.get(c_low, 1), var_lookup.get(c_low, 0.0)):
            assignments[cid] = (cand_name, score)
            assigned_clusters.add(cid)
            assigned_roster.add(cand_name)

    results: Dict[str, Dict[str, Any]] = {}
    for cid, (cand_name, top_score) in assignments.items():
        second_score = max((s for c_name, s in candidate_scores[cid].items() if c_name != cand_name), default=0.0)
        margin, c_low = top_score - second_score, cand_name.lower()
        sample_count = cnt_lookup.get(c_low, 1)
        eff_thresh = compute_adaptive_threshold(threshold, sample_count, var_lookup.get(c_low, 0.0))
        results[cid] = {"cluster_id": cid, "canonical_name": cand_name, "score": round(top_score, 4), "second_score": round(second_score, 4), "margin": round(margin, 4), "adaptive_threshold": eff_thresh, "is_decisive": top_score >= eff_thresh and margin >= margin_threshold, "sample_count": sample_count}
    return results


def _merge_and_update_voiceprint(
    conn: Any, clean_proj: str, clean_name: str, blob: bytes, count: int,
    norm_vec: Sequence[float], clean_speech_sec: float, verification_source: Optional[str],
    now_ts: str, audio_sha256: Optional[str], channel_profile: str = "mono", old_var: float = 0.0,
) -> Dict[str, Any]:
    old_arr = array.array("f")
    old_arr.frombytes(blob)
    n = max(1, int(count))
    sim = sum(old_arr[i] * norm_vec[i] for i in range(EMBEDDING_DIM))
    dur_factor = min(1.0, max(0.25, float(clean_speech_sec) / 45.0))
    w_old = float(n)
    w_new = 0.05 if (n >= 2 and sim < 0.50) else min(dur_factor, w_old * 0.35 if n >= 2 else dur_factor)
    merged = [(w_old * old_arr[i] + w_new * norm_vec[i]) / (w_old + w_new) for i in range(EMBEDDING_DIM)]
    new_var = round((old_var * n + max(0.0, 1.0 - sim)) / (n + 1), 4)
    blob_out = array.array("f", _normalize_vector(merged, EMBEDDING_DIM)).tobytes()
    execute_with_retry(
        conn,
        "UPDATE speaker_voiceprints SET embedding = ?, sample_count = sample_count + 1, "
        "channel_profile = ?, intra_speaker_variance = ?, is_verified = 1, verification_source = ?, updated_at = ? "
        "WHERE project = ? AND canonical_name = ?;",
        (blob_out, channel_profile or "mono", new_var, verification_source or "hitl_verified", now_ts, clean_proj, clean_name),
    )
    conn.commit()
    return {
        "canonical_name": clean_name, "project": clean_proj, "sample_count": n + 1,
        "channel_profile": channel_profile or "mono", "intra_speaker_variance": new_var,
        "updated_at": now_ts, "action": "UPDATED", "audio_sha256": str(audio_sha256).strip() if audio_sha256 else None,
    }


def _load_deduped_voiceprints(conn: Any, clean_proj: str) -> List[Tuple[str, array.array, int]]:
    """Loads enrolled voiceprints across projects, prioritizing target project then GLOBAL."""
    cur = execute_with_retry(
        conn,
        "SELECT canonical_name, embedding, sample_count FROM speaker_voiceprints "
        "ORDER BY (project = ?) DESC, (project = 'GLOBAL') DESC, sample_count DESC, updated_at DESC;",
        (clean_proj,),
    )
    seen: set[str] = set()
    profiles: List[Tuple[str, array.array, int]] = []
    for name, blob, count in cur.fetchall():
        clean_name = (name or "").strip()
        key = _canonical_alias_key(clean_name)
        if not key or key in seen:
            continue
        seen.add(key)
        arr = array.array("f")
        arr.frombytes(blob)
        if len(arr) == EMBEDDING_DIM:
            profiles.append((clean_name, arr, int(count)))
    return profiles


def _blob_to_vec(blob: bytes) -> Optional[array.array]:
    arr = array.array("f")
    arr.frombytes(blob)
    return arr if len(arr) == EMBEDDING_DIM else None


def _load_channel_and_negative_maps(
    conn: Any, clean_proj: str, channel_profile: Optional[str]
) -> Tuple[Dict[str, array.array], Dict[str, List[array.array]], Dict[str, float]]:
    ch_map: Dict[str, array.array] = {}
    if channel_profile:
        rows_c = execute_with_retry(
            conn, "SELECT canonical_name, embedding FROM speaker_voiceprint_samples WHERE channel_profile = ? ORDER BY (project = ?) DESC, created_at DESC;",
            (channel_profile.strip().lower(), clean_proj),
        ).fetchall()
        for name, blob in rows_c:
            k, arr = (name or "").strip().lower(), _blob_to_vec(blob)
            if k and arr is not None:
                ch_map.setdefault(k, arr)
    neg_map: Dict[str, List[array.array]] = {}
    for r_name, blob in execute_with_retry(conn, "SELECT rejected_speaker, embedding FROM speaker_voiceprint_negatives;").fetchall():
        k, arr = (r_name or "").strip().lower(), _blob_to_vec(blob)
        if k and arr is not None:
            neg_map.setdefault(k, []).append(arr)
    var_map = {
        c_name.strip().lower(): float(var_val or 0.0)
        for c_name, var_val in execute_with_retry(conn, "SELECT canonical_name, intra_speaker_variance FROM speaker_voiceprints;").fetchall()
        if c_name
    }
    return ch_map, neg_map, var_map


def _validate_enrollment_inputs(speaker_name: str, is_verified: bool, embedding_model: str, verification_source: Optional[str]) -> str:
    if not is_verified:
        raise ValueError(f"Fail-Closed Voiceprint Gate: Cannot enroll unverified speaker '{speaker_name}'. Voiceprint updates strictly require human-in-the-loop or authenticated cryptographic verification.")
    clean_model, clean_vsrc = (embedding_model or "").strip().lower(), (verification_source or "").strip().lower()
    if clean_model != "wespeaker_resnet_256d" or any(bad in clean_vsrc for bad in ("sha256_hash", "21d", "handcrafted", "unverified_hash", "poison")):
        raise ValueError(f"Fail-Closed 256-D Model Gate: Rejected non-256D or hash vector (model={embedding_model!r}, source={verification_source!r}).")
    clean_name = speaker_name.strip()
    if not clean_name:
        raise ValueError("Cannot enroll voiceprint with empty speaker name")
    return clean_name


def _adjust_sim_channel_and_negatives(
    raw_sim: float, c_vec: Sequence[float], c_low: str,
    ch_map: Optional[Dict[str, array.array]], neg_map: Optional[Dict[str, List[array.array]]],
) -> float:
    sim = raw_sim
    if ch_map and c_low in ch_map:
        ch_arr = ch_map[c_low]
        ch_sim = sum(c_vec[i] * ch_arr[i] for i in range(EMBEDDING_DIM))
        sim = max(sim, 0.65 * ch_sim + 0.35 * sim) + 0.02
    if neg_map and c_low in neg_map:
        max_neg = max(sum(c_vec[i] * n_arr[i] for i in range(EMBEDDING_DIM)) for n_arr in neg_map[c_low])
        if max_neg >= 0.75:
            sim -= 0.18 * max_neg
    return sim


def _score_clusters_against_profiles(
    cluster_centroids: Dict[str, Sequence[float]], roster_profiles: List[Tuple[str, Any, int]],
    roster_name_set: set[str], include_global_fallback: bool, roster_priority_boost: float,
    ch_map: Optional[Dict[str, array.array]] = None, neg_map: Optional[Dict[str, List[array.array]]] = None,
) -> Dict[str, Dict[str, float]]:
    norm_clusters = {cid: _normalize_vector(vec, EMBEDDING_DIM) for cid, vec in cluster_centroids.items()}
    candidate_scores: Dict[str, Dict[str, float]] = {}
    for cid, c_vec in norm_clusters.items():
        scores_for_cid: Dict[str, float] = {}
        for cand_name, r_arr, _ in roster_profiles:
            c_low = cand_name.lower()
            raw_sim = _adjust_sim_channel_and_negatives(sum(c_vec[i] * r_arr[i] for i in range(EMBEDDING_DIM)), c_vec, c_low, ch_map, neg_map)
            if include_global_fallback and roster_name_set and c_low in roster_name_set:
                raw_sim = min(1.0, raw_sim + roster_priority_boost)
            scores_for_cid[cand_name] = min(1.0, raw_sim)
        candidate_scores[cid] = scores_for_cid
    return candidate_scores


class VoiceprintStore:
    """Governed SQLite WAL storage for cross-meeting 256-D speaker voiceprints."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        is_default_canonical = db_path is None and not os.environ.get("NEE_VOICEPRINT_DB_PATH")
        self.db_path = Path(db_path) if db_path is not None else resolve_canonical_voiceprint_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_schema()
        if is_default_canonical:
            legacy_local = self.db_path.parent.parent / ".local" / "voiceprint_memory.sqlite"
            if legacy_local.exists() and legacy_local.resolve() != self.db_path.resolve():
                self.sync_from_sqlite(legacy_local)
            legacy_output = self.db_path.parent.parent / "output" / "voiceprint_memory.sqlite"
            if legacy_output.exists() and legacy_output.resolve() != self.db_path.resolve():
                self.sync_from_sqlite(legacy_output)

    def _get_connection(self, readonly: bool = False) -> Any:
        conn = create_sqlite_conn(self.db_path, readonly=readonly)
        configure_pragmas(conn, readonly=readonly)
        return conn

    def _init_schema(self) -> None:
        with self._get_connection(readonly=False) as conn:
            execute_with_retry(
                conn,
                "CREATE TABLE IF NOT EXISTS speaker_voiceprints (speaker_id TEXT NOT NULL, canonical_name TEXT NOT NULL, "
                "project TEXT NOT NULL DEFAULT 'GLOBAL', embedding BLOB NOT NULL, dimension INTEGER NOT NULL DEFAULT 256, "
                "sample_count INTEGER NOT NULL DEFAULT 1, channel_profile TEXT NOT NULL DEFAULT 'mono', "
                "intra_speaker_variance REAL NOT NULL DEFAULT 0.0, is_verified INTEGER NOT NULL DEFAULT 1, "
                "verification_source TEXT, updated_at TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project, canonical_name));",
            )
            cols = {r[1] for r in execute_with_retry(conn, "PRAGMA table_info(speaker_voiceprints);").fetchall()}
            if "channel_profile" not in cols:
                execute_with_retry(conn, "ALTER TABLE speaker_voiceprints ADD COLUMN channel_profile TEXT NOT NULL DEFAULT 'mono';")
            if "intra_speaker_variance" not in cols:
                execute_with_retry(conn, "ALTER TABLE speaker_voiceprints ADD COLUMN intra_speaker_variance REAL NOT NULL DEFAULT 0.0;")
            execute_with_retry(
                conn,
                "CREATE TABLE IF NOT EXISTS speaker_voiceprint_samples (sample_id TEXT PRIMARY KEY, project TEXT NOT NULL, "
                "canonical_name TEXT NOT NULL, audio_sha256 TEXT NOT NULL, channel_profile TEXT NOT NULL DEFAULT 'mono', "
                "duration_sec REAL NOT NULL DEFAULT 0.0, clean_speech_sec REAL NOT NULL DEFAULT 0.0, embedding BLOB NOT NULL, "
                "verification_source TEXT, created_at TEXT NOT NULL, UNIQUE(project, canonical_name, audio_sha256, channel_profile));",
            )
            execute_with_retry(
                conn,
                "CREATE TABLE IF NOT EXISTS speaker_voiceprint_negatives (negative_id TEXT PRIMARY KEY, project TEXT NOT NULL, "
                "rejected_speaker TEXT NOT NULL, true_speaker TEXT, embedding BLOB NOT NULL, audio_sha256 TEXT, created_at TEXT NOT NULL);",
            )
            for idx_sql in (
                "CREATE INDEX IF NOT EXISTS idx_voiceprint_project ON speaker_voiceprints(project);",
                "CREATE INDEX IF NOT EXISTS idx_voiceprint_name ON speaker_voiceprints(canonical_name);",
                "CREATE INDEX IF NOT EXISTS idx_sample_lookup ON speaker_voiceprint_samples(project, canonical_name);",
                "CREATE INDEX IF NOT EXISTS idx_sample_audio_sha ON speaker_voiceprint_samples(audio_sha256);",
                "CREATE INDEX IF NOT EXISTS idx_neg_speaker ON speaker_voiceprint_negatives(rejected_speaker);",
            ):
                execute_with_retry(conn, idx_sql)
            conn.commit()

    def verify_integrity(self) -> bool:
        """Executes PRAGMA integrity_check returning True if database has zero corruption."""
        with self._get_connection(readonly=True) as conn:
            row = execute_with_retry(conn, "PRAGMA integrity_check;").fetchone()
            return bool(row and row[0] == "ok")

    def enroll_voiceprint(
        self, speaker_name: str, embedding: Sequence[float], project: str = "GLOBAL",
        is_verified: bool = False, verification_source: Optional[str] = None,
        audio_sha256: Optional[str] = None, duration_sec: float = 1.0,
        clean_speech_sec: float = 1.0, channel_profile: str = "mono",
        embedding_model: str = "wespeaker_resnet_256d",
    ) -> Dict[str, Any]:
        """Enrolls or updates a verified 256-D speaker voiceprint in WAL storage."""
        clean_name = _validate_enrollment_inputs(speaker_name, is_verified, embedding_model, verification_source)
        clean_proj = (project or "GLOBAL").strip().upper()
        clean_ch = (channel_profile or "mono").strip().lower()
        norm_vec = _normalize_vector(embedding, EMBEDDING_DIM)
        now_ts = _now_iso()

        with self._write_lock, self._get_connection(readonly=False) as conn:
            idem = _check_existing_sample_idempotent(conn, clean_proj, clean_name, audio_sha256, clean_ch, now_ts)
            if idem is not None:
                return idem
            _insert_sample_record(conn, clean_proj, clean_name, audio_sha256, clean_ch, duration_sec, clean_speech_sec, norm_vec, verification_source, now_ts)
            existing = execute_with_retry(
                conn, "SELECT embedding, sample_count, intra_speaker_variance FROM speaker_voiceprints WHERE project = ? AND canonical_name = ?;", (clean_proj, clean_name)
            ).fetchone()
            if existing:
                return _merge_and_update_voiceprint(
                    conn, clean_proj, clean_name, existing[0], existing[1], norm_vec, clean_speech_sec,
                    verification_source, now_ts, audio_sha256, clean_ch, float(existing[2] or 0.0)
                )
            blob_out = array.array("f", norm_vec).tobytes()
            spk_id = f"vp_{clean_proj.lower()}_{hashlib.sha256(clean_name.encode()).hexdigest()[:10]}"
            execute_with_retry(
                conn,
                "INSERT OR REPLACE INTO speaker_voiceprints (speaker_id, canonical_name, project, embedding, dimension, sample_count, channel_profile, intra_speaker_variance, is_verified, verification_source, updated_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 1, ?, ?, ?);",
                (spk_id, clean_name, clean_proj, blob_out, EMBEDDING_DIM, 1, clean_ch, verification_source or "hitl_verified", now_ts, now_ts),
            )
            conn.commit()
            return {"canonical_name": clean_name, "project": clean_proj, "sample_count": 1, "channel_profile": clean_ch, "intra_speaker_variance": 0.0, "updated_at": now_ts, "action": "ENROLLED", "audio_sha256": str(audio_sha256).strip() if audio_sha256 else None}

    def record_negative_voiceprint(
        self, rejected_speaker: str, embedding: Sequence[float], project: str = "GLOBAL",
        true_speaker: Optional[str] = None, audio_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Records contrastive negative repulsion vector and unlearns contaminated sample when operator overrides."""
        clean_rej = (rejected_speaker or "").strip()
        if not clean_rej:
            raise ValueError("Cannot record negative voiceprint with empty rejected_speaker")
        clean_proj = (project or "GLOBAL").strip().upper()
        norm_vec = _normalize_vector(embedding, EMBEDDING_DIM)
        now_ts = _now_iso()
        neg_id = f"neg_{clean_proj.lower()}_{hashlib.sha256((clean_rej + str(audio_sha256 or now_ts)).encode()).hexdigest()[:12]}"
        blob_neg = array.array("f", norm_vec).tobytes()
        with self._write_lock, self._get_connection(readonly=False) as conn:
            execute_with_retry(
                conn,
                "INSERT OR REPLACE INTO speaker_voiceprint_negatives (negative_id, project, rejected_speaker, true_speaker, embedding, audio_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
                (neg_id, clean_proj, clean_rej, (true_speaker or "").strip() or None, blob_neg, str(audio_sha256).strip() if audio_sha256 else None, now_ts),
            )
            if audio_sha256:
                execute_with_retry(
                    conn, "DELETE FROM speaker_voiceprint_samples WHERE project = ? AND canonical_name = ? AND audio_sha256 = ?;",
                    (clean_proj, clean_rej, str(audio_sha256).strip()),
                )
            existing = execute_with_retry(
                conn, "SELECT embedding, sample_count FROM speaker_voiceprints WHERE project = ? AND canonical_name = ?;", (clean_proj, clean_rej)
            ).fetchone()
            if existing:
                old_arr = array.array("f")
                old_arr.frombytes(existing[0])
                sim = sum(old_arr[i] * norm_vec[i] for i in range(EMBEDDING_DIM))
                if sim > 0.30:
                    repelled = _normalize_vector([old_arr[i] - 0.25 * norm_vec[i] for i in range(EMBEDDING_DIM)], EMBEDDING_DIM)
                    execute_with_retry(
                        conn, "UPDATE speaker_voiceprints SET embedding = ?, updated_at = ? WHERE project = ? AND canonical_name = ?;",
                        (array.array("f", repelled).tobytes(), now_ts, clean_proj, clean_rej),
                    )
            conn.commit()
        if true_speaker and true_speaker.strip() and true_speaker.strip().lower() != clean_rej.lower():
            self.enroll_voiceprint(true_speaker.strip(), norm_vec, project=clean_proj, is_verified=True, verification_source="hitl_override_rebind", audio_sha256=audio_sha256)
        return {"negative_id": neg_id, "rejected_speaker": clean_rej, "true_speaker": true_speaker, "project": clean_proj}

    def get_negative_voiceprints(self, rejected_speaker: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns recorded contrastive negative samples."""
        with self._get_connection(readonly=True) as conn:
            if rejected_speaker:
                rows = execute_with_retry(conn, "SELECT negative_id, project, rejected_speaker, true_speaker, audio_sha256, created_at FROM speaker_voiceprint_negatives WHERE LOWER(rejected_speaker) = ?;", (rejected_speaker.strip().lower(),)).fetchall()
            else:
                rows = execute_with_retry(conn, "SELECT negative_id, project, rejected_speaker, true_speaker, audio_sha256, created_at FROM speaker_voiceprint_negatives;").fetchall()
            return [{"negative_id": r[0], "project": r[1], "rejected_speaker": r[2], "true_speaker": r[3], "audio_sha256": r[4], "created_at": r[5]} for r in rows]

    def match_voiceprint(
        self, embedding: Sequence[float], project: str = "GLOBAL",
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD, roster_names: Optional[Sequence[str]] = None,
        roster_priority_boost: float = 0.05, channel_profile: Optional[str] = None,
    ) -> Optional[Tuple[str, float]]:
        """Matches a 256-D embedding against all enrolled voiceprints across projects (D-REC-02)."""
        norm_target = _normalize_vector(embedding, EMBEDDING_DIM)
        clean_proj = (project or "GLOBAL").strip().upper()
        roster_lookup = _build_roster_lookup(roster_names) if roster_names else {}
        with self._get_connection(readonly=True) as conn:
            profiles = _load_deduped_voiceprints(conn, clean_proj)
            ch_map, neg_map, var_map = _load_channel_and_negative_maps(conn, clean_proj, channel_profile)

        best_name, best_sim = None, -1.0
        for name, arr, count in profiles:
            c_low = name.lower()
            sim = _adjust_sim_channel_and_negatives(sum(norm_target[i] * arr[i] for i in range(EMBEDDING_DIM)), norm_target, c_low, ch_map, neg_map)
            if roster_lookup and (c_low in roster_lookup or _canonical_alias_key(name) in roster_lookup):
                sim = min(1.0, sim + roster_priority_boost)
            if sim >= compute_adaptive_threshold(threshold, count, var_map.get(c_low, 0.0)) and sim > best_sim:
                best_name, best_sim = name, min(1.0, sim)
        return (best_name, round(best_sim, 4)) if best_name else None

    def find_all_matches(
        self, cluster_embeddings: Dict[str, Sequence[float]], project: str = "GLOBAL",
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> Dict[str, Tuple[str, float]]:
        """Matches multiple clusters against enrolled voiceprints with collision resolution."""
        raw_matches = [
            (cid, m[0], m[1]) for cid, emb in cluster_embeddings.items()
            if (m := self.match_voiceprint(emb, project=project, threshold=threshold))
        ]
        raw_matches.sort(key=lambda m: m[2], reverse=True)
        assigned: set[str] = set()
        final_map: Dict[str, Tuple[str, float]] = {}
        for cid, name, score in raw_matches:
            if name not in assigned:
                final_map[cid] = (name, score)
                assigned.add(name)
        return final_map

    def match_roster(
        self, cluster_centroids: Dict[str, Sequence[float]], roster_names: Sequence[str],
        project: str = "GLOBAL", threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        margin_threshold: Optional[float] = None, include_global_fallback: bool = False,
        roster_priority_boost: float = 0.05, channel_profile: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Matches cluster centroids against enrolled voiceprints across all projects with roster priority (D-REC-02)."""
        if not cluster_centroids or (not roster_names and not include_global_fallback):
            return {}
        if margin_threshold is None:
            from dual_pipeline.parameters import get_float_parameter
            margin_threshold = get_float_parameter("hitl.unresolved_entity_margin_threshold", 0.15)
        clean_proj = (project or "GLOBAL").strip().upper()
        roster_lookup = _build_roster_lookup(roster_names) if roster_names else {}
        with self._get_connection(readonly=True) as conn:
            profiles = _load_deduped_voiceprints(conn, clean_proj)
            ch_map, neg_map, var_map = _load_channel_and_negative_maps(conn, clean_proj, channel_profile)
        roster_profiles = _filter_roster_profiles(profiles, roster_lookup) if roster_lookup else []
        roster_name_set = {n.lower() for n, _, _ in roster_profiles}
        if include_global_fallback:
            roster_profiles.extend((name, arr, count) for name, arr, count in profiles if name.lower() not in roster_name_set)
        if not roster_profiles:
            return {}
        candidate_scores = _score_clusters_against_profiles(cluster_centroids, roster_profiles, roster_name_set, include_global_fallback, roster_priority_boost, ch_map, neg_map)
        return _assign_bipartite_roster_matches(candidate_scores, roster_profiles, threshold, margin_threshold, var_map)

    def get_voiceprint(self, speaker_name: str, project: str = "GLOBAL") -> Optional[Dict[str, Any]]:
        """Returns enrolled voiceprint for a speaker if present across project or fallback scopes."""
        clean_name = (speaker_name or "").strip()
        if not clean_name:
            return None
        base_name = re.sub(r"\s*\([^)]*\)", "", clean_name).strip()
        alias_k = _canonical_alias_key(clean_name)
        first_tok = alias_k.split()[0] if alias_k else ""
        clean_proj = (project or "GLOBAL").strip().upper()
        with self._get_connection(readonly=True) as conn:
            cur = execute_with_retry(
                conn,
                "SELECT canonical_name, project, embedding, sample_count, is_verified, verification_source, updated_at, channel_profile, intra_speaker_variance "
                "FROM speaker_voiceprints ORDER BY (project = ?) DESC, (project = 'GLOBAL') DESC, sample_count DESC, updated_at DESC;",
                (clean_proj,),
            )
            row = next(
                (
                    r for r in cur.fetchall()
                    if _canonical_alias_key(r[0]) == alias_k
                    or r[0].strip().lower() in (clean_name.lower(), base_name.lower())
                    or (first_tok and (_canonical_alias_key(r[0]) == first_tok or (" " not in alias_k and _canonical_alias_key(r[0]).split()[0] == first_tok)))
                ),
                None,
            )
            if not row:
                return None
            arr = array.array("f")
            arr.frombytes(row[2])
            return {
                "canonical_name": row[0], "project": row[1], "embedding": list(arr),
                "sample_count": row[3], "is_verified": bool(row[4]),
                "verification_source": row[5], "updated_at": row[6],
                "channel_profile": row[7] if len(row) > 7 else "mono",
                "intra_speaker_variance": float(row[8] or 0.0) if len(row) > 8 else 0.0,
            }

    def get_enrolled_speakers(self, project: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns metadata for all enrolled voiceprint speakers."""
        query = "SELECT canonical_name, project, sample_count, is_verified, verification_source, updated_at, channel_profile, intra_speaker_variance FROM speaker_voiceprints"
        params: Tuple[Any, ...] = (project.strip().upper(),) if project else ()
        if project:
            query += " WHERE project = ?"
        with self._get_connection(readonly=True) as conn:
            cur = execute_with_retry(conn, query + " ORDER BY canonical_name ASC;", params)
            return [{"canonical_name": r[0], "project": r[1], "sample_count": r[2], "is_verified": bool(r[3]), "verification_source": r[4], "updated_at": r[5], "channel_profile": r[6], "intra_speaker_variance": float(r[7] or 0.0)} for r in cur.fetchall()]

    def get_enrolled_samples(self, project: Optional[str] = None, canonical_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns provenance metadata for enrolled audio samples."""
        query = "SELECT sample_id, project, canonical_name, audio_sha256, channel_profile, duration_sec, clean_speech_sec, verification_source, created_at FROM speaker_voiceprint_samples"
        conds, params = [], []
        if project:
            conds.append("project = ?")
            params.append(project.strip().upper())
        if canonical_name:
            conds.append("canonical_name = ?")
            params.append(canonical_name.strip())
        if conds:
            query += " WHERE " + " AND ".join(conds)
        with self._get_connection(readonly=True) as conn:
            cur = execute_with_retry(conn, query + " ORDER BY created_at DESC;", tuple(params))
            return [{"sample_id": r[0], "project": r[1], "canonical_name": r[2], "audio_sha256": r[3], "channel_profile": r[4], "duration_sec": r[5], "clean_speech_sec": r[6], "verification_source": r[7], "created_at": r[8]} for r in cur.fetchall()]

    def purge_samples_by_audio_sha(self, audio_sha256: str, project: Optional[str] = None) -> int:
        """Purges corrupted or inverted voiceprint enrollments for a specific audio_sha256."""
        clean_sha = (audio_sha256 or "").strip()
        if not clean_sha:
            return 0
        with self._write_lock, self._get_connection(readonly=False) as conn:
            cond = "project = ? AND audio_sha256 = ?" if project else "audio_sha256 = ?"
            params = (project.strip().upper(), clean_sha) if project else (clean_sha,)
            pairs = [(r[0], r[1]) for r in execute_with_retry(conn, f"SELECT DISTINCT project, canonical_name FROM speaker_voiceprint_samples WHERE {cond};", params).fetchall()]
            execute_with_retry(conn, f"DELETE FROM speaker_voiceprint_samples WHERE {cond};", params)
            for proj, name in pairs:
                execute_with_retry(conn, "DELETE FROM speaker_voiceprints WHERE project = ? AND canonical_name = ? AND sample_count <= 1;", (proj, name))
            conn.commit()
            return len(pairs)

    def sync_from_sqlite(self, source_db_path: Union[str, Path]) -> int:
        """Merges verified voiceprints from a secondary SQLite store into this store."""
        src = Path(source_db_path)
        if not src.exists() or src.resolve() == self.db_path.resolve():
            return 0
        synced = 0
        try:
            with create_sqlite_conn(src, readonly=True) as sconn:
                rows = execute_with_retry(sconn, "SELECT canonical_name, project, embedding, verification_source FROM speaker_voiceprints WHERE is_verified = 1;").fetchall()
            for name, proj, blob, v_src in rows:
                if self.get_voiceprint(name, project=proj) is not None:
                    continue
                arr = array.array("f")
                arr.frombytes(blob)
                if len(arr) == EMBEDDING_DIM:
                    self.enroll_voiceprint(name, list(arr), project=proj, is_verified=True, verification_source=v_src or "legacy_sync")
                    synced += 1
        except Exception:
            return synced
        return synced

    def clear(self) -> None:
        """Clears all enrolled voiceprints and samples (for isolated test teardowns)."""
        with self._get_connection(readonly=False) as conn:
            execute_with_retry(conn, "DELETE FROM speaker_voiceprints;")
            execute_with_retry(conn, "DELETE FROM speaker_voiceprint_samples;")
            execute_with_retry(conn, "DELETE FROM speaker_voiceprint_negatives;")
            conn.commit()
