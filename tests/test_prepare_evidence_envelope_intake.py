from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import artifact_digest, read_json
from tools.prepare_evidence_envelope_intake import (
    HITLRequired,
    prepare_package,
)

ROOT = Path(__file__).resolve().parents[1]


class PrepareEvidenceEnvelopeIntakeTests(unittest.TestCase):
    def _draft(self) -> dict:
        fixture = read_json(
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        return json.loads(json.dumps(fixture))

    def test_prepare_package_validates_and_reproduces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_file = temp_path / "source.txt"
            source_content = (
                "S01\nDrew Oakley: First principles rebuild. We need to evaluate the entire pipeline "
                "architecture from first principles to ensure that every stage produces verifiable evidence. "
                "In this discussion we are focusing on unifying schema contracts, establishing deterministic gates, "
                "and validating that our end-to-end integration remains resilient and reproducible across environments "
                "without manual intervention or speculative execution."
            )
            source_file.write_text(source_content, encoding="utf-8")
            draft_file = temp_path / "draft.json"
            output_file = temp_path / "package.json"

            draft = self._draft()
            draft_file.write_text(json.dumps(draft, indent=2), encoding="utf-8")

            result = prepare_package(
                draft_path=draft_file,
                source_path=source_file,
                output_path=output_file,
                source_location=str(source_file),
                classification="confidential",
                audiences=["internal_consulting", "nee-engineering"],
                started_at="2026-09-04T09:30:00-04:00",
                organizer="Drew Oakley",
            )

            self.assertEqual(
                result["contract_id"],
                "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            )
            package = read_json(output_file)
            validate_artifact(package)
            self.assertEqual(
                package["contract_id"],
                "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            )
            self.assertEqual(
                package["canonical_digest"]["value"],
                artifact_digest(package),
            )
            self.assertEqual(
                package["canonical_digest"]["value"],
                result["canonical_digest"]["value"],
            )

    def test_missing_classification_raises_hitl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_file = temp_path / "source.txt"
            source_content = (
                "S01\nSource text. We are reviewing the classification criteria for meeting intake notes. "
                "Every session must specify an explicit classification tier such as internal, confidential, "
                "or client-facing. If classification is absent, the pipeline must raise a HITL request "
                "to ensure operator authorization before proceeding to downstream processing stages. "
                "This ensures continuous compliance and governance across all data pipelines and artifacts."
            )
            source_file.write_text(source_content, encoding="utf-8")
            draft_file = temp_path / "draft.json"
            output_file = temp_path / "package.json"

            draft = self._draft()
            draft["meeting"]["classification"] = None
            draft_file.write_text(json.dumps(draft, indent=2), encoding="utf-8")

            with self.assertRaises(HITLRequired) as ctx:
                prepare_package(
                    draft_path=draft_file,
                    source_path=source_file,
                    output_path=output_file,
                    source_location=str(source_file),
                    classification=None,
                    audiences=["internal_consulting"],
                )
            fields = [r["field"] for r in ctx.exception.request["requested_decisions"]]
            self.assertIn("classification", fields)

    def test_v53_canonical_signal_projection_adaptation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_file = temp_path / "source.txt"
            source_content = (
                "0:03\nCarlos: Systems design discussion. We are discussing the candidate design "
                "and architectural boundaries for the ingestion engine. All action items and entity upserts "
                "must be grounded directly in forensic transcript turns with explicit locators and verified speakers. "
                "This guarantees traceability across the entire lifecycle, ensuring high-fidelity extraction "
                "and preventing ungrounded hallucinations from polluting the knowledge graph."
            )
            source_file.write_text(source_content, encoding="utf-8")
            draft_file = temp_path / "03_canonical_signal_projection.json"
            output_file = temp_path / "package.json"

            v53_draft = {
                "meeting_id": "meeting-20260827-pd-conduit-catch-up",
                "evidence_index": {
                    "stmt-01": {
                        "speaker": "Carlos Salazar",
                        "source_classification": "OBSERVATION",
                        "text": "Systems design discussion.",
                        "locator": {"kind": "timestamp", "value": "0:03"},
                    }
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {
                            "id": "person:carlos-salazar",
                            "type": "person",
                            "name": "Carlos Salazar",
                            "mutation": "CREATE",
                        }
                    ],
                    "action_updates": [
                        {
                            "action_id": "act-01",
                            "action_text": "Follow up on systems design interview criteria.",
                            "owner": "Carlos Salazar",
                            "locator": {"kind": "timestamp", "value": "0:03"},
                        }
                    ],
                    "decision_updates": [],
                },
                "evidence_only_statement_ids": [],
            }
            draft_file.write_text(json.dumps(v53_draft, indent=2), encoding="utf-8")

            result = prepare_package(
                draft_path=draft_file,
                source_path=source_file,
                output_path=output_file,
                source_location=str(source_file),
                classification="internal",
                audiences=["internal_consulting"],
                started_at="2026-08-27T15:30:00-04:00",
                organizer="Amit Ayer",
            )

            self.assertEqual(
                result["contract_id"],
                "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            )
            self.assertEqual(len(result["speakers"]), 1)
            self.assertEqual(result["speakers"][0]["reported_name"], "Carlos Salazar")
            self.assertEqual(len(result["actions"]), 1)
            self.assertIn("com.partner_advisory.nee.canonical-signal-projection", result["extensions"])
            validate_artifact(result)

    def test_v53_hitl_locator_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_file = temp_path / "01_transcript.md"
            source_content = (
                "# Weekly Alignment -- Forensic Transcript\n\n"
                "[00:00:00] Drew Oakley: Welcome everyone. We are discussing pipeline architecture.\n"
                "[00:00:15] Amit Ayer: Agreed, we must ensure all evidence locators bind properly.\n\n"
                "## Ambiguity Lock Register\n"
                "| Item # | Identified Ambiguity | Source Locator | Final Resolved Record |\n"
                "| 1 | Speaker Attribution | [00:00:00] | Operator verified Robson N. Silva |\n"
            )
            source_file.write_text(source_content, encoding="utf-8")
            draft_file = temp_path / "03_canonical_signal_projection.json"
            output_file = temp_path / "package.json"

            v53_draft = {
                "meeting_id": "meeting-20261013-alignment",
                "evidence_index": {
                    "stmt_hitl_01": {
                        "speaker": "Operator",
                        "source_classification": "summary",
                        "text": "Human operator verified professional title for Robson N. Silva.",
                        "locator": {"kind": "hitl_decision_ledger", "value": "hitl"},
                    }
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {
                            "id": "person:robson-silva",
                            "type": "person",
                            "name": "Robson N. Silva",
                            "evidence": {
                                "statement_id": "stmt_hitl_01",
                                "locator": {"kind": "hitl_decision_ledger", "value": "hitl"},
                            },
                        }
                    ],
                    "action_updates": [],
                    "decision_updates": [],
                },
            }
            draft_file.write_text(json.dumps(v53_draft, indent=2), encoding="utf-8")

            result = prepare_package(
                draft_path=draft_file,
                source_path=source_file,
                output_path=output_file,
                source_location=str(source_file),
                classification="internal",
                audiences=["internal_consulting"],
                started_at="2026-10-13T15:00:00-04:00",
                organizer="Drew Oakley",
            )

            self.assertEqual(
                result["contract_id"],
                "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            )
            stmt = result["speakers"][0]["statements"][0]
            self.assertEqual(stmt["locator"]["kind"], "section")
            self.assertIn("Ambiguity Lock Register", stmt["locator"]["value"])
            validate_artifact(result)


if __name__ == "__main__":
    unittest.main()
