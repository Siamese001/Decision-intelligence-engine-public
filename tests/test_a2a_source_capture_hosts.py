import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from tools.capture_a2a_first_party_sources import (
    PinnedVendorRedirectHandler,
    _raw_capture_filename,
    capture_bundle,
)


class A2ASourceCaptureHostTests(unittest.TestCase):
    def _verified(self, bundle_path: Path, url: str) -> dict:
        return {
            "data": {
                "sources": [
                    {
                        "source_id": "SRC-OAI-FIXTURE",
                        "vendor": "OpenAI",
                        "url": url,
                    }
                ],
                "evidence_records": [],
            },
            "bundle_path": bundle_path,
            "bundle_sha256": "a" * 64,
            "bundle_manifest_sha256": "b" * 64,
            "upstream": {"packet_id": "fixture", "packet_manifest_sha256": "c" * 64},
        }

    def test_unpinned_declared_url_is_rejected_before_output_or_egress(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-a2a-host-") as directory:
            root = Path(directory)
            bundle_path = root / "bundle.zip"
            bundle_path.write_bytes(b"fixture")
            output_dir = root / "capture"
            with patch(
                "tools.capture_a2a_first_party_sources.read_and_verify_bundle",
                return_value=self._verified(bundle_path, "https://evil.invalid/a2a"),
            ), self.assertRaisesRegex(ValueError, "pinned official host"):
                capture_bundle(bundle_path, output_dir, timeout_seconds=1)
            self.assertFalse(output_dir.exists())

    def test_redirect_handler_rejects_an_unpinned_hop_before_following_it(self) -> None:
        handler = PinnedVendorRedirectHandler("OpenAI")
        with self.assertRaisesRegex(ValueError, "pinned official host"):
            handler.redirect_request(
                Request("https://openai.github.io/start"),
                None,
                302,
                "Found",
                {},
                "https://evil.invalid/redirected",
            )

    def test_terminal_host_is_checked_before_response_bytes_are_consumed(self) -> None:
        class Response:
            url = "https://evil.invalid/final"
            headers = {"Content-Type": "text/html; charset=utf-8"}
            status = 200

            def __init__(self) -> None:
                self.read_called = False

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                self.read_called = True
                return b"should not be consumed"

        class Opener:
            def __init__(self, response: Response) -> None:
                self.response = response

            def open(self, request: Request, timeout: int) -> Response:
                return self.response

        with tempfile.TemporaryDirectory(prefix="nee-a2a-host-") as directory:
            root = Path(directory)
            bundle_path = root / "bundle.zip"
            bundle_path.write_bytes(b"fixture")
            response = Response()
            with patch(
                "tools.capture_a2a_first_party_sources.read_and_verify_bundle",
                return_value=self._verified(
                    bundle_path, "https://openai.github.io/openai-agents-python/"
                ),
            ), patch(
                "tools.capture_a2a_first_party_sources.build_opener",
                return_value=Opener(response),
            ), self.assertRaisesRegex(ValueError, "pinned official host"):
                capture_bundle(bundle_path, root / "capture", timeout_seconds=1)
            self.assertFalse(response.read_called)

    def test_contract_identifier_is_not_reused_as_a_windows_capture_filename(self) -> None:
        filename = _raw_capture_filename("SRC:OPENAI/FIXTURE")
        self.assertTrue(filename.endswith(".response"))
        self.assertNotIn(":", filename)
        self.assertNotIn("/", filename)
        self.assertNotIn("\\", filename)


if __name__ == "__main__":
    unittest.main()
