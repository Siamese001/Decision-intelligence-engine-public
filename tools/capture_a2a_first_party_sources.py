#!/usr/bin/env python3
"""Fetch immutable first-party capture evidence for a governed A2A v3 bundle.

The script treats the supplied v3 ZIP as the source inventory, verifies it
before use, then records raw response bytes, response metadata, normalized
visible text hashes, and evidence-preview match results.  It does not promote,
change a graph, or make a HITL decision.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (  # noqa: E402
    normalize_space,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_exclusive,
)
from dual_pipeline.source_trust import assert_pinned_vendor_host  # noqa: E402
from tools.prepare_a2a_fact_vector_packet_intake import (  # noqa: E402
    read_and_verify_bundle,
)

USER_AGENT = "NEE-governed-source-verifier/1.0"
MANIFEST_VERSION = "1.0.0"


class PinnedVendorRedirectHandler(HTTPRedirectHandler):
    """Reject an off-domain redirect before urllib follows or reads it."""

    def __init__(self, vendor: str) -> None:
        super().__init__()
        self.vendor = vendor

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        assert_pinned_vendor_host(self.vendor, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class VisibleTextExtractor(HTMLParser):
    """Extract only visible textual nodes from an HTML response."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript", "template", "svg"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "template", "svg"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0 and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return normalize_space(" ".join(self._parts))


def _decode(raw: bytes, content_type: str | None) -> str:
    charset = None
    if content_type and "charset=" in content_type.casefold():
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    for encoding in (charset, "utf-8", "utf-8-sig"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _visible_text(raw: bytes, content_type: str | None) -> str:
    decoded = _decode(raw, content_type)
    if content_type and "html" in content_type.casefold():
        parser = VisibleTextExtractor()
        parser.feed(decoded)
        parser.close()
        return parser.text()
    return normalize_space(html.unescape(decoded))


def _evidence_result(evidence: dict[str, Any], visible_text: str) -> dict[str, Any]:
    preview = evidence.get("excerpt_preview")
    if preview is None:
        return {
            "evidence_id": evidence["evidence_id"],
            "preview_available": False,
            "preview_match": "NOT_APPLICABLE_LOCATOR_REQUIRES_HITL_SPAN_SELECTION",
            "heading_present": normalize_space(str(evidence.get("heading", "")))
            in visible_text,
            "recorded_evidence_span_sha256": evidence["evidence_span_sha256"],
        }
    normalized_preview = normalize_space(str(preview))
    normalized_preview_sha256 = sha256_bytes(normalized_preview.encode("utf-8"))
    expected_sha256 = evidence.get("exact_excerpt_sha256")
    preview_offset = visible_text.find(normalized_preview)
    preview_hash_matches_packet = normalized_preview_sha256 == expected_sha256
    preview_verified = preview_offset >= 0 and preview_hash_matches_packet
    return {
        "evidence_id": evidence["evidence_id"],
        "preview_available": True,
        # A textual hit is not a verified reproduction if the packet's
        # recorded excerpt digest disagrees.  Treat either failure as a
        # mismatch so downstream code cannot accidentally promote it.
        "preview_match": "MATCH" if preview_verified else "MISSING",
        "preview_offset": preview_offset if preview_verified else None,
        "normalized_preview_sha256": normalized_preview_sha256,
        "recorded_exact_excerpt_sha256": expected_sha256,
        "preview_hash_matches_packet": preview_hash_matches_packet,
        "heading_present": normalize_space(str(evidence.get("heading", "")))
        in visible_text,
        "recorded_evidence_span_sha256": evidence["evidence_span_sha256"],
    }


def _raw_capture_filename(source_id: str) -> str:
    """Return a deterministic normal filename, never a source ID as a path."""

    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source ID must be a nonempty string")
    # Source identifiers are contract identifiers and may contain ':'; on
    # Windows that could address an NTFS alternate data stream.  The manifest
    # preserves the source ID, while the archive filename is content-neutral.
    return f"{sha256_bytes(source_id.encode('utf-8'))}.response"


def capture_bundle(bundle_path: Path, output_dir: Path, *, timeout_seconds: int) -> dict[str, Any]:
    verified = read_and_verify_bundle(bundle_path)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError("--output-dir must not already exist")
    if timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")
    source_rows = verified["data"]["sources"]
    # Validate every declared request target before creating capture output or
    # opening the first network connection.  The redirect handler below then
    # enforces the same vendor host policy for every redirect hop.
    for source in source_rows:
        assert_pinned_vendor_host(source.get("vendor"), source.get("url"))
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir()
    evidence_by_source: dict[str, list[dict[str, Any]]] = {}
    for evidence in verified["data"]["evidence_records"]:
        evidence_by_source.setdefault(evidence["source_id"], []).append(evidence)
    captures: list[dict[str, Any]] = []
    for source in sorted(source_rows, key=lambda item: item["source_id"]):
        source_id = source["source_id"]
        requested_url = source["url"]
        captured_at = utc_now()
        base: dict[str, Any] = {
            "source_id": source_id,
            "vendor": source["vendor"],
            "requested_url": requested_url,
            "captured_at": captured_at,
        }
        try:
            request = Request(requested_url, headers={"User-Agent": USER_AGENT})
            opener = build_opener(PinnedVendorRedirectHandler(source["vendor"]))
            with opener.open(request, timeout=timeout_seconds) as response:
                content_type = response.headers.get("Content-Type")
                final_url = response.url
                # Validate the terminal URL before any response body bytes are
                # consumed.  This also covers an opener implementation that
                # did not invoke a redirect hook for a terminal response.
                assert_pinned_vendor_host(source["vendor"], final_url)
                raw = response.read()
                status = getattr(response, "status", response.getcode())
                response_headers = {
                    key: response.headers.get(key)
                    for key in ("ETag", "Last-Modified", "Content-Type", "Date")
                    if response.headers.get(key) is not None
                }
        except HTTPError as exc:
            captures.append(
                base
                | {
                    "status": "HTTP_ERROR",
                    "http_status": exc.code,
                    "error": str(exc),
                }
            )
            continue
        except (URLError, TimeoutError, OSError) as exc:
            captures.append(base | {"status": "FETCH_ERROR", "error": str(exc)})
            continue
        raw_path = raw_dir / _raw_capture_filename(source_id)
        raw_path.write_bytes(raw)
        visible_text = _visible_text(raw, content_type)
        evidence = [
            _evidence_result(item, visible_text)
            for item in evidence_by_source.get(source_id, [])
        ]
        captures.append(
            base
            | {
                "status": "CAPTURED",
                "http_status": int(status),
                "final_url": final_url,
                "response_headers": response_headers,
                "raw_path": raw_path.relative_to(output_dir).as_posix(),
                "raw_sha256": sha256_bytes(raw),
                "raw_bytes": len(raw),
                "visible_text_sha256": sha256_bytes(visible_text.encode("utf-8")),
                "visible_text_characters": len(visible_text),
                "evidence": evidence,
            }
        )
    complete = all(item["status"] == "CAPTURED" for item in captures)
    preview_matches = [
        item
        for capture in captures
        if capture["status"] == "CAPTURED"
        for item in capture["evidence"]
        if item["preview_available"]
    ]
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "status": "PASS" if complete else "BLOCKED",
        "capture_id": f"a2a-v3-first-party-capture-{sha256_file(bundle_path)[:16]}",
        "created_at": utc_now(),
        "bundle": {
            "path": str(verified["bundle_path"]),
            "sha256": verified["bundle_sha256"],
            "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            "upstream_packet_id": verified["upstream"]["packet_id"],
            "upstream_packet_manifest_sha256": verified["upstream"][
                "packet_manifest_sha256"
            ],
        },
        "capture_policy": {
            "user_agent": USER_AGENT,
            "timeout_seconds": timeout_seconds,
            "promotion_effect": "none; individual HITL and UWG approval remain required",
        },
        "captures": captures,
        "summary": {
            "source_count": len(captures),
            "captured_source_count": sum(
                item["status"] == "CAPTURED" for item in captures
            ),
            "preview_match_count": sum(
                item["preview_match"] == "MATCH" for item in preview_matches
            ),
            "preview_missing_count": sum(
                item["preview_match"] != "MATCH" for item in preview_matches
            ),
            "locator_only_evidence_count": sum(
                1
                for capture in captures
                if capture["status"] == "CAPTURED"
                for item in capture["evidence"]
                if not item["preview_available"]
            ),
        },
    }
    write_json_exclusive(output_dir / "capture_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()
    try:
        manifest = capture_bundle(
            args.bundle, args.output_dir, timeout_seconds=args.timeout_seconds
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest["summary"] | {"status": manifest["status"]}, indent=2))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
