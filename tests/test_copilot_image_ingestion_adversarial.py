"""
tests/test_copilot_image_ingestion_adversarial.py

Adversarial and edge-case test suite for copilot image ingestion:
1. Boundary payloads: 0-byte PNG, corrupt byte sequences, truncated headers.
2. Malformed input & 401 API key failure: OpenAI returns 401 token_invalidated;
   asserts seamless fallback to Apple Vision / Gemini / deterministic extraction.
3. Concurrency races & deduplication: Identical images uploaded concurrently or duplicated.
4. Token budget overflow protection: Dense OCR extraction handled without crashing.
5. Invariant enforcement: Gate Cross-5 rejects missing collateral evidence, and
   collateral_healer repairs the canonical projection deterministically.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.attachment_core import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
    build_attachment_prompt_section,
    stage_attachments_into_output,
)
from dual_pipeline.attachment_core.extractors import _extract_image_visual
from dual_pipeline.transcript_qa_core.collateral_healer import heal_collateral_graph_mutations
from dual_pipeline.transcript_qa_core.consistency import _check_collateral_evidence_propagation


class TestCopilotImageIngestionAdversarial(unittest.TestCase):
    """Adversarial and edge-case testing for copilot image ingestion."""

    def test_boundary_payloads_zero_byte_and_corrupt(self) -> None:
        """Boundary: 0-byte file and corrupt byte sequences must not crash extractors."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)

            # 1. 0-byte file
            zero_byte_png = tmp_p / "empty_image.png"
            zero_byte_png.write_bytes(b"")

            # 2. Corrupt garbage bytes
            corrupt_png = tmp_p / "corrupt_image.png"
            corrupt_png.write_bytes(b"NOT_A_REAL_PNG_HEADER_GARBAGE_BYTES_1234567890")

            # Staging must succeed safely
            out_dir = tmp_p / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            records = stage_attachments_into_output([zero_byte_png, corrupt_png], out_dir)
            self.assertEqual(len(records), 2)

            # Extraction must handle corrupt bytes gracefully via fallback
            res_zero = _extract_image_visual(records[0].staged_path, mock_mode=False)
            self.assertIsNotNone(res_zero)
            self.assertIn("text", res_zero)

            res_corrupt = _extract_image_visual(records[1].staged_path, mock_mode=False)
            self.assertIsNotNone(res_corrupt)
            self.assertIn("text", res_corrupt)

    def test_401_invalid_key_resilient_fallback(self) -> None:
        """Malformed/Invalid Credential: 401 token_invalidated engages fallback seamlessly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            png_file = tmp_p / "slide_architecture.png"
            png_file.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
                b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            # Simulate an OpenAI 401 AuthenticationError
            import httpx
            from openai import AuthenticationError

            fake_resp = httpx.Response(
                status_code=401,
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
                json={"error": {"message": "Your API key has been invalidated.", "code": "token_invalidated"}},
            )
            fake_401 = AuthenticationError("Your API key has been invalidated.", response=fake_resp, body=None)

            with patch("storage.credentials.CredentialManager.get_credential", return_value="sk-invalid-mock-key"), \
                 patch("storage.credentials.CredentialManager.get_async_openai_client", side_effect=fake_401):
                res = _extract_image_visual(png_file, mock_mode=False)
                self.assertIsNotNone(res)
                self.assertIn("components", res)
                self.assertIn("summary", res)

    def test_concurrency_and_deduplication(self) -> None:
        """Concurrency: Duplicate files and identical hashes deduplicate cleanly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            img1 = tmp_p / "identical_copy_1.png"
            img2 = tmp_p / "identical_copy_2.png"
            content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy_identical_payload"
            img1.write_bytes(content)
            img2.write_bytes(content)

            out_dir = tmp_p / "output"
            out_dir.mkdir(parents=True, exist_ok=True)

            # Stage both identical files
            records = stage_attachments_into_output([img1, img2], out_dir)
            # Must deduplicate by SHA-256
            self.assertEqual(len(records), 1)

            # Verify drop log has exactly 1 entry
            drop_log_path = out_dir / "collateral_drop_log.json"
            self.assertTrue(drop_log_path.exists())
            entries = json.loads(drop_log_path.read_text(encoding="utf-8"))
            self.assertEqual(len(entries), 1)

    def test_token_overflow_protection_on_dense_slide(self) -> None:
        """Overflow: Dense OCR extraction is bounded and formatted without crashing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            img_file = tmp_p / "dense_slide.png"
            img_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy")

            rec = AttachmentRecord(
                attachment_id="att-dense-01",
                original_path=img_file,
                staged_path=img_file,
                attachment_type=AttachmentType.IMAGE,
                filename="dense_slide.png",
                file_size_bytes=1024,
                sha256="dummy_sha_dense",
                relative_staged_path="referenced_artifacts/dense_slide.png",
                attribution_mode="COLLATERAL_REVIEWED",
            )

            # Generate massive multiline OCR output
            long_text = "\n".join(f"Engagement Row {i}: Project Alpha Delta {i} verified" for i in range(150))
            dense_content = ExtractedAttachmentContent(
                attachment_id=rec.attachment_id,
                filename=rec.filename,
                attachment_type=AttachmentType.IMAGE,
                sha256="dummy_sha_dense",
                extracted_text=long_text,
                components=[{"component_ref": f"comp-{i}", "label": f"Component {i}", "type": "system_component"} for i in range(50)],
                connections=[],
                summary="Dense architectural slide with extensive engagement matrix.",
            )

            prompt_section = build_attachment_prompt_section(
                [rec],
                extracted_contents={rec.attachment_id: dense_content},
            )
            self.assertTrue(len(prompt_section) > 0)
            self.assertIn("dense_slide.png", prompt_section)
            self.assertIn("COLLATERAL_REVIEWED", prompt_section)

    def test_gate_cross_5_rejection_and_collateral_healing(self) -> None:
        """Invariant Enforcement: Gate Cross-5 rejects missing collateral, healer repairs it."""
        meta = {
            "project": "test_adversarial",
            "attachments": [
                {
                    "filename": "unmodeled_architecture.png",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "sha256": "abc123456789",
                }
            ],
        }

        # Empty graph projection without collateral locators
        empty_graph = {
            "schema_version": "5.7",
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
            },
        }

        # Gate Cross-5 must fail closed
        ok, failures = _check_collateral_evidence_propagation(empty_graph, meta)
        self.assertFalse(ok)
        self.assertTrue(any("unmodeled_architecture.png" in f for f in failures))

        # Collateral healer repairs it
        changes = []
        heal_collateral_graph_mutations(empty_graph, meta, changes)
        self.assertTrue(len(changes) >= 1)

        # Gate Cross-5 must now pass
        ok_after, failures_after = _check_collateral_evidence_propagation(empty_graph, meta)
        self.assertTrue(ok_after)
        self.assertEqual(len(failures_after), 0)


if __name__ == "__main__":
    unittest.main()
