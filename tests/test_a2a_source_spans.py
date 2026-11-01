from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import seal_artifact, sha256_bytes, sha256_file
from dual_pipeline.source_spans import (
    SourceSpanError,
    build_span_proposals,
    validate_capture_document,
    validate_span_proposal,
    verify_runtime_a2a_capture,
)
from tools.finalize_a2a_hitl_source_packets import (
    _capture_index,
    _validate_span_ledger,
    finalize_packages,
)


class A2ASourceSpanTests(unittest.TestCase):
    def _capture(self, raw: bytes) -> dict:
        return {
            "raw_sha256": sha256_bytes(raw),
            "raw_bytes": len(raw),
            "visible_text_sha256": sha256_bytes(
                b"Agent handoffs Handoffs delegate tasks to another agent. "
                b"Handoffs are represented as tools to the model."
            ),
            "visible_text_characters": len(
                "Agent handoffs Handoffs delegate tasks to another agent. "
                "Handoffs are represented as tools to the model."
            ),
            "response_headers": {"Content-Type": "text/html; charset=utf-8"},
            "requested_url": "https://example.test/handoffs",
            "final_url": "https://example.test/handoffs",
            "captured_at": "2026-07-25T00:00:00Z",
        }

    def _raw(self) -> bytes:
        return b"""<!doctype html><html><body>
        <h2 id=\"handoffs\">Agent handoffs</h2>
        <p>Handoffs delegate tasks to another agent.</p>
        <p>Handoffs are represented as tools to the model.</p>
        </body></html>"""

    def _fact(self) -> dict:
        return {
            "fact_id": "FACT-004",
            "assertion_text": "Handoffs delegate tasks and are represented as tools to the model.",
            "embedding_text": "Handoffs delegate tasks and are represented as tools to the model.",
            "subject": "handoff",
            "predicate": "is_represented_as",
            "object": "tool to the model",
        }

    def _evidence(self, heading: str = "Agent handoffs", anchor_hint: str = "handoffs") -> dict:
        return {
            "evidence_id": "E-OAI-HANDOFF",
            "source_id": "SRC-OAI-HANDOFF",
            "heading": heading,
            "anchor_hint": anchor_hint,
            "locator_type": "web_extracted_line_range",
            "excerpt_preview": "Handoffs delegate tasks to another agent.",
        }

    def test_proposals_are_bound_to_raw_visible_text_and_offsets(self) -> None:
        raw = self._raw()
        capture = self._capture(raw)
        document = validate_capture_document(capture, raw)

        proposals = build_span_proposals(
            document=document,
            fact=self._fact(),
            evidence=self._evidence(),
            capture=capture,
            capture_id="capture-test",
            capture_manifest_sha256="a" * 64,
            raw_relative_path="raw/SRC-OAI-HANDOFF.response",
            max_proposals=2,
        )

        self.assertEqual(2, len(proposals))
        self.assertEqual("PROPOSED_FOR_HITL", proposals[0]["status"])
        self.assertEqual("FACT-004", proposals[0]["fact_id"])
        self.assertEqual("E-OAI-HANDOFF", proposals[0]["evidence_id"])
        self.assertTrue(proposals[0]["validation"]["raw_hash_valid"])
        self.assertTrue(proposals[0]["validation"]["quote_at_offsets_valid"])
        self.assertTrue(proposals[0]["selection"]["historic_heading_exact_match"])
        self.assertFalse(proposals[0]["selection"]["locator_reconciliation_required"])
        self.assertEqual(
            proposals[0]["quote"],
            document.visible_text[
                proposals[0]["visible_start_offset"] : proposals[0]["visible_end_offset"]
            ],
        )
        for proposal in proposals:
            validate_span_proposal(proposal, document, self._evidence())

    def test_renamed_historic_heading_is_explicitly_reconciliation_required(self) -> None:
        raw = self._raw()
        capture = self._capture(raw)
        document = validate_capture_document(capture, raw)

        proposals = build_span_proposals(
            document=document,
            fact=self._fact(),
            evidence=self._evidence("Handoff workflow", "historic-handoff-workflow"),
            capture=capture,
            capture_id="capture-test",
            capture_manifest_sha256="a" * 64,
            raw_relative_path="raw/SRC-OAI-HANDOFF.response",
            max_proposals=1,
        )

        proposal = proposals[0]
        self.assertFalse(proposal["selection"]["historic_heading_exact_match"])
        self.assertFalse(proposal["selection"]["historic_anchor_match"])
        self.assertTrue(proposal["selection"]["locator_reconciliation_required"])
        self.assertEqual("Agent handoffs", proposal["heading_path"][-1]["text"])

    def test_hash_or_quote_tampering_fails_closed(self) -> None:
        raw = self._raw()
        capture = self._capture(raw)
        with self.assertRaisesRegex(SourceSpanError, "raw response hash"):
            validate_capture_document(capture, raw + b"tampered")

        document = validate_capture_document(capture, raw)
        proposal = build_span_proposals(
            document=document,
            fact=self._fact(),
            evidence=self._evidence(),
            capture=capture,
            capture_id="capture-test",
            capture_manifest_sha256="a" * 64,
            raw_relative_path="raw/SRC-OAI-HANDOFF.response",
            max_proposals=1,
        )[0]
        proposal["quote"] = "tampered"
        with self.assertRaisesRegex(SourceSpanError, "quote does not match"):
            validate_span_proposal(proposal, document, self._evidence())

    def test_finalizer_reconstructs_a_resealed_ledger_from_frozen_raw_capture(self) -> None:
        raw = self._raw()
        capture = self._capture(raw) | {
            "source_id": "SRC-OAI-HANDOFF",
            "status": "CAPTURED",
            "raw_path": "raw/SRC-OAI-HANDOFF.response",
            "evidence": [{"evidence_id": "E-OAI-HANDOFF"}],
        }
        evidence = self._evidence()
        fact = self._fact() | {
            "source_ids_json": ["SRC-OAI-HANDOFF"],
            "evidence_ids_json": ["E-OAI-HANDOFF"],
        }
        verified = {
            "bundle_sha256": "a" * 64,
            "bundle_manifest_sha256": "b" * 64,
            "data": {"evidence_records": [evidence], "fact_candidates": [fact]},
        }
        with tempfile.TemporaryDirectory(prefix="nee-finalizer-span-") as directory:
            root = Path(directory)
            raw_path = root / "raw" / "SRC-OAI-HANDOFF.response"
            raw_path.parent.mkdir()
            raw_path.write_bytes(raw)
            manifest_path = root / "capture_manifest.json"
            manifest = {
                "schema_version": "1.0.0",
                "status": "PASS",
                "bundle": {
                    "sha256": verified["bundle_sha256"],
                    "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
                },
                "capture_id": "capture-test",
                "captures": [capture],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            document = validate_capture_document(capture, raw)
            proposal = build_span_proposals(
                document=document,
                fact=fact,
                evidence=evidence,
                capture=capture,
                capture_id="capture-test",
                capture_manifest_sha256=sha256_file(manifest_path),
                raw_relative_path=capture["raw_path"],
                max_proposals=1,
            )[0]
            ledger_path = root / "ledger.json"
            ledger = seal_artifact(
                {
                    "schema_version": "1.0.0",
                    "ledger_kind": "first_party_evidence_span_proposals",
                    "status": "PASS",
                    "source_bundle": {
                        "sha256": verified["bundle_sha256"],
                        "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
                    },
                    "capture_manifest": {
                        "capture_id": "capture-test",
                        "sha256": sha256_file(manifest_path),
                    },
                    "proposals": [proposal],
                }
            )
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            indexed_manifest, captures = _capture_index(manifest_path, verified)
            indexed_manifest["_path"] = manifest_path
            self.assertEqual(
                {proposal["span_proposal_id"]},
                set(
                    _validate_span_ledger(
                        ledger_path,
                        verified=verified,
                        capture_manifest=indexed_manifest,
                        captures=captures,
                    )
                ),
            )

            tampered = json.loads(ledger_path.read_text(encoding="utf-8"))
            tampered_proposal = tampered["proposals"][0]
            tampered_proposal["quote"] = "forged source text"
            tampered_proposal["quote_sha256"] = sha256_bytes(
                tampered_proposal["quote"].encode("utf-8")
            )
            tampered_proposal["span_proposal_id"] = proposal["span_proposal_id"]
            tampered = seal_artifact(tampered)
            ledger_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reproduc"):
                _validate_span_ledger(
                    ledger_path,
                    verified=verified,
                    capture_manifest=indexed_manifest,
                    captures=captures,
                )

            anchor_tampered = json.loads(json.dumps(ledger))
            anchor_tampered["proposals"][0]["anchor"] = "forged-anchor"
            anchor_tampered = seal_artifact(anchor_tampered)
            ledger_path.write_text(json.dumps(anchor_tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reproduc"):
                _validate_span_ledger(
                    ledger_path,
                    verified=verified,
                    capture_manifest=indexed_manifest,
                    captures=captures,
                )

    def test_finalizer_rejects_source_id_capture_vendor_or_requested_url_swap(self) -> None:
        """A capture cannot borrow a source ID while changing its vendor or URL."""

        raw = self._raw()
        source = {
            "source_id": "SRC-OAI-HANDOFF",
            "vendor": "OpenAI",
            "url": "https://openai.github.io/openai-agents-python/handoffs/",
        }
        verified = {
            "bundle_sha256": "a" * 64,
            "bundle_manifest_sha256": "b" * 64,
            "data": {
                "sources": [source],
                "fact_candidates": [],
                "guidance_practices": [],
                "pattern_guidance": [],
            },
        }
        with tempfile.TemporaryDirectory(prefix="nee-finalizer-source-binding-") as directory:
            root = Path(directory)
            raw_path = root / "raw" / "SRC-OAI-HANDOFF.response"
            raw_path.parent.mkdir()
            raw_path.write_bytes(raw)
            capture = self._capture(raw) | {
                "source_id": source["source_id"],
                "vendor": "Anthropic",
                "requested_url": "https://www.anthropic.com/engineering/",
                "final_url": "https://www.anthropic.com/engineering/",
                "status": "CAPTURED",
                "raw_path": "raw/SRC-OAI-HANDOFF.response",
            }
            manifest_path = root / "capture_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "status": "PASS",
                        "bundle": {
                            "sha256": verified["bundle_sha256"],
                            "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
                        },
                        "capture_id": "capture-source-swap",
                        "captures": [capture],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "tools.finalize_a2a_hitl_source_packets.read_and_verify_bundle",
                return_value=verified,
            ), self.assertRaisesRegex(ValueError, "URL or vendor"):
                finalize_packages(
                    bundle_path=root / "bundle.zip",
                    capture_manifest_path=manifest_path,
                    span_ledger_path=root / "ledger.json",
                    selection_path=root / "selection.json",
                    output_dir=root / "output",
                    audiences=["internal_consulting"],
                )

    def test_runtime_capture_reconstruction_rejects_resealed_package_claims(self) -> None:
        """Ingress must replay retained raw bytes instead of trusting package extensions."""

        raw = self._raw()
        capture = self._capture(raw) | {
            "source_id": "SRC-OAI-HANDOFF",
            "vendor": "OpenAI",
            "status": "CAPTURED",
            "raw_path": "raw/SRC-OAI-HANDOFF.response",
        }
        with tempfile.TemporaryDirectory(prefix="nee-a2a-runtime-capture-") as directory:
            archive_root = Path(directory) / "source_captures" / "fixture"
            raw_path = archive_root / "raw" / "SRC-OAI-HANDOFF.response"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_bytes(raw)
            manifest_path = archive_root / "capture_manifest.json"
            manifest = {
                "schema_version": "1.0.0",
                "status": "PASS",
                "capture_id": "capture-test",
                "bundle": {"sha256": "a" * 64, "bundle_manifest_sha256": "b" * 64},
                "captures": [capture],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            fresh = {
                "capture_id": "capture-test",
                "capture_manifest_sha256": sha256_file(manifest_path),
                "captured_at": capture["captured_at"],
                "requested_url": capture["requested_url"],
                "final_url": capture["final_url"],
                "raw_response_sha256": capture["raw_sha256"],
                "visible_text_sha256": capture["visible_text_sha256"],
            }
            package = {
                "packet": {"source_zip_sha256": "a" * 64, "packet_manifest_sha256": "b" * 64},
                "source": {"source_id": "SRC-OAI-HANDOFF", "vendor": "OpenAI"},
                "extensions": {"com.partner_advisory.nee.a2a-fresh-capture": fresh},
            }
            proof = verify_runtime_a2a_capture(package, capture_root=archive_root)
            self.assertEqual("runtime_capture_reconstructed", proof["verification_state"])

            false_capture = deepcopy(package)
            false_capture["extensions"]["com.partner_advisory.nee.a2a-fresh-capture"][
                "raw_response_sha256"
            ] = "f" * 64
            with self.assertRaisesRegex(SourceSpanError, "does not match the trusted archive"):
                verify_runtime_a2a_capture(false_capture, capture_root=archive_root)

            document = validate_capture_document(capture, raw)
            proposal = build_span_proposals(
                document=document,
                fact=self._fact(),
                evidence=self._evidence(),
                capture=capture,
                capture_id="capture-test",
                capture_manifest_sha256=sha256_file(manifest_path),
                raw_relative_path=capture["raw_path"],
                max_proposals=1,
            )[0]
            span = {
                "source_id": proposal["source_id"],
                "capture_id": proposal["capture_binding"]["capture_id"],
                "raw_response_sha256": proposal["source_version"]["raw_response_sha256"],
                "visible_text_sha256": proposal["source_version"]["visible_text_sha256"],
                "visible_start_offset": proposal["visible_start_offset"],
                "visible_end_offset": proposal["visible_end_offset"],
                "quote": proposal["quote"],
                "quote_sha256": proposal["quote_sha256"],
                "anchor": proposal["anchor"],
            }
            selected = deepcopy(package)
            selected["extensions"]["com.partner_advisory.nee.a2a-evidence-spans"] = {
                "E-OAI-HANDOFF": span
            }
            verify_runtime_a2a_capture(selected, capture_root=archive_root)
            forged_span = deepcopy(selected)
            forged_span["extensions"]["com.partner_advisory.nee.a2a-evidence-spans"][
                "E-OAI-HANDOFF"
            ]["quote"] = "forged source text"
            forged_span["extensions"]["com.partner_advisory.nee.a2a-evidence-spans"][
                "E-OAI-HANDOFF"
            ]["quote_sha256"] = sha256_bytes(b"forged source text")
            with self.assertRaisesRegex(SourceSpanError, "quote does not match"):
                verify_runtime_a2a_capture(forged_span, capture_root=archive_root)


if __name__ == "__main__":
    unittest.main()
