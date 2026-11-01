"""Tests for audit_hyperlink_source_attestation.py.

Verifies:
1. Detection of Microsoft Entra ID / corporate SSO challenges in raw text/HTML.
2. Rejection of intake packages claiming remote URLs without capture receipts.
3. Rejection of packages with SSO challenge text in evidence statements.
4. Acceptance of non-URL intake packages and authenticated live captures.
"""

import unittest

from tools.audit_hyperlink_source_attestation import (
    audit_hyperlink_content,
    audit_intake_package,
    detect_sso_challenge,
)


class TestAuditHyperlinkSourceAttestation(unittest.TestCase):
    def test_detect_sso_challenge_microsoft_entra(self):
        html = """
        <html>
          <head>
            <title>Sign in to your account</title>
            <script src="https://aadcdn.msftauth.net/shared/1.0/content/js/ConvergedLogin.js"></script>
          </head>
          <body>Please sign in</body>
        </html>
        """
        markers = detect_sso_challenge(html)
        self.assertGreaterEqual(len(markers), 3)
        self.assertTrue(any("Sign in to your account" in m for m in markers))
        self.assertTrue(any("aadcdn" in m for m in markers))

    def test_detect_sso_challenge_cognito(self):
        text = "Redirecting to https://myorg.auth.us-east-1.amazoncognito.com/authorize?response_type=code"
        markers = detect_sso_challenge(text)
        self.assertTrue(any("amazoncognito" in m for m in markers))

    def test_audit_hyperlink_content_blocks_sso(self):
        sso_html = "<html><title>Sign in to your account</title><body>Login prompt</body></html>"
        result = audit_hyperlink_content(sso_html, source_url="https://example.com")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status_code, "SOURCE_AUTH_CHALLENGE_DETECTED")
        self.assertGreater(len(result.detected_markers), 0)

    def test_audit_hyperlink_content_blocks_short_payload(self):
        short_text = "<html><body>Too short</body></html>"
        result = audit_hyperlink_content(short_text, source_url="https://example.com", min_length=100)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status_code, "INSUFFICIENT_PAYLOAD_LENGTH")

    def test_audit_hyperlink_content_passes_valid_text(self):
        valid_text = (
            "Apex Global Enterprises Analytics (NEA) Onboarding Portal defines active delivery charters. "
            "The PartnerAdvisory Pod portfolio includes Symphony Outage Journey, Fault Code Database Conduit, "
            "and Billing Journey delivery workstreams. Each charter is assigned a tech lead and PM."
        )
        result = audit_hyperlink_content(valid_text, source_url="https://example.com")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.status_code, "PASS")

    def test_audit_intake_package_blocks_missing_receipt_on_url(self):
        pkg = {
            "artifact_id": "test-pkg",
            "meeting": {
                "source_location": "https://nea-onboarding.static.consulting.example.com/",
                "title": "Onboarding Portal Charters",
            },
            "speakers": [],
        }
        result = audit_intake_package(pkg)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status_code, "MISSING_AUTHENTICATED_CAPTURE_RECEIPT")

    def test_audit_intake_package_passes_non_url_source(self):
        pkg = {
            "artifact_id": "test-pkg-doc",
            "meeting": {
                "source_location": "intake/historical_doc.md",
                "title": "Historical Offline Document",
            },
            "speakers": [],
        }
        result = audit_intake_package(pkg)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.status_code, "NON_URL_SOURCE")

    def test_audit_intake_package_passes_authenticated_capture(self):
        pkg = {
            "artifact_id": "test-pkg-live",
            "meeting": {
                "source_location": "https://nea-onboarding.static.consulting.example.com/",
                "title": "Live Captured Onboarding Portal",
                "capture_receipt": {
                    "method": "AUTHENTICATED_BROWSER_DOM",
                    "browser": "Microsoft Edge",
                    "timestamp_utc": "2026-09-25T17:17:00Z",
                    "sha256": "abcdef1234567890",
                },
            },
            "speakers": [],
        }
        result = audit_intake_package(pkg)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.status_code, "PASS")


if __name__ == "__main__":
    unittest.main()
