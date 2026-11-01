"""
tests/test_sso_auth_escalation_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 3:
Stage U0 Hyperlink Ingestion & Corporate SSO Auth Guard.

Verifies:
1. Boundary & Malformed Inputs: SSO challenge signatures (Entra, Cognito, Okta, HTTP 302/401/403).
2. Browser Escalation Protocol:
   - Successful browser extraction yields capture_receipt with AUTHENTICATED_BROWSER_DOM.
   - Unauthenticated browser extraction or failure fails closed with SourceAuthRequiredError ("SOURCE_AUTH_REQUIRED").
3. Anti-Silent-Fallback Invariant:
   - Prohibits silent local filesystem substitution when remote URL is specified.
   - Stage U0 ingress refuses to fall back to disk on SSO challenge.
"""
from __future__ import annotations

import unittest
from unittest import mock

from dual_pipeline.transcript_pipeline_core.ingress import process_u0_ingress
from dual_pipeline.url_ingress import (
    SourceAuthRequiredError,
    build_capture_receipt,
    detect_sso_challenge,
    fetch_url_with_sso_guard,
)


class TestSSOAuthEscalationAdversarial(unittest.TestCase):
    """Adversarial test cases covering SSO detection, browser escalation, and anti-fallback guard."""

    def test_entra_sso_challenge_detection_boundary_payloads(self):
        """Verify Microsoft Entra login challenge detection across various HTML structures."""
        entra_html_1 = """
        <!DOCTYPE html><html><head>
        <title>Sign in to your account</title>
        <link rel="shortcut icon" href="https://aadcdn.msftauth.net/shared/1.0/content/images/favicon.ico" />
        </head><body>Redirecting to login.microsoftonline.com...</body></html>
        """
        markers = detect_sso_challenge(entra_html_1)
        self.assertGreaterEqual(len(markers), 2)
        self.assertTrue(any("Sign in to your account" in m for m in markers))

        entra_html_2 = '<script src="https://aadcdn.msftauth.net/ConvergedLogin.js"></script>'
        markers2 = detect_sso_challenge(entra_html_2)
        self.assertTrue(any("ConvergedLogin" in m for m in markers2))

    def test_cognito_and_okta_sso_challenge_detection(self):
        """Verify Amazon Cognito and Okta authentication markers are caught."""
        cognito_payload = "https://auth.enterprise.amazoncognito.com/oauth2/authorize?client_id=123"
        markers = detect_sso_challenge(cognito_payload)
        self.assertTrue(any("amazoncognito" in m for m in markers))
        self.assertTrue(any("oauth2/authorize" in m for m in markers))

        okta_payload = '<html>Redirecting to <a href="https://login.okta.com/login">Okta Verify</a></html>'
        markers_okta = detect_sso_challenge(okta_payload)
        self.assertTrue(any("login.okta.com" in m for m in markers_okta))

    def test_fetch_url_fails_closed_on_unresolvable_sso_challenge(self):
        """Verify SourceAuthRequiredError is raised when SSO is present and browser escalation fails."""
        entra_body = "<html><title>Sign in to your account</title><body>SSO Challenge</body></html>"
        url = "https://nea-onboarding.static.consulting.example.com/charters"

        # Mock browser extraction returning None (browser not authenticated or not open)
        with mock.patch("dual_pipeline.url_ingress.extract_authenticated_browser_dom", return_value=None):
            with self.assertRaises(SourceAuthRequiredError) as ctx:
                fetch_url_with_sso_guard(
                    url,
                    allow_browser_escalation=True,
                    mock_payload=(302, entra_body),
                )
            err = ctx.exception
            self.assertEqual(err.reason_code, "SOURCE_AUTH_REQUIRED")
            self.assertIn("SOURCE_AUTH_REQUIRED", str(err))
            self.assertIn("Anti-Silent-Fallback policy prohibits", str(err))

    def test_fetch_url_succeeds_via_browser_escalation(self):
        """Verify browser escalation retrieves live DOM text and issues an authenticated capture receipt."""
        entra_body = "<html><title>Sign in to your account</title><body>Login prompt</body></html>"
        url = "https://nea-onboarding.static.consulting.example.com/charters"

        valid_live_dom = (
            "Apex Global Enterprises Analytics (NEA) Onboarding Portal Charters.\n"
            "Charter 1: Symphony Outage Journey - Tech Lead: Reid S, Delivery Lead: Justin M.\n"
            "Charter 2: NEE Enterprise AI Conduit - Enterprise Core AI Models."
        )

        with mock.patch(
            "dual_pipeline.url_ingress.extract_authenticated_browser_dom",
            return_value=(valid_live_dom, "Microsoft Edge"),
        ):
            content, receipt = fetch_url_with_sso_guard(
                url,
                allow_browser_escalation=True,
                mock_payload=(302, entra_body),
            )
            self.assertEqual(content, valid_live_dom)
            self.assertIn("AUTHENTICATED_BROWSER_DOM (Microsoft Edge)", receipt["method"])
            self.assertEqual(receipt["source_url"], url)
            self.assertGreater(len(receipt["sha256"]), 0)

    def test_stage_u0_ingress_blocks_local_fallback_on_sso(self):
        """Verify process_u0_ingress refuses to fallback to local files when URL encounters SSO."""
        url = "https://nea-onboarding.static.consulting.example.com/"
        meta = {
            "project": "project_nexus",
            "meeting_name": "Onboarding Charters",
            "date": "09-26-2026",
        }

        # Mock fetch_url_with_sso_guard to raise SourceAuthRequiredError
        with mock.patch(
            "dual_pipeline.url_ingress.fetch_url_with_sso_guard",
            side_effect=SourceAuthRequiredError("SOURCE_AUTH_REQUIRED: Corporate SSO detected", url=url),
        ):
            with self.assertRaises(SourceAuthRequiredError) as ctx:
                process_u0_ingress(
                    input_path=url,
                    metadata=meta,
                )
            self.assertEqual(ctx.exception.reason_code, "SOURCE_AUTH_REQUIRED")

    def test_stage_u0_ingress_attaches_capture_receipt_on_browser_success(self):
        """Verify process_u0_ingress populates metadata with capture receipt on successful extraction."""
        url = "https://nea-onboarding.static.consulting.example.com/live"
        live_content = (
            "Forensic capture for live portal charters.\n"
            "Amit Ayer: Reviewing the delivery charters for SOW 1 and SOW 2.\n"
            "Justin M: Confirmation of architecture boundaries."
        )
        fake_receipt = build_capture_receipt(url, "AUTHENTICATED_BROWSER_DOM (Google Chrome)", live_content)

        meta = {
            "project": "project_nexus",
            "meeting_name": "Portal Charters Live",
            "date": "09-26-2026",
        }

        with mock.patch(
            "dual_pipeline.url_ingress.fetch_url_with_sso_guard",
            return_value=(live_content, fake_receipt),
        ):
            res = process_u0_ingress(
                input_path=url,
                metadata=meta,
            )
            self.assertIn("capture_receipt", res.metadata)
            self.assertEqual(res.metadata["capture_receipt"]["method"], "AUTHENTICATED_BROWSER_DOM (Google Chrome)")
            self.assertEqual(res.metadata["trust_tier"], "TIER_1_AUTHENTICATED_DOM")
            self.assertTrue(res.metadata["source_file_hash"].startswith("urn:nee:hyperlink-capture:sha256:"))


if __name__ == "__main__":
    unittest.main()
