"""
dual_pipeline/url_ingress.py

Stage U0 Hyperlink Ingress and Corporate SSO Authentication Guard.
Enforces Master Invariant (Anti-Silent-Fallback & Hyperlink Ingestion Standard):
Intercepts HTTP 302 redirects, Microsoft Entra, Amazon Cognito, Okta, Ping, and Auth0
authentication challenges. Attempts authenticated OS-level browser extraction (Edge/Chrome/Safari)
and fails closed with SourceAuthRequiredError ("SOURCE_AUTH_REQUIRED") to prevent silent local fallback.
"""
from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("dual_pipeline.url_ingress")

SSO_CHALLENGE_PATTERNS = [
    r"Sign in to your account",
    r"login\.microsoftonline\.com",
    r"aadcdn\.msftauth\.net",
    r"amazoncognito\.com",
    r"ConvergedLogin",
    r"login\.okta\.com",
    r"pingidentity\.com",
    r"auth0\.com",
    r"oauth2/authorize",
]

COMPILED_SSO_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SSO_CHALLENGE_PATTERNS]


class SourceAuthRequiredError(RuntimeError):
    """Raised when an ingestion source requires corporate SSO authentication."""

    def __init__(self, message: str, url: str = "", markers: Optional[List[str]] = None):
        super().__init__(message)
        self.reason_code = "SOURCE_AUTH_REQUIRED"
        self.url = url
        self.markers = markers or []


def detect_sso_challenge(text: str) -> List[str]:
    """Scan text or HTML payload for known corporate SSO challenge signatures."""
    if not text:
        return []
    matches: List[str] = []
    for pattern in COMPILED_SSO_PATTERNS:
        m = pattern.search(text)
        if m:
            matches.append(m.group(0))
    return matches


def build_capture_receipt(url: str, method: str, content: str) -> Dict[str, Any]:
    """Builds a cryptographic capture receipt for source attestation."""
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "$schema": "urn:nee:hyperlink-capture-receipt:1.0.0",
        "method": method,
        "source_url": url,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": content_sha,
        "content_length": len(content),
    }


def _run_browser_applescript(browser: str, script: str) -> str:
    """Executes AppleScript snippet targeting a specified macOS browser."""
    cmd = ["osascript", "-e", f'tell application "{browser}" to {script}']
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        return res.stdout.strip() if res.returncode == 0 else ""
    except Exception:
        return ""


def extract_browser_dom_single(browser: str) -> Optional[str]:
    """Extracts document.body.innerText from the frontmost tab of a specific browser."""
    if browser in ("Google Chrome", "Microsoft Edge", "Brave Browser"):
        script = 'execute front window\'s active tab javascript "document.body.innerText"'
    elif browser == "Safari":
        script = 'do JavaScript "document.body.innerText" in front document'
    else:
        return None
    out = _run_browser_applescript(browser, script)
    return out if len(out) >= 50 else None


def extract_authenticated_browser_dom(
    url: str,
    browsers: Sequence[str] = ("Microsoft Edge", "Google Chrome", "Safari"),
) -> Optional[Tuple[str, str]]:
    """Iterates through installed macOS browsers to extract authenticated live DOM text."""
    if sys.platform != "darwin":
        return None

    for b in browsers:
        try:
            content = extract_browser_dom_single(b)
            if content and not detect_sso_challenge(content):
                logger.info(f"Successfully extracted authenticated DOM from {b} for {url}")
                return content, b
        except Exception as e:
            logger.debug(f"Failed extraction from {b}: {e}")
    return None


def fetch_http_payload(url: str, timeout: int = 10) -> Tuple[int, str, List[str]]:
    """Fetches raw HTTP content and returns (status_code, body, sso_markers)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            final_url = resp.geturl()
            body = resp.read().decode("utf-8", errors="replace")
            markers = detect_sso_challenge(body) + detect_sso_challenge(final_url)
            return status, body, list(dict.fromkeys(markers))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        markers = detect_sso_challenge(body) + detect_sso_challenge(str(e.headers))
        return e.code, body, list(dict.fromkeys(markers))
    except Exception as e:
        logger.warning(f"HTTP fetch failed for {url}: {e}")
        return 0, "", [f"FETCH_ERROR: {e}"]


def _evaluate_browser_fallback(
    url: str,
    allow_browser_escalation: bool,
    markers: List[str],
) -> Tuple[str, Dict[str, Any]]:
    """Attempts browser escalation if allowed; otherwise raises SourceAuthRequiredError."""
    if allow_browser_escalation:
        browser_res = extract_authenticated_browser_dom(url)
        if browser_res:
            content, browser_name = browser_res
            receipt = build_capture_receipt(url, f"AUTHENTICATED_BROWSER_DOM ({browser_name})", content)
            return content, receipt

    err_msg = (
        f"SOURCE_AUTH_REQUIRED: Remote URL '{url}' requires corporate SSO authentication. "
        f"Detected challenge markers: {markers}. Anti-Silent-Fallback policy prohibits "
        "substituting local repository files."
    )
    raise SourceAuthRequiredError(err_msg, url=url, markers=markers)


def fetch_url_with_sso_guard(
    url: str,
    allow_browser_escalation: bool = True,
    mock_payload: Optional[Tuple[int, str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Fetches remote URL with SSO challenge detection and browser escalation.
    Fails closed with SourceAuthRequiredError on unresolvable SSO barriers.
    """
    if mock_payload is not None:
        status, body = mock_payload
        markers = detect_sso_challenge(body)
    else:
        status, body, markers = fetch_http_payload(url)

    is_sso_status = status in (302, 401, 403)
    if is_sso_status or markers:
        return _evaluate_browser_fallback(url, allow_browser_escalation, markers)

    if status != 200 or len(body.strip()) < 50:
        return _evaluate_browser_fallback(url, allow_browser_escalation, ["INSUFFICIENT_PAYLOAD_OR_NON_200"])

    receipt = build_capture_receipt(url, "HTTP_GET_ANONYMOUS", body)
    return body, receipt
