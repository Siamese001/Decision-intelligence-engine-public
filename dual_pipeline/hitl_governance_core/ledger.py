"""
dual_pipeline/hitl_governance_core/ledger.py

Governed persistent store for operator answers and historical feedback learning.
Supports high-performance SQLite indexing alongside legacy JSON storage.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from dual_pipeline.core import digest, file_lock, stable_id, utc_now, write_json
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore

from .bayesian import (
    _accumulate_historical_weights,
    _calibrate_single_option,
    _match_keys,
)
from .operator_roster import (
    derive_operator_class,
    normalize_operator_id,
    stamp_decision_latency,
)
from .surfacing import _option_key, _standardize_option, parse_confidence_score
from .telemetry import _derive_enriched_telemetry, resolve_hitl_ledger_path

logger = logging.getLogger(__name__)


CORE_RECORD_KEYS = frozenset({
    "entry_id",
    "sequence_number",
    "decision_id",
    "category",
    "operator_id",
    "prompt",
    "options",
    "selected_option",
    "selected_key",
    "rationale",
    "previous_entry_hash",
    "recorded_at",
    "metadata",
})


def _find_default_ledger_path() -> Path:
    return resolve_hitl_ledger_path()


def _matches_legacy_or_envelope_digest(expected: str, rec: dict[str, Any], rec_core: dict[str, Any]) -> bool:
    meta = rec.get("metadata")
    if isinstance(meta, dict):
        if "signature" in meta:
            meta_no_sig = {k: v for k, v in meta.items() if k != "signature"}
            if expected == digest({**rec_core, "metadata": meta_no_sig}):
                return True
        if "_alr_envelope" in meta:
            return True
    legacy_v1_keys = ("decision_id", "operator_id", "previous_entry_hash", "prompt", "recorded_at", "selected_option", "sequence_number")
    if expected == digest({k: rec[k] for k in legacy_v1_keys if k in rec}):
        return True
    legacy_auth_keys = ("category", "decision_id", "entry_id", "operator_id", "previous_entry_hash", "recorded_at", "selected_key", "sequence_number")
    if expected == digest({k: rec[k] for k in legacy_auth_keys if k in rec}):
        return True
    return False


def _verify_single_record_digest(rec: dict[str, Any], idx: int) -> None:
    from dual_pipeline.exceptions import LedgerIntegrityTamperedError
    expected = rec.get("entry_digest")
    if not expected:
        return
    rec_sans = {k: v for k, v in rec.items() if k != "entry_digest"}
    rec_core = {k: v for k, v in rec.items() if k in CORE_RECORD_KEYS}
    if expected == digest(rec_sans) or expected == digest(rec_core):
        return
    if not _matches_legacy_or_envelope_digest(expected, rec, rec_core):
        raise LedgerIntegrityTamperedError(
            f"Entry digest mismatch at index {idx} (decision_id: {rec.get('decision_id')}): "
            f"record content was modified after signature."
        )


def _validate_record_chain(records: list[dict[str, Any]]) -> None:
    from dual_pipeline.exceptions import LedgerIntegrityTamperedError

    prev_hash = "0" * 64
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        _verify_single_record_digest(rec, idx)
        prev = rec.get("previous_entry_hash")
        if prev and prev != prev_hash:
            raise LedgerIntegrityTamperedError(
                f"Merkle chain broken at index {idx} (decision_id: {rec.get('decision_id')}): "
                f"previous_entry_hash ({prev}) does not match expected ({prev_hash})."
            )
        prev_hash = rec.get("entry_digest") or prev_hash




def _normalize_record_fields(r: dict[str, Any]) -> None:
    if "operator_id" not in r and "operator" in r:
        r["operator_id"] = r["operator"]
    if "prompt" not in r and "topic" in r:
        r["prompt"] = r["topic"]
    if "selected_option" not in r and "resolution" in r:
        r["selected_option"] = r["resolution"]
    if "recorded_at" not in r and "timestamp" in r:
        r["recorded_at"] = r["timestamp"]
    if "selected_key" not in r and "key" in r:
        r["selected_key"] = r["key"]


def _merge_decisions_into_records(records: list[Any], decisions: list[Any]) -> None:
    existing_keys = {
        r.get("entry_digest") or r.get("entry_id")
        for r in records
        if isinstance(r, dict)
    }
    for r in decisions:
        if not isinstance(r, dict):
            continue
        k = r.get("entry_digest") or r.get("entry_id")
        if not k or k not in existing_keys:
            records.append(r)
            if k:
                existing_keys.add(k)


def _extract_json_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = data.get("operator_decisions")
    decisions = data.get("decisions")
    if not isinstance(records, list) or not records:
        records = decisions if isinstance(decisions, list) else []
    elif isinstance(decisions, list) and decisions:
        _merge_decisions_into_records(records, decisions)

    if not isinstance(records, list):
        return []

    for r in records:
        if isinstance(r, dict):
            _normalize_record_fields(r)
    return records


def _verify_envelope_and_chain(data: dict[str, Any], records: list[dict[str, Any]], ledger_path: Path) -> None:
    if "ledger_digest" not in data:
        return
    from dual_pipeline.exceptions import LedgerIntegrityTamperedError
    expected = data.get("ledger_digest")
    envelope_sans = {k: v for k, v in data.items() if k != "ledger_digest"}
    if expected != digest(envelope_sans):
        raise LedgerIntegrityTamperedError(
            f"Ledger envelope digest mismatch in '{ledger_path}': "
            f"expected {expected}, computed {digest(envelope_sans)}."
        )
    _validate_record_chain(records)



class HITLDecisionStore:
    """Governed persistent store for operator answers and historical feedback learning.

    Hardened with multi-process lock serialization (30s busy timeout) and Merkle audit linking.
    Continuous Bayesian learning applies Beta-binomial updating with anti-contamination weighting.
    """

    def __init__(
        self,
        ledger_path: Path | str | None = None,
        sqlite_path: Path | str | None = None,
        use_sqlite: bool = True,
    ) -> None:
        if ledger_path is None:
            if sqlite_path is not None:
                ledger_path = Path(sqlite_path).with_suffix(".json")
            else:
                ledger_path = _find_default_ledger_path()
        self.ledger_path = Path(ledger_path).resolve()
        self.lock_path = self.ledger_path.with_name(f".{self.ledger_path.name}.lock")
        self.sqlite_store: Optional[HITLSQLiteStore] = None
        self.use_sqlite = bool(use_sqlite)
        if use_sqlite:
            try:
                target_sqlite_path = (
                    Path(sqlite_path).resolve()
                    if sqlite_path
                    else self.ledger_path.with_suffix(".sqlite")
                )
                self.sqlite_store = HITLSQLiteStore(db_path=target_sqlite_path)
                if self.ledger_path.is_file():
                    total_sqlite = self.sqlite_store.total_records()
                    file_records = self._load_records_from_json(verify_integrity=False)
                    if file_records and total_sqlite < len(file_records):
                        if total_sqlite == 0:
                            self.sqlite_store.insert_decisions_batch(file_records)
                        else:
                            existing_digests = {
                                r.get("entry_digest") for r in self.sqlite_store.get_history()
                            }
                            to_insert = [
                                rec
                                for rec in file_records
                                if rec.get("entry_digest") and rec.get("entry_digest") not in existing_digests
                            ]
                            if to_insert:
                                self.sqlite_store.insert_decisions_batch(to_insert)
            except Exception as e:
                logger.warning("Failed to initialize HITL SQLite store: %s", e)
                self.sqlite_store = None
                self.use_sqlite = False

    @contextmanager
    def _ledger_lock(self, timeout_seconds: float = 60.0):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock(self.lock_path, timeout_seconds=timeout_seconds):
                yield
        except TimeoutError as err:
            raise TimeoutError(
                f"HITL_LEDGER_LOCK_TIMEOUT: Failed to acquire lock on {self.lock_path} after {timeout_seconds}s."
            ) from err

    _RECORDS_CACHE: dict[str, tuple[Any, int, list[dict[str, Any]]]] = {}

    def _load_records_from_sqlite(self, verify_integrity: bool) -> list[dict[str, Any]]:
        assert self.sqlite_store is not None
        key = f"sqlite:{self.sqlite_store.db_path}"
        committed_version = None
        try:
            committed_version = self.sqlite_store.get_committed_head_version()
            cached = self._RECORDS_CACHE.get(key)
            if cached and cached[0] == committed_version:
                return cached[2]
        except Exception:
            pass

        records = self.sqlite_store.get_history()
        if verify_integrity:
            try:
                _validate_record_chain(records)
            except Exception as chain_err:
                if self.ledger_path.is_file():
                    logger.warning("Rebuilding corrupted HITL SQLite store from JSON: %s", chain_err)
                    file_records = self._load_records_from_json(verify_integrity=True)
                    self.sqlite_store.rebuild_from_records(file_records)
                    records = file_records
                else:
                    raise
        if committed_version is not None:
            self._RECORDS_CACHE[key] = (committed_version, len(records), records)
        return records

    def _load_records_from_json(self, verify_integrity: bool) -> list[dict[str, Any]]:
        if not self.ledger_path.is_file():
            return []
        try:
            stat = self.ledger_path.stat()
            key = str(self.ledger_path)
            cached = self._RECORDS_CACHE.get(key)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                return cached[2]

            raw_text = self.ledger_path.read_text(encoding="utf-8")
            if not raw_text.strip():
                return []
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                return []

            records = _extract_json_records(data)
            if verify_integrity:
                _verify_envelope_and_chain(data, records, self.ledger_path)

            self._RECORDS_CACHE[key] = (stat.st_mtime, stat.st_size, records)
            return records
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return []

    def _load_records(self, verify_integrity: bool = True) -> list[dict[str, Any]]:
        if self.use_sqlite and self.sqlite_store and self.sqlite_store.total_records() > 0:
            return self._load_records_from_sqlite(verify_integrity)
        return self._load_records_from_json(verify_integrity)

    def _save_records(self, records: list[dict[str, Any]]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema_version": "1.1.0",
            "record_count": len(records),
            "updated_at": utc_now(),
            "genesis_hash": "0" * 64,
            "latest_entry_hash": records[-1].get("entry_digest", digest(records[-1]))
            if records
            else ("0" * 64),
            "decisions": records,
        }
        envelope["ledger_digest"] = digest(envelope)
        write_json(self.ledger_path, envelope)
        try:
            stat = self.ledger_path.stat()
            self._RECORDS_CACHE[str(self.ledger_path)] = (stat.st_mtime, stat.st_size, records)
        except OSError:
            pass
        if self.sqlite_store:
            try:
                self.sqlite_store.insert_decisions_batch(records)
            except Exception as e:
                logger.warning("Failed to batch insert decisions to SQLite: %s", e)

    def export_json_projection(self, output_path: Path | str | None = None) -> Path:
        """Exports the SQLite event store as an on-demand canonical Merkle-chained JSON projection."""
        out_p = Path(output_path).resolve() if output_path else self.ledger_path
        out_p.parent.mkdir(parents=True, exist_ok=True)
        records = self.get_history()
        prev_hash = "0" * 64
        proj_records = []
        for idx, rec in enumerate(records):
            rec_core = {k: v for k, v in rec.items() if k in CORE_RECORD_KEYS}
            rec_core["sequence_number"] = idx
            rec_core["previous_entry_hash"] = prev_hash
            rec_core.pop("entry_digest", None)
            rec_core["entry_digest"] = digest(rec_core)
            prev_hash = rec_core["entry_digest"]
            proj_records.append(rec_core)

        envelope = {
            "schema_version": "1.1.0",
            "record_count": len(proj_records),
            "updated_at": utc_now(),
            "genesis_hash": "0" * 64,
            "latest_entry_hash": prev_hash,
            "decisions": proj_records,
        }
        envelope["ledger_digest"] = digest(envelope)
        write_json(out_p, envelope)
        return out_p

    def record_decision(
        self,
        *,
        decision_id: str,
        category: str,
        prompt: str,
        options: list[Any],
        selected_option: Any,
        rationale: str | None = None,
        operator_id: str = "human_operator",
        operator_class: str | None = None,
        metadata: dict[str, Any] | None = None,
        presented_at: str | None = None,
        resolved_at: str | None = None,
        resolution_latency_ms: int | None = None,
    ) -> dict[str, Any]:
        """Record an operator's resolved decision with Merkle hash linking under lock serialization."""
        with self._ledger_lock():
            now = utc_now()
            norm_operator_id = normalize_operator_id(operator_id)
            eff_operator_class = operator_class or derive_operator_class(
                norm_operator_id, metadata
            )

            latency_info = stamp_decision_latency(
                presented_at=presented_at or (metadata or {}).get("presented_at"),
                resolved_at=resolved_at or (metadata or {}).get("resolved_at"),
                operator_class=eff_operator_class,
                explicit_latency_ms=resolution_latency_ms
                or (metadata or {}).get("resolution_latency_ms"),
            )

            if isinstance(selected_option, dict):
                selected_key = (
                    selected_option.get("key")
                    or selected_option.get("action")
                    or selected_option.get("label")
                    or str(selected_option)
                )
            else:
                selected_key = str(selected_option)

            if self.sqlite_store and self.use_sqlite:
                previous_entry_hash = self.sqlite_store.latest_entry_hash()
                seq_num = self.sqlite_store.count()
            else:
                records = self._load_records()
                previous_entry_hash = (
                    records[-1].get("entry_digest", "0" * 64) if records else ("0" * 64)
                )
                seq_num = len(records)

            meta = _derive_enriched_telemetry(
                operator_id=norm_operator_id,
                selected_key=selected_key,
                selected_option=selected_option,
                options=options,
                category=category,
                metadata=metadata,
                records=[],
            )
            meta["operator_class"] = eff_operator_class
            meta["presented_at"] = latency_info["presented_at"]
            meta["resolved_at"] = latency_info["resolved_at"]
            meta["resolution_latency_ms"] = latency_info["resolution_latency_ms"]
            meta["latency_slo"] = latency_info["latency_slo"]
            meta["latency_slo_status"] = latency_info["latency_slo_status"]

            record = {
                "entry_id": stable_id("hitl-record", decision_id, now, length=24),
                "sequence_number": seq_num,
                "decision_id": decision_id,
                "category": category,
                "operator_id": norm_operator_id,
                "operator_class": eff_operator_class,
                "prompt": prompt,
                "options": options,
                "selected_option": selected_option,
                "selected_key": selected_key,
                "outcome_type": meta["outcome_type"],
                "margin_at_decision": meta["margin_at_decision"],
                "confidence_interval_at_decision": meta["confidence_interval_at_decision"],
                "sample_count_at_presentation": meta["sample_count_at_presentation"],
                "rationale": rationale,
                "previous_entry_hash": previous_entry_hash,
                "recorded_at": now,
                "presented_at": latency_info["presented_at"],
                "resolved_at": latency_info["resolved_at"],
                "resolution_latency_ms": latency_info["resolution_latency_ms"],
                "latency_slo_status": latency_info["latency_slo_status"],
                "metadata": meta,
            }
            rec_core = {k: v for k, v in record.items() if k in CORE_RECORD_KEYS}
            record["entry_digest"] = digest(rec_core)

            if self.sqlite_store and self.use_sqlite:
                self.sqlite_store.insert_decision(record)
                key = f"sqlite:{self.sqlite_store.db_path}"
                cached = self._RECORDS_CACHE.get(key)
                if cached:
                    cached_records = list(cached[2])
                    cached_records.append(record)
                    self._RECORDS_CACHE[key] = (cached[0], cached[1], cached_records)
                try:
                    json_records = self._load_records_from_json(verify_integrity=False)
                    json_records.append(record)
                    self._save_records(json_records)
                except Exception as e:
                    logger.debug("Failed to dual-write JSON ledger record: %s", e)
            else:
                records = self._load_records()
                records.append(record)
                self._save_records(records)

            try:
                self._emit_h3_telemetry(record)
            except Exception as err:
                logger.warning("Failed to emit H3 telemetry: %s", err)

            return record

    def _emit_h3_telemetry(self, record: dict[str, Any]) -> None:
        """Emit H3 learning_event telemetry for the recorded HITL resolution."""
        db_path = self.ledger_path.parent / "telemetry_ledger.sqlite"
        if not db_path.parent.exists():
            return
        with GovernedStorageContext.write_transaction(db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS learning_event (
                   event_id TEXT PRIMARY KEY,
                   run_id TEXT,
                   trace_id TEXT,
                   span_id TEXT,
                   parent_span_id TEXT,
                   use_case TEXT,
                   stage TEXT,
                   event_type TEXT,
                   status TEXT,
                   payload_reference TEXT,
                   payload_digest TEXT,
                   artifact_json TEXT,
                   created_at TEXT
                )"""
            )
            event_id = f"evt-h3-{record['entry_id']}"
            run_id = f"run-hitl-{record['decision_id']}"
            payload_ref = str(self.ledger_path)
            payload_digest = record.get("entry_digest", "")
            artifact_json = json.dumps(
                {
                    "decision_id": record["decision_id"],
                    "category": record["category"],
                    "outcome_type": record["outcome_type"],
                    "selected_key": record["selected_key"],
                    "operator_id": record["operator_id"],
                    "margin_at_decision": record.get("margin_at_decision", 0.0),
                },
                sort_keys=True,
            )
            conn.execute(
                """INSERT OR REPLACE INTO learning_event
                   (event_id, run_id, trace_id, span_id, parent_span_id, use_case, stage,
                    event_type, status, payload_reference, payload_digest, artifact_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    run_id,
                    f"trace-hitl-{record['decision_id']}",
                    f"span-h3-{record['decision_id']}",
                    f"span-h2-{record['decision_id']}",
                    "hitl",
                    "H3",
                    "hitl_decision_resolved",
                    "OK" if record["outcome_type"] in ("AFFIRMED", "AUTONOMOUS") else "OVERRIDDEN",
                    payload_ref,
                    payload_digest,
                    artifact_json,
                    record["recorded_at"],
                ),
            )

    def enrich_legacy_records(self) -> int:
        """Enriches legacy records lacking outcome_type or margin metadata and re-seals Merkle digests."""
        from .legacy_sync import enrich_legacy_records as _enrich

        return _enrich(self)

    def get_history(self, category: str | None = None) -> list[dict[str, Any]]:
        if self.use_sqlite and self.sqlite_store and self.sqlite_store.total_records() > 0:
            return self.sqlite_store.get_history(category)
        with self._ledger_lock():
            records = self._load_records()
        if category:
            return [r for r in records if r.get("category") == category]
        return records

    def calibrate_options(
        self,
        options: list[Any],
        category: str,
        boost_step: float = 0.15,
        penalty_step: float = 0.10,
        half_life_days: float | None = None,
        prior_strength: float | None = None,
        min_bound: float = 0.05,
        max_bound: float = 0.95,
        current_decision_id: str | None = None,
        project: str | None = None,
        subject_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Improve confidence scores using historical operator answers via Bayesian continuous learning with optional time decay."""
        clean_cat = (category or "").strip().lower()
        history = (
            self.get_history(None)
            if clean_cat in ("", "general", "default", "all")
            else (self.get_history(category) or self.get_history(None))
        )
        if not history:
            res = []
            for opt in options:
                item = _standardize_option(opt)
                item["confidence_level"] = "NEUTRAL"
                item.setdefault("prior_affirmations", 0)
                item.setdefault("prior_rejections", 0)
                item.setdefault("historical_adjustment", 0.0)
                item.setdefault("prior_adjustment", 0.0)
                item.setdefault("confidence_sample_count", 0)
                item.setdefault("confidence_is_precise", False)
                res.append(item)
            return res

        now = datetime.now(timezone.utc)
        from dual_pipeline.parameters import get_hitl_bayesian_prior_strength

        eff_prior_strength = (
            prior_strength if prior_strength is not None else get_hitl_bayesian_prior_strength()
        )
        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(
            history, category, half_life_days, now, current_decision_id=current_decision_id,
            project=project, subject_key=subject_key,
        )

        return [
            _calibrate_single_option(
                opt,
                aff_w,
                rej_w,
                aff_c,
                rej_c,
                eff_prior_strength,
                min_bound,
                max_bound,
                half_life_days,
            )
            for opt in options
        ]

    def export_learned_priors(self, output_path: Path | str | None = None) -> dict[str, Any]:
        """Exports an indexed dictionary of learned priors and posterior adjustments for auditing."""
        from .priors import export_learned_priors as _export

        return _export(self, output_path)

    def sift_candidates_programmatic(
        self,
        candidates: Sequence[Mapping[str, Any]],
        category: str | None = None,
        margin_threshold: float | None = None,
    ) -> dict[str, Any]:
        from .priors import sift_candidates_programmatic as _sift

        return _sift(self, candidates, category=category, margin_threshold=margin_threshold)

    def sync_from_legacy_ledger(self, legacy_path: Path | str = "hitl_decision_ledger.json") -> int:
        """Synchronizes legacy unversioned ledger records into this governed store."""
        from .legacy_sync import sync_legacy_ledger_records

        return sync_legacy_ledger_records(legacy_path, self)


def save_hitl_records(ledger_path: Path | str, records: list[dict[str, Any]]) -> None:
    """Save records directly to the specified HITL decision ledger."""
    store = HITLDecisionStore(ledger_path=ledger_path)
    store._save_records(records)


HitlDecisionLedger = HITLDecisionStore
