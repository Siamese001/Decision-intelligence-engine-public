"""Source and extraction run provisioning for GovernedGraphWriter."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from storage.database import sqlite3

from ..core import digest, normalize_iso_date, sha256_bytes, stable_id
from .helpers import _resolve_evidence_fingerprints, _table_exists, _verify_evidence_row_identity

_DEGENERATE_SOURCE_NAMES = frozenset(
    {"unknown", "unspecified", "meeting-unknown", "meeting-unspecified", "meeting unknown"}
)
_GENERIC_CONTAINER_STEMS = frozenset(
    {"source_document", "intake_package", "metadata", "forensic_transcript", "transcript", "document", "unknown"}
)
_CONTAINER_FILES = frozenset(
    {"source_document.txt", "source_document.md", "intake_package.json", "metadata.json", "forensic_transcript.md"}
)


def sanitize_cold_archive_ref(ref: str | None) -> str | None:
    """Strip ephemeral .staging_* directory prefixes from cold_archive_ref."""
    if not ref:
        return ref
    s = re.sub(r"(?:^|/)\.staging_([A-Za-z0-9_]+?)_(?:[0-9a-fA-F]{8,}|\d{8,})(?=/|$)", r"output/\1", str(ref).strip())
    return re.sub(r"(?:^|/)\.staging_[^/]+/", "output/", s)


def normalize_source_archive_stem(ref: str | None) -> str:
    """Extract normalized container/file stem across worktree, staging, and local input paths."""
    if not ref:
        return ""
    s = str(ref).strip().replace("\\", "/").removeprefix("local://")
    s = re.sub(r"^.*?/\.worktrees/[^/]+/", "", s)
    s = re.sub(r"(?:^|/)\.staging_([A-Za-z0-9_]+?)_(?:[0-9a-fA-F]{8,}|\d{8,})(?=/|$)", r"/\1", s)
    s = re.sub(r"(?:^|/)\.staging_[^/]+/", "/", s)
    p = Path(s)
    is_date_parent = bool(re.fullmatch(r"\d{2}[-_]\d{2}[-_]\d{4}", p.parent.name))
    is_container_file = p.name.lower() in _CONTAINER_FILES or bool(re.match(r"^0[123]_", p.name))
    use_parent = (
        is_container_file
        and bool(p.parent.name)
        and not p.parent.name.startswith(".")
        and not is_date_parent
        and p.parent.name.lower() not in {"input", "output"}
    )
    stem = re.sub(r"^0[123]_", "", p.parent.name if use_parent else p.stem)
    stem = re.sub(r"_(?:hitl_resolved_transcript|executive_recap|canonical_signal_projection)$", "", stem, flags=re.I)
    stem = re.sub(r"_\d{2}[-_]\d{2}[-_]\d{4}(?:_[0-9A-Za-z_]+)?$", "", stem)
    norm = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return "" if norm in _GENERIC_CONTAINER_STEMS else norm


def _title_tokens(title: str | None) -> set[str]:
    if not title:
        return set()
    cleaned = re.sub(r"\b(?:document|meeting|\d{8}|\d{4}-\d{2}-\d{2})\b", " ", str(title).lower())
    return {t for t in re.findall(r"[a-z0-9]{3,}", cleaned) if t not in {"the", "and", "for", "with", "updates", "update"}}


def _is_degenerate_source_metadata(proposed_id: str, source_name: str) -> bool:
    if not source_name or source_name.lower() in _DEGENERATE_SOURCE_NAMES:
        return True
    if proposed_id == "source-cb6118d820a3ebd9b8e6c576":
        return True
    return bool(re.fullmatch(r"document\s+[0-9a-f]{12}", source_name, re.I))


def _row_matches_incoming_source(
    row: Any,
    incoming_stem: str,
    incoming_date: str | None,
    incoming_tokens: set[str],
) -> bool:
    if incoming_date and normalize_iso_date(row["source_date"]) not in (None, incoming_date):
        return False
    cand_stem = normalize_source_archive_stem(row["cold_archive_ref"])
    if incoming_stem and cand_stem and incoming_stem == cand_stem and len(incoming_stem) >= 6:
        return True
    cand_date = normalize_iso_date(row["source_date"])
    if not (incoming_date and cand_date and incoming_date == cand_date and incoming_tokens):
        return False
    cand_tokens = _title_tokens(row["source_name"])
    if not cand_tokens:
        return False
    inter = len(incoming_tokens & cand_tokens)
    overlap = inter / max(1, min(len(incoming_tokens), len(cand_tokens)))
    if overlap >= 0.70 and inter >= 2:
        return True
    return overlap >= 0.80 and inter >= 2


def _rebind_source_id_if_needed(
    connection: sqlite3.Connection,
    source: dict[str, Any],
) -> bool:
    orig_source_id = source["source_id"]
    canonical_source_id = SourceOpsMixin.resolve_canonical_source_id(connection, source)
    if canonical_source_id == orig_source_id:
        return False
    source["source_id"] = canonical_source_id
    for ev in source.get("evidence", []):
        if isinstance(ev, dict):
            ev["source_id"] = canonical_source_id
    return True


def _verify_source_row_identity(
    source_row: Any,
    expected_name: str,
    was_rebound: bool,
) -> None:
    if source_row["source_type"] != "dual_pipeline_intake":
        raise RuntimeError("source identity collides with different governed source metadata")
    if source_row["source_name"] != expected_name and not was_rebound:
        raise RuntimeError("source identity collides with different governed source metadata")


def _resolve_source_date_status(source: dict[str, Any], source_date: str | None) -> tuple[str, str]:
    """Resolve source_date_status and notes distinguishing file upload date vs actual content date."""
    default_notes = "Governed NEE dual-pipeline source package."
    if not source_date:
        return "unknown", default_notes
    raw_status = str(source.get("date_status") or source.get("source_date_status") or "").strip()
    is_upload_only = bool(source.get("upload_date") and not source.get("actual_date"))
    if raw_status == "estimated_from_source_snapshot" or is_upload_only or source.get("source_posture") == "citation_only_weekly_snapshot":
        return (
            "estimated_from_source_snapshot",
            f"Governed NEE dual-pipeline source package (file upload/export date: {source_date}; content reflects undated snapshot notes).",
        )
    if raw_status in {"exact", "unknown"}:
        return raw_status, default_notes
    return "exact", default_notes


class SourceOpsMixin:
    """Provides source and extraction run lifecycle methods."""

    @staticmethod
    def resolve_canonical_source_id(
        connection: sqlite3.Connection,
        source: dict[str, Any],
    ) -> str:
        """Return existing canonical source_id when re-ingesting the same logical document/meeting."""
        proposed_id = str(source["source_id"])
        source_name = str(source.get("name") or "").strip()
        if _is_degenerate_source_metadata(proposed_id, source_name):
            raise RuntimeError(
                f"Degenerate source metadata rejected (source_id={proposed_id!r}, name={source_name!r})."
            )
        exact = connection.execute(
            "SELECT source_id FROM source WHERE source_id=?", (proposed_id,)
        ).fetchone()
        if exact is not None:
            return proposed_id
        if source.get("source_version_id"):
            exact_sv = connection.execute("SELECT source_id FROM source_version WHERE source_version_id=?", (str(source["source_version_id"]),)).fetchone()
            if exact_sv is not None:
                return str(exact_sv[0])

        incoming_stem = normalize_source_archive_stem(source.get("source_location"))
        incoming_date = normalize_iso_date(source.get("date"))
        incoming_tokens = _title_tokens(source_name)

        rows = connection.execute(
            "SELECT source_id, source_name, source_date, cold_archive_ref FROM source WHERE source_type='dual_pipeline_intake'"
        ).fetchall()
        for row in rows:
            if _row_matches_incoming_source(row, incoming_stem, incoming_date, incoming_tokens):
                return str(row["source_id"])
        return proposed_id

    def _ensure_source(
        self,
        connection: sqlite3.Connection,
        source: dict[str, Any],
        now: str,
    ) -> None:
        was_rebound = _rebind_source_id_if_needed(connection, source)
        source_id = source["source_id"]
        source_version_id = source["source_version_id"]
        package_digest = source.get("package_digest") or digest(source)
        if len(package_digest) != 64:
            package_digest = digest(package_digest)
        content_hash = source.get("source_content_digest") or package_digest
        if len(content_hash) != 64:
            content_hash = digest(content_hash)
        source_date = normalize_iso_date(source.get("date"))
        source_date_status, source_notes = _resolve_source_date_status(source, source_date)
        connection.execute(
            """INSERT OR IGNORE INTO source
               (source_id,source_type,source_name,source_date,authority_rank,source_posture,cold_archive_ref,notes,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                source_id, "dual_pipeline_intake", source.get("name") or source_id, source_date,
                int(source.get("authority_rank", 5)), source.get("source_posture", "derivative_internal"),
                sanitize_cold_archive_ref(source.get("source_location")), source_notes, now,
            ),
        )
        source_row = connection.execute(
            "SELECT source_type,source_name FROM source WHERE source_id=?", (source_id,)
        ).fetchone()
        _verify_source_row_identity(source_row, source.get("name") or source_id, was_rebound)
        existing = connection.execute(
            "SELECT source_id,content_hash FROM source_version WHERE source_version_id=?",
            (source_version_id,),
        ).fetchone()
        if existing and (
            existing["source_id"] != source_id
            or existing["content_hash"] not in {content_hash, package_digest}
        ):
            raise RuntimeError("source version is bound to different immutable content")
        if not existing:
            version_number = connection.execute(
                "SELECT coalesce(max(version_number),0)+1 FROM source_version WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO source_version
                   (source_version_id,source_id,version_number,source_fingerprint,content_hash,
                    source_date_normalized,source_date_precision,source_date_status,authority_rank,
                    scope_text,posture_text,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source_version_id,
                    source_id,
                    version_number,
                    package_digest,
                    content_hash,
                    source_date,
                    "day" if source_date else "unknown",
                    source_date_status,
                    int(source.get("authority_rank", 5)),
                    "Governed source-bound candidate intake.",
                    source.get("source_posture", "derivative_internal"),
                    now,
                ),
            )
        if _table_exists(connection, "source_label_state"):
            posture = str(source.get("source_posture", "derivative_internal"))
            source_origin = "external" if posture.startswith("external_") else "internal"
            connection.execute(
                """INSERT OR IGNORE INTO source_label_state
                   (source_id,source_kind,source_format,source_origin,source_posture_code,
                    source_posture_note,mapping_state,mapping_rule_id,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    source_id,
                    "governed_intake",
                    "json",
                    source_origin,
                    posture,
                    None,
                    "native",
                    None,
                    now,
                ),
            )
        evidence_spans = source.get("a2a_evidence_spans")
        if evidence_spans is not None and not isinstance(evidence_spans, dict):
            raise RuntimeError("A2A evidence-span provenance must be an object")
        a2a_source = (
            "a2a_evidence_spans" in source
            or "a2a_source_span_selection" in source
        )
        for evidence in source.get("evidence", []):
            locator = evidence["locator"]
            evidence_id = evidence["evidence_id"]
            direct_quote = None
            if evidence_spans is not None:
                span = evidence_spans.get(evidence_id)
                if (
                    not isinstance(span, dict)
                    or span.get("source_id") != source_id
                    or span.get("source_version_id") != source_version_id
                    or not isinstance(span.get("quote"), str)
                    or not span["quote"].strip()
                    or span.get("quote_sha256")
                    != sha256_bytes(span["quote"].encode("utf-8"))
                    or evidence.get("content_digest") != span["quote_sha256"]
                    or locator.get("quote_digest") != span["quote_sha256"]
                    or span.get("status") != "SELECTED_FOR_HITL"
                ):
                    raise RuntimeError("selected A2A evidence span is not quote-bound")
                direct_quote = span["quote"]
            elif source.get("evidence_quotes") and evidence_id in source["evidence_quotes"]:
                direct_quote = str(source["evidence_quotes"][evidence_id])
            elif evidence.get("direct_quote"):
                direct_quote = str(evidence["direct_quote"])
            evidence_fingerprint, legacy_ev_fp = _resolve_evidence_fingerprints(
                source_id, source_version_id, locator, evidence, a2a_source
            )
            location_status = (
                "exact" if direct_quote is not None or not a2a_source else "unavailable_in_baseline"
            )
            transcript_ts = locator["value"] if locator["kind"] == "timestamp" else evidence.get("transcript_timestamp")
            connection.execute(
                """INSERT OR IGNORE INTO evidence_item
                   (evidence_id,evidence_fingerprint,source_id,source_version_id,locator_kind,
                    locator_value,page_number,section_name,paragraph_number,row_reference,slide_number,
                    transcript_timestamp,direct_quote,evidence_directness,source_authority_rank,
                    extraction_confidence,corroboration_group,location_status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    evidence_fingerprint,
                    source_id,
                    source_version_id,
                    locator["kind"],
                    locator["value"],
                    locator["value"] if locator["kind"] == "page" else None,
                    locator["value"] if locator["kind"] == "section" else None,
                    None,
                    locator["value"] if locator["kind"] in {"cell", "record"} else None,
                    None,
                    transcript_ts,
                    direct_quote,
                    evidence["directness"],
                    int(evidence["authority_rank"]),
                    1.0,
                    stable_id("corroboration", source_version_id, length=24),
                    location_status,
                    now,
                ),
            )
            evidence_row = connection.execute(
                """SELECT evidence_fingerprint,source_id,source_version_id,direct_quote
                   FROM evidence_item WHERE evidence_id=?""",
                (evidence_id,),
            ).fetchone()
            _verify_evidence_row_identity(
                evidence_row, evidence_fingerprint, legacy_ev_fp,
                source_id, source_version_id, a2a_source, direct_quote,
            )
            if _table_exists(connection, "evidence_label_state"):
                connection.execute(
                    """INSERT OR IGNORE INTO evidence_label_state
                       (evidence_id,directness_code,basis_note,mapping_state,
                        mapping_rule_id,updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        evidence_id,
                        evidence["directness"],
                        None,
                        "native",
                        None,
                        now,
                    ),
                )

    @staticmethod
    def _ensure_extraction_run(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        source: dict[str, Any],
        baseline_graph_sha256: str,
        now: str,
    ) -> str:
        source_version_id = source["source_version_id"]
        input_manifest_sha256 = source.get("package_digest") or digest(source)
        if not isinstance(input_manifest_sha256, str) or len(input_manifest_sha256) != 64:
            input_manifest_sha256 = digest(input_manifest_sha256)
        extraction_run_id = stable_id("extraction-run", run_id, source_version_id, input_manifest_sha256, length=28)
        connection.execute(
            """INSERT OR IGNORE INTO extraction_run
               (extraction_run_id,algorithm_version,baseline_graph_sha256,input_manifest_sha256,started_at,completed_at,status,deterministic)
               VALUES (?,?,?,?,?,?,?,?)""",
            (extraction_run_id, "dual-pipeline-structured-adapter-1.0.0", baseline_graph_sha256, input_manifest_sha256, now, now, "completed", 1),
        )
        row = connection.execute(
            "SELECT algorithm_version,baseline_graph_sha256,input_manifest_sha256,status,deterministic FROM extraction_run WHERE extraction_run_id=?",
            (extraction_run_id,),
        ).fetchone()
        expected = ("dual-pipeline-structured-adapter-1.0.0", baseline_graph_sha256, input_manifest_sha256, "completed", 1)
        if row is None or tuple(row) != expected:
            raise RuntimeError("extraction-run identity collides with different lineage")
        return extraction_run_id
