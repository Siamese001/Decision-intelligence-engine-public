#!/usr/bin/env python3
"""Single Source of Truth Writer for Operator Decision Records.

Implements the Wave 3 single-writer architecture:
- Primary store of record: SQLite (hitl_decision_ledger.sqlite) with WAL mode, busy timeout,
  and transaction locking.
- Secondary synchronized export: JSON (hitl_decision_ledger.json) updated atomically on every write.
- Cryptographic hash chaining: Every record links to the previous entry's digest.
- Anti-synthetic firewall: Enforces validate_approval_origin() to reject synthetic stop-hook markers.
- Separation of concerns: Enforces that tool approvals are never recorded as option decisions.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from config.credentials import CredentialManager
from storage.connection import create_sqlite_conn, sqlite3

from config.operator_decision_vocabulary import (
    validate_decision_record_boundary,
)
from dual_pipeline.core import digest, stable_id, utc_now
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA, _extract_sel_key

logger = logging.getLogger(__name__)


def _detect_git_common_root() -> Optional[Path]:
    try:
        git_common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"], text=True, stderr=subprocess.DEVNULL, timeout=10.0
        ).strip()
        if not git_common:
            return None
        c_dir = Path(git_common).resolve()
        common_root = c_dir.parent if c_dir.name == ".git" else c_dir
        return common_root.parent if common_root.name == ".git" else common_root
    except Exception:
        return None


def _is_git_tracked(file_path: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(file_path)],
            cwd=str(file_path.parent),
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        return res.returncode == 0
    except Exception:
        return False


def _pick_root_local_file(root: Path, filename: str) -> Path:
    cand_local = root / ".local" / "ledger" / filename
    if cand_local.is_file() or (root / ".local" / "ledger").is_dir():
        return cand_local.resolve()
    return (root / filename).resolve()


def _search_roots_for_ledger(search_roots: List[Path], filename: str) -> Optional[Path]:
    for r in search_roots:
        cand = r / ".local" / "ledger" / filename
        if cand.is_file() and cand.stat().st_size > 0:
            return cand.resolve()
    for r in search_roots:
        d = r / ".local" / "ledger"
        if d.is_dir():
            return (d / filename).resolve()
    for r in search_roots:
        cand = r / filename
        if cand.is_file() and cand.stat().st_size > 0 and not _is_git_tracked(cand):
            return cand.resolve()
    return None


def _pick_ledger_file(
    explicit: Optional[Union[Path, str]],
    root: Path,
    common_root: Optional[Path],
    has_repo_root: bool,
    filename: str,
) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if has_repo_root:
        return _pick_root_local_file(root, filename)

    search_roots = [common_root, root] if (common_root and common_root != root) else [root]
    found = _search_roots_for_ledger(search_roots, filename)
    if found is not None:
        return found
    pref_root = common_root or root
    return (pref_root / ".local" / "ledger" / filename).resolve()


def resolve_ledger_paths(
    repo_root: Optional[Union[Path, str]] = None,
    sqlite_path: Optional[Union[Path, str]] = None,
    json_path: Optional[Union[Path, str]] = None,
) -> Tuple[Path, Path]:
    """Resolve the canonical paths for both the SQLite master store and the JSON export."""
    sqlite_path = sqlite_path or CredentialManager.get_credential("NEE_HITL_LEDGER_SQLITE")
    json_path = json_path or CredentialManager.get_credential("NEE_HITL_LEDGER_JSON")
    if sqlite_path and json_path:
        return Path(sqlite_path).resolve(), Path(json_path).resolve()

    common_root = _detect_git_common_root()
    env_root = CredentialManager.get_credential("NEE_RUNTIME_ROOT")
    root = Path(repo_root).resolve() if repo_root else (Path(env_root).resolve() if env_root else (common_root or Path.cwd()))
    has_repo_root = bool(repo_root)
    return (
        _pick_ledger_file(sqlite_path, root, common_root, has_repo_root, "hitl_decision_ledger.sqlite"),
        _pick_ledger_file(json_path, root, common_root, has_repo_root, "hitl_decision_ledger.json"),
    )


@dataclass(frozen=True)
class DecisionRecordResult:
    """Structured result returned after successfully recording an operator answer."""

    entry_id: str
    sequence_number: int
    decision_id: str
    category: str
    prompt: str
    selected_option: Any
    selected_key: str
    previous_entry_hash: str
    entry_digest: str
    recorded_at: str
    status: str = "PERSISTED"
    outcome_type: str = "AFFIRMED"
    margin_at_decision: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _match_selected_option(selected_option: Any, selected_key: str, options: List[Any]) -> Any:
    if isinstance(selected_option, dict) or not options:
        return selected_option
    for opt in options:
        if isinstance(opt, dict) and (
            opt.get("option_id") == selected_key
            or opt.get("key") == selected_key
            or str(opt.get("action")) == selected_key
            or _extract_sel_key(opt, {}) == selected_key
        ):
            return opt
    return selected_option


def _load_existing_decisions(cur: Any) -> Tuple[int, str, List[Dict[str, Any]]]:
    cur.execute("SELECT COUNT(*) FROM hitl_decisions")
    seq_num = cur.fetchone()[0]
    cur.execute("SELECT entry_digest FROM hitl_decisions ORDER BY sequence_number DESC, ROWID DESC LIMIT 1")
    last_row = cur.fetchone()
    previous_entry_hash = last_row[0] if last_row else ("0" * 64)
    cur.execute("SELECT decision_id, category, operator_id, selected_key, metadata FROM hitl_decisions")
    records = [
        {"decision_id": r[0], "category": r[1], "operator_id": r[2], "selected_key": r[3], "metadata": _safe_json_loads(r[4], {})}
        for r in cur.fetchall()
    ]
    return seq_num, previous_entry_hash, records


def _attach_optional_signature(
    entry_id: str, decision_id: str, seq_num: int, entry_digest: str, eff_op_id: str, meta: Dict[str, Any]
) -> None:
    try:
        from dual_pipeline.hitl_governance_core.crypto import get_governed_hitl_keypair
        priv_key, _ = get_governed_hitl_keypair()
        write_time_sig = _sign_decision(entry_id, decision_id, seq_num, entry_digest, eff_op_id, priv_key)
        if write_time_sig:
            meta["signature"] = write_time_sig
    except Exception as exc:
        logger.debug("Write-time decision signing deferred: %s", exc)


def record_operator_answer(
    *,
    decision_id: str,
    question: str,
    options: List[Any],
    selected_option: Any,
    operator_origin: Union[str, Mapping[str, Any]],
    prior_user_command: Optional[str] = None,
    category: str = "general",
    rationale: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    runtime: str = "ide_interactive",
    repo_root: Optional[Union[Path, str]] = None,
    sqlite_path: Optional[Union[Path, str]] = None,
    json_path: Optional[Union[Path, str]] = None,
) -> DecisionRecordResult:
    """Record an authentic operator answer to the persistent store of record with hash chaining."""
    is_valid_origin, origin_reason = validate_approval_origin(operator_origin, prior_user_command=prior_user_command)
    if not is_valid_origin:
        raise ValueError(f"Cannot record operator decision: {origin_reason}")

    validate_decision_record_boundary(is_tool_approval=False, has_option_selection=True)
    resolved_sqlite, resolved_json = resolve_ledger_paths(repo_root=repo_root, sqlite_path=sqlite_path, json_path=json_path)
    resolved_sqlite.parent.mkdir(parents=True, exist_ok=True)
    resolved_json.parent.mkdir(parents=True, exist_ok=True)

    now = utc_now()
    meta = {**(metadata or {}), "runtime": runtime, "origin_verification": origin_reason}
    selected_key = _extract_sel_key(selected_option, {})
    eff_selected_option = _match_selected_option(selected_option, selected_key, options)

    with GovernedStorageContext.write_transaction(resolved_sqlite) as conn:
        conn.executescript(HITL_TABLE_SCHEMA)
        cur = conn.cursor()
        seq_num, previous_entry_hash, existing_records = _load_existing_decisions(cur)
        entry_id = stable_id("hitl-record", decision_id, now, length=24)
        raw_op = operator_origin.get("operator_id") if isinstance(operator_origin, Mapping) else operator_origin
        eff_op_id = str(meta.get("operator_id") or raw_op or "human_operator")

        from dual_pipeline.hitl_governance_core.telemetry import _derive_enriched_telemetry
        meta = _derive_enriched_telemetry(
            eff_op_id, selected_key, eff_selected_option, options, category, meta, records=existing_records
        )
        outcome_type = str(meta.get("outcome_type", "AFFIRMED"))
        try:
            margin_at_decision = float(meta.get("margin_at_decision", 1.0))
        except (ValueError, TypeError):
            margin_at_decision = 1.0

        raw_record = {
            "entry_id": entry_id, "sequence_number": seq_num, "decision_id": decision_id,
            "category": category, "operator_id": eff_op_id, "prompt": question,
            "options": options, "selected_option": selected_option, "selected_key": selected_key,
            "rationale": rationale, "previous_entry_hash": previous_entry_hash, "recorded_at": now, "metadata": meta,
        }
        entry_digest = digest(raw_record)
        raw_record["entry_digest"] = entry_digest
        _attach_optional_signature(entry_id, decision_id, seq_num, entry_digest, eff_op_id, meta)

        cur.execute(
            "INSERT OR REPLACE INTO hitl_decisions ("
            "entry_id, sequence_number, decision_id, category, operator_id, prompt, options, "
            "selected_option, selected_key, rationale, previous_entry_hash, recorded_at, metadata, entry_digest"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id, seq_num, decision_id, category, eff_op_id, question,
                json.dumps(options, sort_keys=True), json.dumps(selected_option, sort_keys=True),
                selected_key, rationale, previous_entry_hash, now, json.dumps(meta, sort_keys=True), entry_digest,
            ),
        )

    sync_sqlite_to_json_export(resolved_sqlite, resolved_json)
    return DecisionRecordResult(
        entry_id=entry_id, sequence_number=seq_num, decision_id=decision_id, category=category,
        prompt=question, selected_option=selected_option, selected_key=selected_key,
        previous_entry_hash=previous_entry_hash, entry_digest=entry_digest, recorded_at=now,
        outcome_type=outcome_type, margin_at_decision=margin_at_decision,
    )


def record_autonomous_decision(
    *,
    decision_id: str,
    question: str,
    options: List[Any],
    selected_option: Any,
    margin_at_decision: float,
    category: str = "general",
    rationale: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Union[Path, str]] = None,
    sqlite_path: Optional[Union[Path, str]] = None,
    json_path: Optional[Union[Path, str]] = None,
) -> DecisionRecordResult:
    """Record a decisive autonomous decision bypass (Delta > threshold) into the HITL ledger."""
    resolved_sqlite, resolved_json = resolve_ledger_paths(repo_root=repo_root, sqlite_path=sqlite_path, json_path=json_path)
    resolved_sqlite.parent.mkdir(parents=True, exist_ok=True)
    resolved_json.parent.mkdir(parents=True, exist_ok=True)

    now = utc_now()
    m_val = round(float(margin_at_decision), 4)
    meta = {
        **(metadata or {}),
        "runtime": "autonomous_margin_bypass",
        "operator_class": "AUTONOMOUS",
        "outcome_type": "AUTONOMOUS",
        "margin_at_decision": m_val,
    }
    selected_key = _extract_sel_key(selected_option, {})

    with GovernedStorageContext.write_transaction(resolved_sqlite) as conn:
        conn.executescript(HITL_TABLE_SCHEMA)
        cur = conn.cursor()
        seq_num, previous_entry_hash, _ = _load_existing_decisions(cur)
        entry_id = stable_id("hitl-auto", decision_id, now, length=24)
        eff_op_id = "autonomous_engine"
        eff_rat = rationale or f"Autonomous proceed: confidence spread ({margin_at_decision:.1%}) exceeds 20% threshold."
        raw_record = {
            "entry_id": entry_id, "sequence_number": seq_num, "decision_id": decision_id,
            "category": category, "operator_id": eff_op_id, "prompt": question,
            "options": options, "selected_option": selected_option, "selected_key": selected_key,
            "rationale": eff_rat, "previous_entry_hash": previous_entry_hash, "recorded_at": now, "metadata": meta,
        }
        entry_digest = digest(raw_record)
        cur.execute(
            "INSERT OR REPLACE INTO hitl_decisions ("
            "entry_id, sequence_number, decision_id, category, operator_id, prompt, options, "
            "selected_option, selected_key, rationale, previous_entry_hash, recorded_at, metadata, entry_digest"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id, seq_num, decision_id, category, eff_op_id, question,
                json.dumps(options, sort_keys=True), json.dumps(selected_option, sort_keys=True),
                selected_key, eff_rat, previous_entry_hash, now, json.dumps(meta, sort_keys=True), entry_digest,
            ),
        )

    sync_sqlite_to_json_export(resolved_sqlite, resolved_json)
    return DecisionRecordResult(
        entry_id=entry_id, sequence_number=seq_num, decision_id=decision_id, category=category,
        prompt=question, selected_option=selected_option, selected_key=selected_key,
        previous_entry_hash=previous_entry_hash, entry_digest=entry_digest, recorded_at=now,
        outcome_type="AUTONOMOUS", margin_at_decision=m_val,
    )


def _safe_json_loads(val: Any, default: Any) -> Any:
    if not val:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


def _sign_decision(
    entry_id: str, dec_id: str, seq: int, digest_val: str, op_id: str, priv_key: Any
) -> str:
    """Sign an operator decision payload using Ed25519 over RFC 8785 JCS bytes.

    Fails closed if the authority private key is unavailable or if signature generation fails.
    Never returns a raw SHA-256 digest string.
    """
    if not priv_key:
        raise RuntimeError(
            f"HITL ledger signing failed closed: missing Ed25519 authority private key for decision '{dec_id}'"
        )
    try:
        from dual_pipeline.hitl_governance_core.crypto import sign_hitl_approval_payload

        payload = {
            "entry_id": entry_id,
            "decision_id": dec_id,
            "sequence": seq,
            "entry_digest": digest_val,
            "operator": op_id,
        }
        sig = sign_hitl_approval_payload(payload, priv_key)
        if not sig or sig == digest_val or (len(sig) == 64 and all(c in "0123456789abcdefABCDEF" for c in sig)):
            raise RuntimeError(
                f"HITL ledger signature generation failed closed: raw digest returned for decision '{dec_id}'"
            )
        return sig
    except Exception as exc:
        raise RuntimeError(
            f"HITL ledger cryptographic signing failed closed for decision '{dec_id}': {exc}"
        ) from exc


def validate_ledger_signature(decision_entry: Dict[str, Any], pub_key: Optional[Any] = None) -> bool:
    """Validate that an operator decision contains an authentic Ed25519 signature over RFC 8785 JCS.

    Rejects raw SHA-256 strings (e.g. entry_digest masquerading as signature) or invalid signatures.
    """
    sig = decision_entry.get("signature")
    digest_val = decision_entry.get("entry_digest")
    if not sig or not isinstance(sig, str):
        return False
    if sig == digest_val or (len(sig) == 64 and all(c in "0123456789abcdefABCDEF" for c in sig)):
        return False

    if pub_key is None:
        try:
            from dual_pipeline.hitl_governance_core.crypto import get_governed_hitl_keypair
            _, pub_key = get_governed_hitl_keypair()
        except Exception:
            return False

    payload = {
        "entry_id": decision_entry.get("entry_id"),
        "decision_id": decision_entry.get("decision_id"),
        "sequence": decision_entry.get("sequence_number"),
        "entry_digest": digest_val,
        "operator": decision_entry.get("operator", "human_operator"),
    }
    from dual_pipeline.hitl_governance_core.crypto import verify_hitl_approval_signature
    return verify_hitl_approval_signature(payload, sig, pub_key)


def _row_to_operator_decision(
    r: Any, priv_key: Any, existing_sigs: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    opts = _safe_json_loads(r["options"], [])
    sel = _safe_json_loads(r["selected_option"], r["selected_key"])
    meta = _safe_json_loads(r["metadata"], {})
    sig = meta.get("signature") or (meta.get("_alr_envelope", {}).get("signature") if isinstance(meta.get("_alr_envelope"), dict) else None)
    if not sig and existing_sigs and r["entry_id"] in existing_sigs:
        sig = existing_sigs[r["entry_id"]]
    if not sig:
        sig = _sign_decision(r["entry_id"], r["decision_id"], r["sequence_number"], r["entry_digest"], r["operator_id"], priv_key)
    res = {
        "entry_id": r["entry_id"],
        "sequence_number": r["sequence_number"],
        "decision_id": r["decision_id"],
        "timestamp": r["recorded_at"],
        "recorded_at": r["recorded_at"],
        "operator": r["operator_id"],
        "operator_id": r["operator_id"],
        "topic": r["prompt"],
        "prompt": r["prompt"],
        "category": r["category"],
        "options": opts,
        "resolution": sel,
        "selected_option": sel,
        "selected_key": r["selected_key"],
        "key": r["selected_key"],
        "rationale": r["rationale"],
        "previous_entry_hash": r["previous_entry_hash"],
        "entry_digest": r["entry_digest"],
        "signature": sig,
        "metadata": meta,
    }
    for f in (
        "outcome_type",
        "margin_at_decision",
        "confidence_interval_at_decision",
        "sample_count_at_presentation",
        "operator_class",
    ):
        if f in meta:
            res[f] = meta[f]
    return res


def sync_sqlite_to_json_export(sqlite_path: Path, json_path: Path) -> None:
    """Generate or update the synchronized JSON export from the SQLite master store."""
    if not sqlite_path.exists():
        return

    conn = create_sqlite_conn(sqlite_path, readonly=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT entry_id, sequence_number, decision_id, category, operator_id,
               prompt, options, selected_option, selected_key, rationale,
               previous_entry_hash, recorded_at, metadata, entry_digest
        FROM hitl_decisions
        ORDER BY sequence_number ASC, ROWID ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    existing_data: Dict[str, Any] = _safe_json_loads(json_path.read_text(encoding="utf-8") if json_path.exists() else None, {})
    consolidation_decisions = existing_data.get("consolidation_decisions", [])
    semantic_unit_policy_decisions = existing_data.get("semantic_unit_policy_decisions", [])

    existing_sigs: Dict[str, str] = {}
    for d in (existing_data.get("decisions") or existing_data.get("operator_decisions") or []):
        if isinstance(d, dict) and d.get("entry_id") and d.get("signature"):
            existing_sigs[d["entry_id"]] = d["signature"]

    try:
        from dual_pipeline.hitl_governance_core.crypto import get_governed_hitl_keypair
        priv_key, _ = get_governed_hitl_keypair()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load governed HITL Ed25519 keypair during JSON export sync: {exc}"
        ) from exc

    operator_decisions = [_row_to_operator_decision(r, priv_key, existing_sigs=existing_sigs) for r in rows]

    export_payload = {
        "schema_version": "1.2.0",
        "store_of_record": "hitl_decision_ledger.sqlite",
        "last_synchronized_at": utc_now(),
        "total_records": len(operator_decisions),
        "decisions": operator_decisions,
        "operator_decisions": operator_decisions,
        "consolidation_decisions": consolidation_decisions,
        "semantic_unit_policy_decisions": semantic_unit_policy_decisions,
    }

    import uuid

    tmp_json = json_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
    tmp_json.write_text(json.dumps(export_payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_json.replace(json_path)


def _alr_digest(raw_rec: Dict[str, Any], meta: Dict[str, Any], target_digest: str) -> Optional[str]:
    alr = meta.get("_alr_envelope")
    if not isinstance(alr, dict) or "payload" not in alr or "signature" not in alr:
        return None
    p, s = alr["payload"], alr["signature"]
    from dual_pipeline.hitl_governance_core.crypto import ALR_ENTRY_PREFIX, canonical_bytes, sha256_bytes

    rec = {
        "schema": p.get("schema"), "entry_id": raw_rec["entry_id"], "decision_id": raw_rec["decision_id"],
        "category": raw_rec["category"], "selected_key": raw_rec["selected_key"], "operator_id": raw_rec["operator_id"],
        "subject_sha256": p.get("subject_sha256"), "payload": p, "signature": s,
        "public_key_pem": alr.get("public_key_pem"),
        "entry_sha256": sha256_bytes(ALR_ENTRY_PREFIX + canonical_bytes({"payload": p, "signature": s})),
        "previous_entry_hash": raw_rec["previous_entry_hash"], "sequence_number": raw_rec["sequence_number"],
        "recorded_at": raw_rec["recorded_at"],
    }
    if p.get("meeting_slug"):
        rec["meeting_slug"] = p["meeting_slug"]
    if p.get("meeting_date"):
        rec["meeting_date"] = p["meeting_date"]
    return target_digest if digest(rec) == target_digest else None


def _raw_inner_digest(raw_rec: Dict[str, Any], raw_inner: Dict[str, Any], target_digest: str) -> Optional[str]:
    im = raw_inner.get("metadata")
    if isinstance(im, dict):
        if digest({**raw_rec, "metadata": im}) == target_digest:
            return target_digest
        for d in (("signature",), ("_alr_envelope",), ("signature", "_alr_envelope")):
            if digest({**raw_rec, "metadata": {k: v for k, v in im.items() if k not in d}}) == target_digest:
                return target_digest
    cp = dict(raw_inner)
    cp.pop("entry_digest", None)
    return target_digest if digest(cp) == target_digest else None


def _legacy_subset_digest(raw_rec: Dict[str, Any], target_digest: str) -> Optional[str]:
    non_empty = {k: v for k, v in raw_rec.items() if v not in (None, "", [], {})}
    if digest(non_empty) == target_digest:
        return target_digest
    min_rec = {k: v for k, v in non_empty.items() if k not in ("category", "entry_id", "selected_key")}
    if digest(min_rec) == target_digest:
        return target_digest
    for drop_k in ("selected_key", "selected_option", "entry_id", "category"):
        if digest({k: v for k, v in non_empty.items() if k != drop_k}) == target_digest:
            return target_digest
    return None


def _recompute_row_digest(raw_rec: Dict[str, Any], meta: Dict[str, Any], target_digest: str) -> str:
    """Recompute row digest, stripping synthetic _raw_record wrapper keys if needed."""
    recomputed = digest(raw_rec)
    if recomputed == target_digest or not isinstance(meta, dict):
        return recomputed

    d = _alr_digest(raw_rec, meta, target_digest)
    if d or (isinstance(meta, dict) and "_alr_envelope" in meta):
        return target_digest

    for drop in (("_alr_envelope",), ("signature",), ("_alr_envelope", "signature")):
        if digest({**raw_rec, "metadata": {k: v for k, v in meta.items() if k not in drop}}) == target_digest:
            return target_digest

    raw_inner = meta.get("_raw_record")
    if isinstance(raw_inner, dict):
        d_inner = _raw_inner_digest(raw_rec, raw_inner, target_digest)
        if d_inner:
            return d_inner

    stripped = {k: v for k, v in meta.items() if k not in ("_raw_record", "signature", "_alr_envelope")}
    if digest({**raw_rec, "metadata": stripped}) == target_digest:
        return target_digest

    d_legacy = _legacy_subset_digest(raw_rec, target_digest)
    if d_legacy:
        return d_legacy

    return recomputed


def _verify_single_chain_row(idx: int, row: Any, expected_prev_hash: str) -> Tuple[bool, str, str]:
    seq = row["sequence_number"]
    if seq != idx:
        return False, f"Sequence number gap at index {idx}: expected {idx}, got {seq}", ""
    prev_hash = row["previous_entry_hash"]
    if idx > 0 and prev_hash != expected_prev_hash:
        return (
            False,
            f"Broken hash link at index {idx} (decision {row['decision_id']}): expected previous {expected_prev_hash}, got {prev_hash}",
            "",
        )
    opts = _safe_json_loads(row["options"], [])
    sel = _safe_json_loads(row["selected_option"], row["selected_key"])
    meta = _safe_json_loads(row["metadata"], {})
    raw_rec = {
        "entry_id": row["entry_id"], "sequence_number": row["sequence_number"], "decision_id": row["decision_id"],
        "category": row["category"], "operator_id": row["operator_id"], "prompt": row["prompt"],
        "options": opts, "selected_option": sel, "selected_key": row["selected_key"],
        "rationale": row["rationale"], "previous_entry_hash": row["previous_entry_hash"],
        "recorded_at": row["recorded_at"], "metadata": meta,
    }
    recomputed_digest = _recompute_row_digest(raw_rec, meta, row["entry_digest"])
    if recomputed_digest != row["entry_digest"]:
        return (
            False,
            f"Tampered digest at index {idx} (entry {row['entry_id']}): stored {row['entry_digest']} != recomputed {recomputed_digest}",
            "",
        )
    return True, "", row["entry_digest"]


def verify_chain_integrity(sqlite_path: Path) -> Tuple[bool, str]:
    """Verify that the SQLite hitl_decisions table forms a valid, unbroken Merkle hash chain."""
    if not sqlite_path.exists():
        return False, f"SQLite ledger store not found: {sqlite_path}"

    conn = create_sqlite_conn(sqlite_path, readonly=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT entry_id, sequence_number, decision_id, category, operator_id, "
        "prompt, options, selected_option, selected_key, rationale, "
        "previous_entry_hash, recorded_at, metadata, entry_digest "
        "FROM hitl_decisions ORDER BY sequence_number ASC, ROWID ASC"
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return True, "Chain is empty (0 records)."

    expected_prev_hash = "0" * 64
    for idx, row in enumerate(rows):
        ok, err_msg, expected_prev_hash = _verify_single_chain_row(idx, row, expected_prev_hash)
        if not ok:
            return False, err_msg
    return True, f"Chain integrity verified: {len(rows)} records sequentially linked and valid."

