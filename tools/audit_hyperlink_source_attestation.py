#!/usr/bin/env python3
"""audit_hyperlink_source_attestation.py - Source attestation guard for remote URL ingestion.

Prevents silent fallback to stale local files, detects corporate SSO / login challenges,
and enforces authenticated browser extraction for auth-gated enterprise portals.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

SSO_CHALLENGE_PATTERNS = [
    r"Sign in to your account",
    r"login\.microsoftonline\.com",
    r"aadcdn\.msftauth\.net",
    r"amazoncognito\.com",
    r"ConvergedLogin",
    r"login\.okta\.com",
    r"pingidentity\.com",
    r"auth0\.com",
]

COMPILED_SSO_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SSO_CHALLENGE_PATTERNS]


@dataclass
class AttestationResult:
    is_valid: bool
    status_code: str
    reasons: List[str]
    detected_markers: List[str]


def detect_sso_challenge(text: str) -> List[str]:
    """Scan raw text or HTML payload for known corporate SSO challenge signatures."""
    if not text:
        return []
    matches = []
    for pattern in COMPILED_SSO_PATTERNS:
        found = pattern.findall(text)
        if found:
            matches.append(pattern.pattern)
    return matches


def audit_hyperlink_content(
    content: str,
    source_url: Optional[str] = None,
    min_length: int = 100,
) -> AttestationResult:
    """Verify that extracted hyperlink content is meaningful and free from login redirects."""
    reasons = []
    detected_markers = detect_sso_challenge(content)

    if detected_markers:
        reasons.append(
            f"Corporate SSO or login challenge signatures detected: {detected_markers}"
        )
        return AttestationResult(
            is_valid=False,
            status_code="SOURCE_AUTH_CHALLENGE_DETECTED",
            reasons=reasons,
            detected_markers=detected_markers,
        )

    clean_text = re.sub(r"<[^>]+>", " ", content).strip()
    if len(clean_text) < min_length:
        reasons.append(
            f"Extracted content length ({len(clean_text)}) is below threshold ({min_length})"
        )
        return AttestationResult(
            is_valid=False,
            status_code="INSUFFICIENT_PAYLOAD_LENGTH",
            reasons=reasons,
            detected_markers=[],
        )

    return AttestationResult(
        is_valid=True,
        status_code="PASS",
        reasons=["Payload passed SSO challenge and length verification"],
        detected_markers=[],
    )


def audit_intake_package(
    package_data: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> AttestationResult:
    """Audit an intake package to ensure remote URL sources are attested and not substituted."""
    reasons = []
    detected_markers = []

    metadata = package_data.get("meeting") or package_data.get("metadata") or {}
    source_location = package_data.get("source_location") or metadata.get("source_location") or ""
    platform_ref = metadata.get("platform_reference") or ""
    effective_url = source_location if source_location.startswith("http") else platform_ref

    if not effective_url.startswith("http"):
        return AttestationResult(
            is_valid=True,
            status_code="NON_URL_SOURCE",
            reasons=["Source is not an HTTP/HTTPS remote URL; skipping remote attestation."],
            detected_markers=[],
        )

    # If package claims a remote URL, verify it has an authenticated capture receipt
    capture_receipt = package_data.get("capture_receipt") or metadata.get("capture_receipt")
    if not capture_receipt:
        reasons.append(
            f"Package claims remote URL '{effective_url}' but lacks an authenticated capture receipt. "
            "Ingestion requires verifiable proof of live DOM capture or authenticated HTTP extraction."
        )
        return AttestationResult(
            is_valid=False,
            status_code="MISSING_AUTHENTICATED_CAPTURE_RECEIPT",
            reasons=reasons,
            detected_markers=[],
        )

    # Check evidence statements and content for SSO markers
    evidence_items = package_data.get("evidence", [])
    if isinstance(evidence_items, dict):
        evidence_items = evidence_items.get("items", [])

    all_evidence_text = []
    for item in evidence_items:
        text = item.get("text") or item.get("claim") or item.get("content") or ""
        all_evidence_text.append(text)

    combined_text = "\n".join(all_evidence_text)
    sso_markers = detect_sso_challenge(combined_text)
    if sso_markers:
        return AttestationResult(
            is_valid=False,
            status_code="SOURCE_AUTH_CHALLENGE_IN_PACKAGE",
            reasons=[f"Package evidence contains SSO challenge text: {sso_markers}"],
            detected_markers=sso_markers,
        )

    # Check for unauthorized local substitution
    raw_source = package_data.get("raw_source") or metadata.get("raw_source") or ""
    if raw_source and ("intake/" in raw_source or ".md" in raw_source):
        # A package with a remote URL that declares a local markdown file as raw_source
        # without capture proof is a suspected substitution.
        capture_receipt = package_data.get("capture_receipt") or metadata.get("capture_receipt")
        if not capture_receipt:
            reasons.append(
                f"Package claims remote URL {effective_url} but relies on local raw_source "
                f"'{raw_source}' without an authenticated capture receipt."
            )
            return AttestationResult(
                is_valid=False,
                status_code="UNAUTHORIZED_LOCAL_SOURCE_SUBSTITUTION",
                reasons=reasons,
                detected_markers=[],
            )

    return AttestationResult(
        is_valid=True,
        status_code="PASS",
        reasons=["Intake package source attestation passed"],
        detected_markers=[],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit hyperlink ingestion for SSO auth challenges and local substitutions."
    )
    parser.add_argument("--package", type=Path, help="Path to intake package JSON to audit")
    parser.add_argument("--file", type=Path, help="Path to raw scraped text/HTML file to audit")
    parser.add_argument("--url", type=str, help="Target URL (for logging context)")
    parser.add_argument("--min-length", type=int, default=100, help="Minimum body length")
    args = parser.parse_args()

    if args.package:
        if not args.package.is_file():
            print(f"Error: Package file not found: {args.package}", file=sys.stderr)
            return 1
        with open(args.package, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = audit_intake_package(data)
    elif args.file:
        if not args.file.is_file():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            return 1
        content = args.file.read_text(encoding="utf-8")
        result = audit_hyperlink_content(content, source_url=args.url, min_length=args.min_length)
    else:
        print("Error: Specify either --package or --file to audit.", file=sys.stderr)
        return 1

    print(json.dumps({
        "status_code": result.status_code,
        "is_valid": result.is_valid,
        "reasons": result.reasons,
        "detected_markers": result.detected_markers,
    }, indent=2))

    return 0 if result.is_valid else 2


if __name__ == "__main__":
    sys.exit(main())
