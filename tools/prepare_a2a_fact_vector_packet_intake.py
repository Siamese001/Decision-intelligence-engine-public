#!/usr/bin/env python3
"""Convert a governed A2A ZIP into source-bound, forced-HITL NEE packages.

This adapter deliberately does not execute archive-supplied scripts or SQL.  It
verifies the archive's hash inventory and its embedded upstream Ed25519 packet
before producing one package per authoritative source plus separate derived
guidance lanes.  It never creates an approved fact package.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    normalize_space,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    slug,
    stable_id,
    write_json_exclusive,
)
from dual_pipeline.source_spans import SourceSpanError, validate_capture_document  # noqa: E402
from dual_pipeline.source_trust import assert_pinned_vendor_host  # noqa: E402
from tools.validate_dual_pipeline_contracts import ContractError  # noqa: E402

CONTRACT_ID = "urn:nee:dual-pipeline:a2a-fact-vector-packet-intake:1.0.0"
PACKET_TRUST_SCOPE = (
    "packet_integrity_only; organizational identity trust must be added by UWG"
)
SOURCE_KIND = "a2a_governed_ingestion_bundle_v3"


def _jsonl(data: bytes, member_name: str) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSONL member: {member_name}") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"JSONL member contains a non-object row: {member_name}")
    return rows


def _single_root(members: Iterable[str]) -> str:
    roots = {member.split("/", 1)[0] for member in members if "/" in member}
    if len(roots) != 1:
        raise ValueError("bundle ZIP must contain exactly one directory root")
    return roots.pop()


def _archive_members(archive: zipfile.ZipFile) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        if info.filename in result:
            raise ValueError(f"duplicate ZIP member: {info.filename}")
        result[info.filename] = archive.read(info)
    return result


def _parse_checksums(value: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = value.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("CHECKSUMS.sha256 must be UTF-8") from exc
    for line in lines:
        digest, separator, path = line.partition("  ")
        if not separator or len(digest) != 64 or not path or path in result:
            raise ValueError("CHECKSUMS.sha256 has an invalid or duplicate entry")
        result[path] = digest
    if not result:
        raise ValueError("CHECKSUMS.sha256 is empty")
    return result


def _verify_upstream_packet(
    upstream_zip: bytes,
    expected_upstream_sha256: str,
) -> dict[str, Any]:
    if sha256_bytes(upstream_zip) != expected_upstream_sha256:
        raise ValueError("embedded upstream ZIP hash does not match BUNDLE_MANIFEST")
    try:
        with zipfile.ZipFile(io.BytesIO(upstream_zip)) as upstream:
            members = _archive_members(upstream)
    except zipfile.BadZipFile as exc:
        raise ValueError("embedded upstream member is not a valid ZIP") from exc
    root = _single_root(members)
    required = {
        "packet_manifest.json",
        "packet_manifest.sig",
        "packet_public_key.pem",
    }
    missing = [name for name in required if f"{root}/{name}" not in members]
    if missing:
        raise ValueError(f"embedded upstream packet is missing required members: {missing}")
    manifest_bytes = members[f"{root}/packet_manifest.json"]
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("embedded upstream packet manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("signature_algorithm") != "Ed25519":
        raise ValueError("embedded upstream packet does not declare Ed25519")
    try:
        public_key = serialization.load_pem_public_key(
            members[f"{root}/packet_public_key.pem"]
        )
    except ValueError as exc:
        raise ValueError("embedded upstream public key is invalid") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("embedded upstream public key is not Ed25519")
    try:
        signature = base64.b64decode(
            members[f"{root}/packet_manifest.sig"].strip(), validate=True
        )
        public_key.verify(signature, manifest_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("embedded upstream Ed25519 signature is invalid") from exc
    declared_files = manifest.get("files")
    if not isinstance(declared_files, list) or not declared_files:
        raise ValueError("embedded upstream manifest has no file inventory")
    for item in declared_files:
        if not isinstance(item, dict):
            raise ValueError("embedded upstream manifest has invalid file inventory")
        path = item.get("path")
        declared_hash = item.get("sha256")
        declared_bytes = item.get("bytes")
        content = members.get(f"{root}/{path}") if isinstance(path, str) else None
        if (
            not isinstance(declared_hash, str)
            or not isinstance(declared_bytes, int)
            or content is None
            or len(content) != declared_bytes
            or sha256_bytes(content) != declared_hash
        ):
            raise ValueError("embedded upstream manifest inventory hash mismatch")
    trust_scope = manifest.get("trust_scope")
    if trust_scope != PACKET_TRUST_SCOPE:
        raise ValueError("embedded upstream packet trust scope is not governance-safe")
    return {
        "packet_id": manifest.get("packet_id"),
        "packet_manifest_sha256": sha256_bytes(manifest_bytes),
        "signature_valid": True,
        "manifest_hashes_valid": True,
        "trust_scope": trust_scope,
    }


def read_and_verify_bundle(zip_path: Path) -> dict[str, Any]:
    """Read exactly one v3 bundle and independently verify its integrity claims."""

    zip_path = zip_path.resolve()
    if not zip_path.is_file():
        raise ValueError("--bundle must identify an existing ZIP file")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = _archive_members(archive)
    except zipfile.BadZipFile as exc:
        raise ValueError("--bundle is not a valid ZIP file") from exc
    root = _single_root(members)
    required = {
        "BUNDLE_MANIFEST.json",
        "CHECKSUMS.sha256",
        "upstream/openai_anthropic_a2a_fact_vector_packet_v2.zip",
        "data/jsonl/sources.jsonl",
        "data/jsonl/evidence_records.jsonl",
        "data/jsonl/fact_candidates.jsonl",
        "data/jsonl/guidance_practices.jsonl",
        "data/jsonl/pattern_guidance.jsonl",
        "data/jsonl/embedding_jobs.jsonl",
    }
    missing = [name for name in required if f"{root}/{name}" not in members]
    if missing:
        raise ValueError(f"bundle is missing required members: {missing}")
    try:
        manifest = json.loads(members[f"{root}/BUNDLE_MANIFEST.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BUNDLE_MANIFEST.json is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("bundle_id") != "nee-a2a-governed-ingestion-v3":
        raise ValueError("unexpected governed bundle identity")
    checksums = _parse_checksums(members[f"{root}/CHECKSUMS.sha256"])
    archive_relative_paths = {
        member[len(root) + 1 :]
        for member in members
        if member.startswith(f"{root}/") and member != f"{root}/CHECKSUMS.sha256"
    }
    if set(checksums) != archive_relative_paths:
        raise ValueError("CHECKSUMS.sha256 does not cover exactly the governed bundle members")
    for path, expected_hash in checksums.items():
        actual = sha256_bytes(members[f"{root}/{path}"])
        if actual != expected_hash:
            raise ValueError(f"bundle member hash mismatch: {path}")
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise ValueError("BUNDLE_MANIFEST.json has no file inventory")
    manifest_hashes = {item.get("path"): item.get("sha256") for item in manifest_files if isinstance(item, dict)}
    if set(manifest_hashes) != archive_relative_paths - {"BUNDLE_MANIFEST.json"}:
        raise ValueError("BUNDLE_MANIFEST file inventory does not cover the expected members")
    for path, expected_hash in manifest_hashes.items():
        if not isinstance(path, str) or not isinstance(expected_hash, str):
            raise ValueError("BUNDLE_MANIFEST file inventory has an invalid entry")
        if sha256_bytes(members[f"{root}/{path}"]) != expected_hash:
            raise ValueError(f"BUNDLE_MANIFEST member hash mismatch: {path}")
    upstream = manifest.get("upstream")
    if not isinstance(upstream, dict):
        raise ValueError("BUNDLE_MANIFEST has no upstream integrity declaration")
    upstream_result = _verify_upstream_packet(
        members[f"{root}/upstream/openai_anthropic_a2a_fact_vector_packet_v2.zip"],
        upstream.get("upstream_zip_sha256", ""),
    )
    if (
        upstream.get("upstream_signature_valid") is not True
        or upstream.get("upstream_manifest_file_hashes_valid") is not True
        or upstream.get("upstream_trust_scope") != PACKET_TRUST_SCOPE
    ):
        raise ValueError("BUNDLE_MANIFEST upstream integrity declaration is inconsistent")
    data = {
        name: _jsonl(members[f"{root}/data/jsonl/{name}.jsonl"], name)
        for name in (
            "sources",
            "evidence_records",
            "fact_candidates",
            "guidance_practices",
            "pattern_guidance",
            "embedding_jobs",
        )
    }
    return {
        "bundle_path": zip_path,
        "bundle_sha256": sha256_file(zip_path),
        "bundle_manifest_sha256": sha256_bytes(members[f"{root}/BUNDLE_MANIFEST.json"]),
        "manifest": manifest,
        "upstream": upstream_result,
        "data": data,
    }


def _load_fresh_capture_manifest(
    path: Path, verified: dict[str, Any], source_ids: set[str], evidence_ids: set[str]
) -> dict[str, Any]:
    """Validate raw first-party captures before binding them into intake packages."""

    path = path.resolve()
    if not path.is_file():
        raise ValueError("--fresh-capture-manifest must identify an existing manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("fresh capture manifest is invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "1.0.0"
        or value.get("status") != "PASS"
        or value.get("bundle", {}).get("sha256") != verified["bundle_sha256"]
        or value.get("bundle", {}).get("bundle_manifest_sha256")
        != verified["bundle_manifest_sha256"]
    ):
        raise ValueError("fresh capture manifest is not bound to this verified bundle")
    captures = value.get("captures")
    if not isinstance(captures, list):
        raise ValueError("fresh capture manifest has no captures")
    by_source: dict[str, dict[str, Any]] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    packet_evidence_by_id = {
        row.get("evidence_id"): row for row in verified["data"]["evidence_records"]
    }
    if None in packet_evidence_by_id or len(packet_evidence_by_id) != len(
        verified["data"]["evidence_records"]
    ):
        raise ValueError("verified bundle has invalid evidence identifiers")
    for capture in captures:
        source_id = capture.get("source_id") if isinstance(capture, dict) else None
        if (
            not isinstance(capture, dict)
            or source_id not in source_ids
            or source_id in by_source
            or capture.get("status") != "CAPTURED"
            or not isinstance(capture.get("raw_sha256"), str)
            or not isinstance(capture.get("visible_text_sha256"), str)
            or not isinstance(capture.get("final_url"), str)
            or not isinstance(capture.get("captured_at"), str)
        ):
            raise ValueError("fresh capture manifest has an invalid source capture")
        raw_relative = capture.get("raw_path")
        if not isinstance(raw_relative, str) or Path(raw_relative).is_absolute():
            raise ValueError("fresh capture raw path must be relative")
        raw_path = (path.parent / raw_relative).resolve()
        try:
            raw_path.relative_to(path.parent)
        except ValueError as exc:
            raise ValueError("fresh capture raw path escapes its manifest directory") from exc
        if not raw_path.is_file() or sha256_file(raw_path) != capture["raw_sha256"]:
            raise ValueError("fresh capture raw bytes are missing or hash-mismatched")
        try:
            document = validate_capture_document(capture, raw_path.read_bytes())
        except SourceSpanError as exc:
            raise ValueError("fresh capture raw bytes do not reproduce its visible text") from exc
        by_source[source_id] = capture
        evidence = capture.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("fresh capture has no evidence results")
        for result in evidence:
            evidence_id = result.get("evidence_id") if isinstance(result, dict) else None
            if evidence_id not in evidence_ids or evidence_id in evidence_by_id:
                raise ValueError("fresh capture evidence results are invalid")
            packet_evidence = packet_evidence_by_id[evidence_id]
            preview = packet_evidence.get("excerpt_preview")
            has_preview = packet_evidence.get("has_exact_excerpt_preview") is True
            if has_preview != isinstance(preview, str):
                raise ValueError("fresh capture evidence preview shape does not match the packet")
            if has_preview:
                normalized_preview = normalize_space(preview)
                expected_digest = packet_evidence.get("exact_excerpt_sha256")
                actual_digest = sha256_bytes(normalized_preview.encode("utf-8"))
                preview_offset = document.visible_text.find(normalized_preview)
                verified_preview = (
                    isinstance(expected_digest, str)
                    and actual_digest == expected_digest
                    and preview_offset >= 0
                )
                expected_match = "MATCH" if verified_preview else "MISSING"
                if (
                    result.get("preview_available") is not True
                    or result.get("preview_match") != expected_match
                    or result.get("normalized_preview_sha256") != actual_digest
                    or result.get("recorded_exact_excerpt_sha256") != expected_digest
                    or result.get("preview_hash_matches_packet")
                    is not (actual_digest == expected_digest)
                    or result.get("preview_offset")
                    != (preview_offset if verified_preview else None)
                ):
                    raise ValueError(
                        "fresh capture preview result is not bound to the packet hash and visible offsets"
                    )
            elif (
                result.get("preview_available") is not False
                or result.get("preview_match")
                != "NOT_APPLICABLE_LOCATOR_REQUIRES_HITL_SPAN_SELECTION"
            ):
                raise ValueError("fresh capture locator-only evidence result is invalid")
            evidence_by_id[evidence_id] = result
    if set(by_source) != source_ids or set(evidence_by_id) != evidence_ids:
        raise ValueError("fresh capture manifest does not cover the complete source/evidence set")
    return {
        "manifest_path": str(path),
        "manifest_sha256": sha256_file(path),
        "capture_id": value.get("capture_id"),
        "captures": by_source,
        "evidence": evidence_by_id,
    }


def _source_date(value: str) -> str:
    date = value.split("T", 1)[0]
    if len(date) != 10:
        raise ValueError("source record has no ISO source date")
    return date


def _locator(evidence: dict[str, Any], url: str) -> dict[str, str]:
    line_start = evidence.get("line_start")
    line_end = evidence.get("line_end")
    anchor = evidence.get("anchor_hint")
    if not isinstance(line_start, int) or not isinstance(line_end, int) or not isinstance(anchor, str):
        raise ValueError(f"evidence record has no usable locator: {evidence.get('evidence_id')}")
    result = {
        "kind": "uri_fragment",
        "value": f"{url}#{anchor}:L{line_start}-L{line_end}",
    }
    if evidence.get("has_exact_excerpt_preview") is True:
        quote_digest = evidence.get("exact_excerpt_sha256")
        if not isinstance(quote_digest, str):
            raise ValueError("text-bearing evidence has no exact excerpt hash")
        result["quote_digest"] = quote_digest
    return result


def _evidence_reference(
    evidence: dict[str, Any],
    *,
    source_id: str,
    source_version_id: str,
    url: str,
    authority_rank: int,
    fresh_capture_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preview = evidence.get("excerpt_preview")
    has_preview = evidence.get("has_exact_excerpt_preview") is True
    if has_preview != isinstance(preview, str):
        raise ValueError(f"evidence preview flag mismatch: {evidence.get('evidence_id')}")
    digest = evidence.get("exact_excerpt_sha256") if has_preview else evidence.get("evidence_span_sha256")
    if not isinstance(digest, str):
        raise ValueError(f"evidence has no stable digest: {evidence.get('evidence_id')}")
    if fresh_capture_evidence is None:
        verification_state = (
            "captured_excerpt_present_refetch_required"
            if has_preview
            else "locator_only_refetch_required"
        )
    else:
        outcome = fresh_capture_evidence.get("preview_match")
        if outcome == "MATCH" and has_preview:
            verification_state = "fresh_capture_verified"
        elif outcome == "MISSING" and has_preview:
            verification_state = "fresh_capture_preview_mismatch"
        elif (
            outcome == "NOT_APPLICABLE_LOCATOR_REQUIRES_HITL_SPAN_SELECTION"
            and not has_preview
        ):
            verification_state = "fresh_capture_locator_pending"
        else:
            raise ValueError(
                f"fresh capture outcome does not match source evidence: {evidence.get('evidence_id')}"
            )
    return {
        "evidence_id": evidence["evidence_id"],
        "source_id": source_id,
        "source_version_id": source_version_id,
        "locator": _locator(evidence, url),
        "relationship": "supports",
        "directness": "direct" if has_preview else "contextual",
        "authority_rank": authority_rank,
        "content_digest": digest,
        "verification_state": verification_state,
    }


def _mention(candidate_id: str, text: str, context: str, *, stable_identifier: str | None = None) -> dict[str, Any]:
    return {
        "mention_id": stable_id("mention", candidate_id, text, length=24),
        "reported_text": text,
        "stable_identifier": stable_identifier,
        "context": context,
    }


def _package(
    *,
    verified: dict[str, Any],
    source: dict[str, Any],
    evidence: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    lineage_kind: str,
    fresh_capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not evidence or not candidates:
        raise ValueError("A2A intake package needs evidence and candidates")
    source_id = source["source_id"]
    artifact = {
        "contract_id": CONTRACT_ID,
        "schema_version": "1.0.0",
        "artifact_id": (
            f"a2a-captured-packet-{slug(source_id)}"
            if fresh_capture is not None
            else f"a2a-packet-{slug(source_id)}"
        ),
        "packet": {
            "packet_id": verified["upstream"]["packet_id"],
            "source_zip_sha256": verified["bundle_sha256"],
            "packet_manifest_sha256": verified["bundle_manifest_sha256"],
            "signature_status": "verified_integrity_only",
            "trust_scope": PACKET_TRUST_SCOPE,
        },
        "source": source,
        "evidence": evidence,
        "candidates": candidates,
        "adapter": {"name": "nee-a2a-fact-vector-packet", "version": "1.0.0"},
        "extensions": {
            "com.partner_advisory.nee.a2a-packet-lineage": {
                "adapter_source_kind": SOURCE_KIND,
                "lineage_kind": lineage_kind,
                "outer_bundle_filename": verified["bundle_path"].name,
                "outer_bundle_sha256": verified["bundle_sha256"],
                "outer_bundle_manifest_sha256": verified["bundle_manifest_sha256"],
                "upstream_packet_id": verified["upstream"]["packet_id"],
                "upstream_packet_manifest_sha256": verified["upstream"]["packet_manifest_sha256"],
                "upstream_signature_valid": True,
                "upstream_manifest_hashes_valid": True,
                "upstream_trust_scope": PACKET_TRUST_SCOPE,
                "fresh_first_party_capture_included": fresh_capture is not None,
                "automatic_fact_promotion_enabled": False,
            },
        },
    }
    if fresh_capture is not None:
        artifact["extensions"]["com.partner_advisory.nee.a2a-fresh-capture"] = fresh_capture
    artifact = seal_artifact(artifact)
    validate_artifact(artifact)
    return artifact


def _authoritative_source(
    record: dict[str, Any],
    bundle_sha256: str,
    audiences: list[str],
    fresh_capture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = ("source_id", "title", "url", "vendor", "retrieved_at_upstream")
    if any(not isinstance(record.get(key), str) or not record[key] for key in required):
        raise ValueError(f"invalid source record: {record.get('source_id')}")
    if record.get("first_party") is not True or record.get("requires_fresh_capture") is not True:
        raise ValueError(f"source is not a first-party refetch candidate: {record['source_id']}")
    assert_pinned_vendor_host(record.get("vendor"), record.get("url"))
    source_id = record["source_id"]
    if fresh_capture is None:
        source_url = record["url"]
        source_date = _source_date(record["retrieved_at_upstream"])
        version_material = (source_id, source_url, bundle_sha256)
    else:
        assert_pinned_vendor_host(record.get("vendor"), fresh_capture.get("final_url"))
        source_url = fresh_capture["final_url"]
        source_date = _source_date(fresh_capture["captured_at"])
        version_material = (source_id, source_url, fresh_capture["raw_sha256"])
    return {
        "source_id": source_id,
        "source_version_id": stable_id("source-version", *version_material, length=28),
        "title": record["title"],
        "source_location": source_url,
        "source_date": source_date,
        "classification": "public",
        "audiences": audiences,
        "source_posture": "external_first_party",
        "authority_rank": 8,
        "vendor": record["vendor"],
        "url": source_url,
        "first_party": True,
    }


def _source_fact_candidate(
    fact: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    source: dict[str, Any],
    *,
    fresh_evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fact_id = fact.get("fact_id")
    text = fact.get("embedding_text")
    evidence_ids = fact.get("evidence_ids_json")
    if (
        not isinstance(fact_id, str)
        or not isinstance(text, str)
        or not isinstance(evidence_ids, list)
        or not evidence_ids
        or fact.get("auto_promote") is not False
        or fact.get("approved_fact_retrieval_enabled") is not False
    ):
        raise ValueError(f"invalid fact candidate: {fact_id}")
    if fact.get("source_ids_json") != [source["source_id"]]:
        raise ValueError(f"fact source lineage is not singular and source-bound: {fact_id}")
    primary_evidence = evidence_by_id.get(evidence_ids[0])
    if primary_evidence is None:
        raise ValueError(f"fact refers to missing evidence: {fact_id}")
    candidate_id = stable_id(
        "candidate",
        source["source_version_id"] if fresh_evidence_by_id is not None else source["source_id"],
        fact_id,
        text,
        length=26,
    )
    subject = fact.get("subject")
    if not isinstance(subject, str) or not subject:
        raise ValueError(f"fact has no atomic subject: {fact_id}")
    if fresh_evidence_by_id is None:
        promotion_status = "candidate_evidence_bound_refetch_required"
        review_question = (
            f"Does a fresh first-party capture of {source['vendor']} source {source['source_id']} "
            f"directly entail {fact_id}, with exact locator, hashes, temporal scope, conflict, "
            "classification, and organizational-attestation checks recorded? Keep it unapproved until then."
        )
    else:
        outcomes = [fresh_evidence_by_id[item_id]["preview_match"] for item_id in evidence_ids]
        if all(outcome == "MATCH" for outcome in outcomes):
            promotion_status = "candidate_evidence_bound_hitl_required"
            review_question = (
                f"The exact recorded excerpt for {fact_id} matched the fresh {source['vendor']} capture. "
                "Does that quoted source text directly entail this claim, with correct temporal scope, "
                "classification, conflict check, and organizational attestation? Approve, hold, or reject; "
                "it remains unapproved until this individual HITL decision."
            )
        elif "MISSING" in outcomes:
            promotion_status = "candidate_evidence_preview_mismatch_requires_hitl"
            review_question = (
                f"The recorded excerpt for {fact_id} no longer matches the fresh {source['vendor']} page. "
                "Select a new exact current span and reassess entailment, or hold/reject this candidate. "
                "Do not treat the historic excerpt as current confirmation."
            )
        else:
            promotion_status = "candidate_evidence_locator_pending"
            review_question = (
                f"The fresh {source['vendor']} page was captured, but {fact_id} has only a heading locator. "
                "Select an exact current span, then assess direct entailment, temporal scope, classification, "
                "and conflicts; otherwise hold or reject."
            )
    return {
        "candidate_id": candidate_id,
        "source_item_id": fact_id,
        "candidate_type": "assertion",
        "assertion_type": "candidate_assertion",
        "normalized_text": text,
        "materiality": "high",
        "entity_mentions": [
            _mention(
                candidate_id,
                subject,
                "Atomic source-fact subject; identity remains subject to individual HITL review.",
            )
        ],
        "evidence_ids": evidence_ids,
        "source_locator": _locator(primary_evidence, source["url"]),
        "classification": "public",
        "audiences": source["audiences"],
        "extraction_confidence": 0.7,
        "retrieval_kind": "assertion",
        "answer_use": "candidate_hypothesis",
        "review_question": review_question,
        "promotion_status": promotion_status,
    }


def _evidence_candidate(
    evidence: dict[str, Any],
    source: dict[str, Any],
    *,
    fresh_capture_evidence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    text = evidence.get("excerpt_preview")
    if evidence.get("has_exact_excerpt_preview") is not True:
        return None
    if fresh_capture_evidence is not None and fresh_capture_evidence.get("preview_match") != "MATCH":
        return None
    if not isinstance(text, str) or not text:
        raise ValueError(f"text-bearing evidence has no excerpt: {evidence.get('evidence_id')}")
    evidence_id = evidence["evidence_id"]
    candidate_id = stable_id(
        "candidate-evidence",
        source["source_version_id"] if fresh_capture_evidence is not None else source["source_id"],
        evidence_id,
        text,
        length=26,
    )
    return {
        "candidate_id": candidate_id,
        "source_item_id": evidence_id,
        "candidate_type": "source_chunk",
        "assertion_type": "candidate_assertion",
        "normalized_text": text,
        "materiality": "medium",
        "entity_mentions": [
            _mention(
                candidate_id,
                source["title"],
                "First-party source attached to a citation-only evidence excerpt.",
                stable_identifier=source["source_id"],
            )
        ],
        "evidence_ids": [evidence_id],
        "source_locator": _locator(evidence, source["url"]),
        "classification": "public",
        "audiences": source["audiences"],
        "extraction_confidence": 0.6,
        "retrieval_kind": "evidence_span",
        "answer_use": "citation_only",
        "review_question": (
            f"Does fresh capture of {source['source_id']} reproduce evidence excerpt {evidence_id} "
            "at the recorded locator and hash? Keep this retrieval item citation-only."
        ),
        "promotion_status": "citation_only",
    }


def _derived_source(
    *,
    source_id: str,
    title: str,
    url: str,
    bundle_sha256: str,
    built_at: str,
    audiences: list[str],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_version_id": stable_id("source-version", source_id, bundle_sha256, length=28),
        "title": title,
        "source_location": url,
        "source_date": _source_date(built_at),
        "classification": "public",
        "audiences": audiences,
        "source_posture": "external_contextual",
        "authority_rank": 4,
        "vendor": "NEE derived synthesis",
        "url": url,
        "first_party": False,
    }


def _derived_evidence(
    *, source: dict[str, Any], item_id: str, text: str, table: str
) -> dict[str, Any]:
    return {
        "evidence_id": stable_id("evidence-derived", source["source_id"], item_id, length=26),
        "source_id": source["source_id"],
        "source_version_id": source["source_version_id"],
        "locator": {"kind": "record", "value": f"{table}:{item_id}"},
        "relationship": "supports",
        "directness": "derivative",
        "authority_rank": source["authority_rank"],
        "content_digest": sha256_bytes(text.encode("utf-8")),
        "verification_state": "derived_packet_content",
    }


def _guidance_candidate(
    item: dict[str, Any], evidence_id: str, source: dict[str, Any]) -> dict[str, Any]:
    item_id = item.get("practice_id")
    text = item.get("embedding_text")
    if not isinstance(item_id, str) or not isinstance(text, str) or item.get("eligible_for_fact_promotion") is not False:
        raise ValueError(f"invalid guidance practice: {item_id}")
    candidate_id = stable_id("candidate-guidance", source["source_id"], item_id, text, length=26)
    return {
        "candidate_id": candidate_id,
        "source_item_id": item_id,
        "candidate_type": "action",
        "assertion_type": "action_item",
        "normalized_text": text,
        "materiality": "medium",
        "entity_mentions": [
            _mention(
                candidate_id,
                item.get("category") or "A2A guidance",
                "Derived recommendation category; not a vendor factual assertion.",
            )
        ],
        "evidence_ids": [evidence_id],
        "source_locator": {"kind": "record", "value": f"guidance_practices:{item_id}"},
        "classification": "public",
        "audiences": source["audiences"],
        "extraction_confidence": 0.8,
        "retrieval_kind": "guidance",
        "answer_use": "guidance",
        "review_question": (
            f"Does derived practice {item_id} remain correctly labeled as guidance, with no attribution "
            "as a vendor fact and no eligibility for factual promotion?"
        ),
        "promotion_status": "recommendation_only",
    }


def _pattern_candidate(
    item: dict[str, Any], evidence_id: str, source: dict[str, Any]) -> dict[str, Any]:
    item_id = item.get("pattern_id")
    text = item.get("embedding_text")
    if not isinstance(item_id, str) or not isinstance(text, str) or item.get("eligible_for_fact_promotion") is not False:
        raise ValueError(f"invalid pattern guidance: {item_id}")
    candidate_id = stable_id("candidate-pattern", source["source_id"], item_id, text, length=26)
    return {
        "candidate_id": candidate_id,
        "source_item_id": item_id,
        "candidate_type": "action",
        "assertion_type": "action_item",
        "normalized_text": text,
        "materiality": "low",
        "entity_mentions": [
            _mention(
                candidate_id,
                item.get("pattern") or "A2A routing pattern",
                "Derived routing guidance; not a vendor factual assertion.",
            )
        ],
        "evidence_ids": [evidence_id],
        "source_locator": {"kind": "record", "value": f"pattern_guidance:{item_id}"},
        "classification": "public",
        "audiences": source["audiences"],
        "extraction_confidence": 0.8,
        "retrieval_kind": "guidance",
        "answer_use": "guidance",
        "review_question": (
            f"Does routing pattern {item_id} remain non-factual guidance, excluded from the fact-promotion path?"
        ),
        "promotion_status": "recommendation_only",
    }


def prepare_packages(
    bundle_path: Path,
    output_dir: Path,
    *,
    audiences: list[str],
    fresh_capture_manifest: Path | None = None,
) -> dict[str, Any]:
    """Verify a v3 ZIP and write immutable source-bound package artifacts."""

    verified = read_and_verify_bundle(bundle_path)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError("--output-dir must not already exist")
    normalized_audiences = list(dict.fromkeys(value.strip() for value in audiences))
    if not normalized_audiences or any(not value for value in normalized_audiences):
        raise ValueError("--audience values must not be blank")
    data = verified["data"]
    source_rows = data["sources"]
    source_records = {row.get("source_id"): row for row in source_rows}
    if len(source_records) != len(source_rows) or None in source_records:
        raise ValueError("sources JSONL has duplicate or missing source IDs")
    evidence_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for row in data["evidence_records"]:
        evidence_id = row.get("evidence_id")
        source_id = row.get("source_id")
        if not isinstance(evidence_id, str) or evidence_id in evidence_by_id or source_id not in source_records:
            raise ValueError("evidence records have invalid source lineage")
        evidence_by_id[evidence_id] = row
        evidence_by_source[source_id].append(row)
    facts_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in data["fact_candidates"]:
        source_ids = fact.get("source_ids_json")
        evidence_ids = fact.get("evidence_ids_json")
        if not isinstance(source_ids, list) or len(source_ids) != 1 or source_ids[0] not in source_records:
            raise ValueError(f"fact has invalid source lineage: {fact.get('fact_id')}")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(evidence_id not in evidence_by_id for evidence_id in evidence_ids):
            raise ValueError(f"fact has invalid evidence lineage: {fact.get('fact_id')}")
        if any(evidence_by_id[evidence_id]["source_id"] != source_ids[0] for evidence_id in evidence_ids):
            raise ValueError(f"fact evidence crosses source boundaries: {fact.get('fact_id')}")
        facts_by_source[source_ids[0]].append(fact)
    fresh_capture = (
        _load_fresh_capture_manifest(
            fresh_capture_manifest,
            verified,
            set(source_records),
            set(evidence_by_id),
        )
        if fresh_capture_manifest is not None
        else None
    )
    packages: list[tuple[str, dict[str, Any]]] = []
    for source_id in sorted(source_records):
        capture = fresh_capture["captures"][source_id] if fresh_capture is not None else None
        source = _authoritative_source(
            source_records[source_id],
            verified["bundle_sha256"],
            normalized_audiences,
            fresh_capture=capture,
        )
        source_evidence = [
            _evidence_reference(
                row,
                source_id=source["source_id"],
                source_version_id=source["source_version_id"],
                url=source["url"],
                authority_rank=source["authority_rank"],
                fresh_capture_evidence=(
                    fresh_capture["evidence"][row["evidence_id"]]
                    if fresh_capture is not None
                    else None
                ),
            )
            for row in evidence_by_source[source_id]
        ]
        candidates = [
            _source_fact_candidate(
                fact,
                evidence_by_id,
                source,
                fresh_evidence_by_id=fresh_capture["evidence"] if fresh_capture is not None else None,
            )
            for fact in facts_by_source[source_id]
        ]
        candidates.extend(
            candidate
            for candidate in (
                _evidence_candidate(
                    row,
                    source,
                    fresh_capture_evidence=(
                        fresh_capture["evidence"][row["evidence_id"]]
                        if fresh_capture is not None
                        else None
                    ),
                )
                for row in evidence_by_source[source_id]
            )
            if candidate is not None
        )
        if not candidates:
            raise ValueError(f"source produces no text-bearing retrieval candidates: {source_id}")
        packages.append(
            (
                f"{slug(source_id)}.captured.intake.json" if fresh_capture is not None else f"{slug(source_id)}.intake.json",
                _package(
                    verified=verified,
                    source=source,
                    evidence=source_evidence,
                    candidates=candidates,
                    lineage_kind=(
                        "first_party_fresh_capture_candidate"
                        if fresh_capture is not None
                        else "first_party_candidate"
                    ),
                    fresh_capture=(
                        {
                            "capture_id": fresh_capture["capture_id"],
                            "capture_manifest_sha256": fresh_capture["manifest_sha256"],
                            "captured_at": capture["captured_at"],
                            "requested_url": capture["requested_url"],
                            "final_url": capture["final_url"],
                            "raw_response_sha256": capture["raw_sha256"],
                            "visible_text_sha256": capture["visible_text_sha256"],
                            "evidence_outcomes": {
                                row["evidence_id"]: fresh_capture["evidence"][row["evidence_id"]]["preview_match"]
                                for row in evidence_by_source[source_id]
                            },
                        }
                        if fresh_capture is not None
                        else None
                    ),
                ),
            )
        )
    if fresh_capture is None:
        built_at = verified["manifest"].get("built_at")
        if not isinstance(built_at, str):
            raise ValueError("BUNDLE_MANIFEST has no built_at timestamp")
        guidance_source = _derived_source(
            source_id="SRC-NEE-A2A-GUIDANCE",
            title="Derived OpenAI and Anthropic A2A guidance from governed v3 packet",
            url="urn:nee:a2a-governed-ingestion-v3:guidance",
            bundle_sha256=verified["bundle_sha256"],
            built_at=built_at,
            audiences=normalized_audiences,
        )
        guidance_evidence = [
            _derived_evidence(
                source=guidance_source,
                item_id=row["practice_id"],
                text=row["embedding_text"],
                table="guidance_practices",
            )
            for row in data["guidance_practices"]
        ]
        guidance_candidates = [
            _guidance_candidate(row, evidence["evidence_id"], guidance_source)
            for row, evidence in zip(data["guidance_practices"], guidance_evidence, strict=True)
        ]
        packages.append(
            (
                "derived-guidance.intake.json",
                _package(
                    verified=verified,
                    source=guidance_source,
                    evidence=guidance_evidence,
                    candidates=guidance_candidates,
                    lineage_kind="derived_guidance",
                ),
            )
        )
        pattern_source = _derived_source(
            source_id="SRC-NEE-A2A-PATTERN-GUIDANCE",
            title="Derived OpenAI and Anthropic A2A routing guidance from governed v3 packet",
            url="urn:nee:a2a-governed-ingestion-v3:pattern-guidance",
            bundle_sha256=verified["bundle_sha256"],
            built_at=built_at,
            audiences=normalized_audiences,
        )
        pattern_evidence = [
            _derived_evidence(
                source=pattern_source,
                item_id=row["pattern_id"],
                text=row["embedding_text"],
                table="pattern_guidance",
            )
            for row in data["pattern_guidance"]
        ]
        pattern_candidates = [
            _pattern_candidate(row, evidence["evidence_id"], pattern_source)
            for row, evidence in zip(data["pattern_guidance"], pattern_evidence, strict=True)
        ]
        packages.append(
            (
                "pattern-guidance.intake.json",
                _package(
                    verified=verified,
                    source=pattern_source,
                    evidence=pattern_evidence,
                    candidates=pattern_candidates,
                    lineage_kind="derived_pattern_guidance",
                ),
            )
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    for filename, package in packages:
        write_json_exclusive(output_dir / filename, package)
    category_counts: dict[str, int] = defaultdict(int)
    for _, package in packages:
        for candidate in package["candidates"]:
            category_counts[candidate["retrieval_kind"]] += 1
    prepared = {
        "status": "PASS",
        "source_bundle": {
            "path": str(verified["bundle_path"]),
            "sha256": verified["bundle_sha256"],
            "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            "upstream_packet_id": verified["upstream"]["packet_id"],
            "upstream_packet_manifest_sha256": verified["upstream"]["packet_manifest_sha256"],
            "upstream_signature_valid": True,
            "upstream_manifest_hashes_valid": True,
            "trust_scope": PACKET_TRUST_SCOPE,
        },
        "fresh_capture": (
            {
                "capture_id": fresh_capture["capture_id"],
                "capture_manifest_sha256": fresh_capture["manifest_sha256"],
                "captured_source_count": len(fresh_capture["captures"]),
                "preview_match_count": sum(
                    1
                    for item in fresh_capture["evidence"].values()
                    if item["preview_match"] == "MATCH"
                ),
                "preview_missing_count": sum(
                    1
                    for item in fresh_capture["evidence"].values()
                    if item["preview_match"] == "MISSING"
                ),
                "locator_pending_count": sum(
                    1
                    for item in fresh_capture["evidence"].values()
                    if item["preview_match"]
                    == "NOT_APPLICABLE_LOCATOR_REQUIRES_HITL_SPAN_SELECTION"
                ),
            }
            if fresh_capture is not None
            else None
        ),
        "packages": [
            {
                "path": filename,
                "artifact_id": package["artifact_id"],
                "source_id": package["source"]["source_id"],
                "source_posture": package["source"]["source_posture"],
                "candidate_count": len(package["candidates"]),
                "canonical_digest": package["canonical_digest"]["value"],
            }
            for filename, package in packages
        ],
        "retrieval_categories": dict(sorted(category_counts.items())),
        "not_embedded": {
            "evidence_locators_missing_text": sum(
                1
                for row in data["evidence_records"]
                if row.get("has_exact_excerpt_preview") is not True
            ),
            "citation_only_examples": 1,
            "reason": "No embedding text or explicitly excluded by source packet.",
        },
        "promotion": {
            "approved_fact_candidates": 0,
            "automatic_fact_promotion": False,
            "individual_hitl_required": True,
            "fresh_capture_required_for_facts": True,
        },
    }
    write_json_exclusive(output_dir / "prepared_manifest.json", prepared)
    return prepared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fresh-capture-manifest", type=Path)
    parser.add_argument(
        "--audience",
        action="append",
        dest="audiences",
        default=["internal_consulting", "nee-engineering"],
    )
    args = parser.parse_args()
    try:
        prepared = prepare_packages(
            args.bundle,
            args.output_dir,
            audiences=args.audiences,
            fresh_capture_manifest=args.fresh_capture_manifest,
        )
    except (OSError, ValueError, ContractError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(json.dumps(prepared, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
