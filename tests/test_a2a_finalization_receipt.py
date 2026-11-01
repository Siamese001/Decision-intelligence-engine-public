import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.a2a_finalization import (
    ARCHIVED_SOURCE_BUNDLE_FILENAME,
    A2AFinalizationError,
    _relative_path,
    _safe_archive_root_id,
    create_finalization_receipt,
    reverify_ingress_finalization_evidence,
    stage_ingress_finalization_evidence,
    verify_finalized_a2a_package,
)
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import seal_artifact, sha256_file
from tools.finalize_a2a_hitl_source_packets import (
    _derived_evidence,
    _derived_source,
    _guidance_candidate,
    _packet,
)

ROOT = Path(__file__).resolve().parents[1]


class A2AFinalizationReceiptTests(unittest.TestCase):
    def _package(self) -> dict:
        return json.loads(
            (
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "valid"
                / "a2a-fact-vector-packet-intake.json"
            ).read_text(encoding="utf-8")
        )

    @staticmethod
    def _verified_bundle(bundle_path: Path) -> dict:
        return {
            "bundle_sha256": sha256_file(bundle_path),
            "bundle_manifest_sha256": "a" * 64,
        }

    def _receipt_and_archive(
        self,
        root: Path,
        package_path: Path,
        *,
        omit_second_raw_capture: bool = False,
    ) -> Path:
        archive_root = root / "runtime" / "source_captures" / "a2a-archive-fixture"
        raw_path = archive_root / "raw" / "source.response"
        raw_path.parent.mkdir(parents=True)
        raw_capture = b"frozen source response"
        captures = [
            {
                "source_id": "source-fixture",
                "raw_path": "raw/source.response",
                "raw_sha256": hashlib.sha256(raw_capture).hexdigest(),
            }
        ]
        if omit_second_raw_capture:
            captures.append(
                {
                    "source_id": "source-fixture-two",
                    "raw_path": "raw/second.response",
                    "raw_sha256": "f" * 64,
                }
            )
        files = {
            "source_bundle.zip": b"source bundle bytes",
            "capture_manifest.json": json.dumps(
                {"capture_id": "capture-fixture", "captures": captures}
            ).encode(),
            "raw/source.response": raw_capture,
            "source_span_ledger.json": b"span ledger",
            "source_span_selection.json": b"selection ledger",
        }
        for relative, content in files.items():
            path = archive_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        roles = {
            "source_bundle.zip": "source_bundle",
            "capture_manifest.json": "capture_manifest",
            "raw/source.response": "raw_capture",
            "source_span_ledger.json": "span_ledger",
            "source_span_selection.json": "selection_ledger",
        }
        archive = {
            "root_id": "a2a-archive-fixture",
            "files": [
                {
                    "role": roles[relative],
                    "relative_path": relative,
                    "sha256": sha256_file(archive_root / relative),
                }
                for relative in sorted(files)
            ],
        }
        package = json.loads(package_path.read_text(encoding="utf-8"))
        receipt = create_finalization_receipt(
            source_bundle={
                "sha256": sha256_file(archive_root / "source_bundle.zip"),
                "bundle_manifest_sha256": "a" * 64,
            },
            capture={
                "capture_id": "capture-fixture",
                "capture_manifest_sha256": sha256_file(archive_root / "capture_manifest.json"),
                "span_ledger_sha256": sha256_file(archive_root / "source_span_ledger.json"),
                "selection_ledger_sha256": sha256_file(
                    archive_root / "source_span_selection.json"
                ),
            },
            archive=archive,
            packages=[
                {
                    "relative_path": "source.reviewed.intake.json",
                    "file_sha256": sha256_file(package_path),
                    "canonical_digest": package["canonical_digest"]["value"],
                    "artifact_id": package["artifact_id"],
                    "source_id": package["source"]["source_id"],
                }
            ],
            audiences=["internal_consulting"],
        )
        receipt_path = root / "finalization_receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return receipt_path

    def test_receipt_requires_exact_package_and_archive_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-a2a-finalization-receipt-") as directory:
            root = Path(directory)
            package_path = root / "source.reviewed.intake.json"
            package_path.write_text(json.dumps(self._package()), encoding="utf-8")
            receipt_path = self._receipt_and_archive(root, package_path)
            package = json.loads(package_path.read_text(encoding="utf-8"))

            def replay_finalizer(**kwargs: object) -> dict:
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True)
                expected = output_dir / "source.reviewed.intake.json"
                expected.write_bytes(package_path.read_bytes())
                return {
                    "packages": [
                        {
                            "path": expected.name,
                            "canonical_digest": package["canonical_digest"]["value"],
                            "artifact_id": package["artifact_id"],
                            "source_id": package["source"]["source_id"],
                        }
                    ]
                }

            with patch(
                "tools.finalize_a2a_hitl_source_packets.finalize_packages",
                side_effect=replay_finalizer,
            ), patch(
                "tools.prepare_a2a_fact_vector_packet_intake.read_and_verify_bundle",
                side_effect=self._verified_bundle,
            ):
                binding = verify_finalized_a2a_package(
                    package_path,
                    receipt_path,
                    runtime_root=root / "runtime",
                )
            binding_data = binding.audit_binding()
            self.assertEqual("finalization_archive_replayed", binding_data["verification_state"])
            self.assertEqual(sha256_file(package_path), binding_data["package_file_sha256"])

            archive_ledger = (
                root
                / "runtime"
                / "source_captures"
                / "a2a-archive-fixture"
                / "source_span_ledger.json"
            )
            archive_ledger.write_bytes(b"tampered ledger")
            with self.assertRaisesRegex(A2AFinalizationError, "file hash"):
                verify_finalized_a2a_package(
                    package_path,
                    receipt_path,
                    runtime_root=root / "runtime",
                )

    def test_receipt_raw_capture_inventory_must_exactly_match_capture_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-a2a-finalization-receipt-") as directory:
            root = Path(directory)
            package_path = root / "source.reviewed.intake.json"
            package_path.write_text(json.dumps(self._package()), encoding="utf-8")
            receipt_path = self._receipt_and_archive(
                root,
                package_path,
                omit_second_raw_capture=True,
            )
            with self.assertRaisesRegex(A2AFinalizationError, "raw-capture inventory"):
                verify_finalized_a2a_package(
                    package_path,
                    receipt_path,
                    runtime_root=root / "runtime",
                )

    def test_receipt_rechecks_its_bundle_manifest_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-a2a-finalization-receipt-") as directory:
            root = Path(directory)
            package_path = root / "source.reviewed.intake.json"
            package_path.write_text(json.dumps(self._package()), encoding="utf-8")
            receipt_path = self._receipt_and_archive(root, package_path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["source_bundle"]["bundle_manifest_sha256"] = "b" * 64
            receipt_path.write_text(json.dumps(seal_artifact(receipt)), encoding="utf-8")
            package = json.loads(package_path.read_text(encoding="utf-8"))

            def replay_finalizer(**kwargs: object) -> dict:
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True)
                expected = output_dir / "source.reviewed.intake.json"
                expected.write_bytes(package_path.read_bytes())
                return {
                    "packages": [
                        {
                            "path": expected.name,
                            "canonical_digest": package["canonical_digest"]["value"],
                            "artifact_id": package["artifact_id"],
                            "source_id": package["source"]["source_id"],
                        }
                    ]
                }

            with patch(
                "tools.finalize_a2a_hitl_source_packets.finalize_packages",
                side_effect=replay_finalizer,
            ), patch(
                "tools.prepare_a2a_fact_vector_packet_intake.read_and_verify_bundle",
                side_effect=self._verified_bundle,
            ), self.assertRaisesRegex(A2AFinalizationError, "manifest summary"):
                verify_finalized_a2a_package(
                    package_path,
                    receipt_path,
                    runtime_root=root / "runtime",
                )

    def test_packet_lineage_uses_archive_stable_bundle_filename(self) -> None:
        source = _derived_source(
            source_id="SRC-NEE-A2A-GUIDANCE",
            title="Derived guidance fixture",
            url="urn:nee:a2a:guidance-fixture",
            bundle_sha256="a" * 64,
            built_at="2026-07-25T00:00:00Z",
            audiences=["internal_consulting"],
        )
        row = {
            "practice_id": "BP-001",
            "category": "ownership",
            "embedding_text": "Guidance fixture for deterministic lineage testing.",
            "eligible_for_fact_promotion": False,
        }
        evidence = _derived_evidence(
            source=source,
            item_id=row["practice_id"],
            text=row["embedding_text"],
            table="guidance_practices",
        )
        packet = _packet(
            verified={
                "bundle_path": Path("a2a_governed_ingestion_bundle_v3.zip"),
                "bundle_sha256": "a" * 64,
                "bundle_manifest_sha256": "b" * 64,
                "upstream": {
                    "packet_id": "nee-openai-anthropic-a2a-facts-v2",
                    "packet_manifest_sha256": "c" * 64,
                },
            },
            source=source,
            evidence=[evidence],
            candidates=[_guidance_candidate(row, evidence["evidence_id"], source)],
            fresh_capture=None,
            evidence_spans=None,
            selection_attestation=None,
            selection_digest=None,
            selection_rows=None,
            lineage_kind="derived_guidance_nonfactual",
        )
        validate_artifact(packet)
        self.assertEqual(
            ARCHIVED_SOURCE_BUNDLE_FILENAME,
            packet["extensions"]["com.partner_advisory.nee.a2a-packet-lineage"][
                "outer_bundle_filename"
            ],
        )

    def test_receipt_can_record_an_all_held_or_rejected_no_content_finalization(self) -> None:
        receipt = create_finalization_receipt(
            source_bundle={"sha256": "a" * 64, "bundle_manifest_sha256": "b" * 64},
            capture={
                "capture_id": "capture-no-content-fixture",
                "capture_manifest_sha256": "c" * 64,
                "span_ledger_sha256": "d" * 64,
                "selection_ledger_sha256": "e" * 64,
            },
            archive={
                "root_id": "a2a-archive-no-content-fixture",
                "files": [
                    {"role": "source_bundle", "relative_path": "source_bundle.zip", "sha256": "f" * 64},
                    {"role": "capture_manifest", "relative_path": "capture_manifest.json", "sha256": "0" * 64},
                    {"role": "raw_capture", "relative_path": "raw/source.response", "sha256": "1" * 64},
                    {"role": "span_ledger", "relative_path": "source_span_ledger.json", "sha256": "2" * 64},
                    {"role": "selection_ledger", "relative_path": "source_span_selection.json", "sha256": "3" * 64},
                ],
            },
            packages=[],
            audiences=["internal_consulting"],
        )
        validate_artifact(receipt)
        self.assertEqual([], receipt["packages"])

    def test_run_retained_packet_and_receipt_are_replayed_before_later_use(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-a2a-finalization-receipt-") as directory:
            root = Path(directory)
            package_path = root / "source.reviewed.intake.json"
            package_path.write_text(json.dumps(self._package()), encoding="utf-8")
            receipt_path = self._receipt_and_archive(root, package_path)
            package = json.loads(package_path.read_text(encoding="utf-8"))

            def replay_finalizer(**kwargs: object) -> dict:
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True)
                expected = output_dir / "source.reviewed.intake.json"
                expected.write_bytes(package_path.read_bytes())
                return {
                    "packages": [
                        {
                            "path": expected.name,
                            "canonical_digest": package["canonical_digest"]["value"],
                            "artifact_id": package["artifact_id"],
                            "source_id": package["source"]["source_id"],
                        }
                    ]
                }

            with patch(
                "tools.finalize_a2a_hitl_source_packets.finalize_packages",
                side_effect=replay_finalizer,
            ), patch(
                "tools.prepare_a2a_fact_vector_packet_intake.read_and_verify_bundle",
                side_effect=self._verified_bundle,
            ):
                proof = verify_finalized_a2a_package(
                    package_path,
                    receipt_path,
                    runtime_root=root / "runtime",
                )
                reference = stage_ingress_finalization_evidence(
                    runtime_root=root / "runtime",
                    run_id="run-a2a-proof-fixture",
                    package_bytes=package_path.read_bytes(),
                    receipt_bytes=receipt_path.read_bytes(),
                    verification=proof,
                )
                replayed = reverify_ingress_finalization_evidence(
                    runtime_root=root / "runtime",
                    reference=reference,
                    expected_binding=proof.audit_binding(),
                )
            self.assertEqual(proof.audit_binding(), replayed.audit_binding())

            retained_packet = root / "runtime" / reference["package_relative_path"]
            retained_packet.write_bytes(b"tampered packet")
            with self.assertRaisesRegex(A2AFinalizationError, "snapshot"):
                reverify_ingress_finalization_evidence(
                    runtime_root=root / "runtime",
                    reference=reference,
                    expected_binding=proof.audit_binding(),
                )

    def test_windows_drive_relative_archive_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(A2AFinalizationError, "safe relative path"):
            _relative_path("C:raw-capture.response", label="test drive-relative path")

    def test_ntfs_alternate_data_stream_archive_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(A2AFinalizationError, "safe relative path"):
            _relative_path("raw-capture.response:hidden", label="test ADS path")

    def test_windows_reserved_device_archive_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(A2AFinalizationError, "safe relative path"):
            _relative_path("raw/CON.response", label="test device path")

    def test_windows_aliasing_archive_root_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(A2AFinalizationError, "valid root ID"):
            _safe_archive_root_id("a2a-archive-fixture.")


if __name__ == "__main__":
    unittest.main()
