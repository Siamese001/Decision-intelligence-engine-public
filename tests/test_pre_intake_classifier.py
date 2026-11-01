from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import read_json
from dual_pipeline.intake_classifier import sensitive_signal_types
from dual_pipeline.pre_intake_classifier import (
    AUDIENCE_PLACEHOLDER,
    CLASSIFICATION_PLACEHOLDER,
    DEFAULT_POLICY_PATH,
    PreIntakePolicyError,
    load_pre_intake_policy,
    propose_pre_intake_classification,
)


class PreIntakeClassifierTests(unittest.TestCase):
    def _meeting(
        self,
        *,
        classification: str = CLASSIFICATION_PLACEHOLDER,
        audiences: list[str] | None = None,
        organizer: str = "Unknown organizer",
        attendees: list[str] | None = None,
    ) -> dict:
        return {
            "artifact_id": "prompt-derived-classification-case",
            "meeting": {
                "meeting_id": "meeting-prompt-derived-case",
                "title": "Prompt-derived ingestion classification case",
                "organizer": organizer,
                "attendees": attendees or ["Example Participant"],
                "classification": classification,
                "audiences": audiences or [AUDIENCE_PLACEHOLDER],
            },
            "speakers": [],
        }

    def _propose(self, package: dict, source_text: str) -> dict:
        return propose_pre_intake_classification(
            package=package,
            source_bytes=source_text.encode("utf-8"),
        )

    def test_prompt_derived_classification_cases(self) -> None:
        cases = [
            {
                "name": "unclassified_public_language_stays_internal",
                "package": self._meeting(),
                "source": "Published press release available on the public website.",
                "classification": "internal",
                "signals": set(),
            },
            {
                "name": "explicit_public_floor_is_preserved",
                "package": self._meeting(
                    classification="public",
                    audiences=["public_web"],
                ),
                "source": "Published press release available on the public website.",
                "classification": "public",
                "signals": set(),
            },
            {
                "name": "routine_internal_meeting",
                "package": self._meeting(
                    organizer="Operator <operator@consulting.example.com>",
                ),
                "source": "Routine internal engineering status and documented follow-up.",
                "classification": "internal",
                "signals": set(),
                "audiences": ["internal_consulting"],
            },
            {
                "name": "client_security_architecture",
                "package": self._meeting(
                    organizer="Operator <operator@consulting.example.com>",
                    attendees=["Architect <architect@grid.example.com>"],
                ),
                "source": (
                    "Authentication and authorization across separate AWS accounts "
                    "requires security architecture decisions."
                ),
                "classification": "confidential",
                "signals": {
                    "customer_partner_participant_boundary",
                    "security_sensitive_architecture",
                },
                "audiences": ["internal_consulting", "nee-engineering"],
            },
            {
                "name": "contractual_and_pricing_material",
                "package": self._meeting(),
                "source": "The statement of work contains pricing and procurement terms.",
                "classification": "confidential",
                "signals": {"contractual_or_commercial_material"},
            },
            {
                "name": "personal_data_shape",
                "package": self._meeting(),
                "source": "Synthetic prompt test SSN 123-45-6789.",
                "classification": "confidential",
                "signals": {"us_social_security_number"},
            },
            {
                "name": "regulated_data",
                "package": self._meeting(),
                "source": "The workflow may process protected health information under HIPAA.",
                "classification": "confidential",
                "signals": {"regulated_or_payment_data"},
            },
            {
                "name": "cloud_credential_shape",
                "package": self._meeting(),
                "source": (
                    "Synthetic key shape "
                    + "AKIA"
                    + "ABCDEFGHIJKLMNOP for classifier testing."
                ),
                "classification": "restricted",
                "signals": {"cloud_access_key"},
            },
            {
                "name": "private_key_shape",
                "package": self._meeting(),
                "source": (
                    "Synthetic marker -----BEGIN "
                    + "PRIVATE KEY----- without key bytes."
                ),
                "classification": "restricted",
                "signals": {"private_key_material"},
            },
            {
                "name": "declared_restricted_is_never_lowered",
                "package": self._meeting(
                    classification="restricted",
                    audiences=["security_reviewers"],
                ),
                "source": "Routine status with no detected sensitive shapes.",
                "classification": "restricted",
                "signals": set(),
                "audiences": ["security_reviewers"],
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                proposal = self._propose(case["package"], case["source"])
                self.assertEqual(
                    case["classification"],
                    proposal["proposed_classification"],
                )
                self.assertEqual(
                    case["signals"],
                    set(proposal["signal_types"]),
                )
                if "audiences" in case:
                    self.assertEqual(
                        case["audiences"],
                        proposal["proposed_audiences"],
                    )
                self.assertEqual(0, proposal["classifier"]["provider_call_count"])
                self.assertTrue(proposal["operator_approval_required"])
                self.assertFalse(proposal["graph_mutation_authorized"])

    def test_declared_audiences_are_never_broadened_by_domain_rules(self) -> None:
        package = self._meeting(
            classification="confidential",
            audiences=["internal_consulting"],
            organizer="Operator <operator@consulting.example.com>",
            attendees=["Architect <architect@enterprise.example.com>"],
        )
        proposal = self._propose(
            package,
            "Authentication and authorization architecture review.",
        )
        self.assertEqual(["internal_consulting"], proposal["proposed_audiences"])
        self.assertEqual("declared_audience_floor", proposal["audience_basis"])

    def test_unknown_domains_do_not_invent_an_audience(self) -> None:
        proposal = self._propose(
            self._meeting(
                organizer="Example <example@unmapped.invalid>",
                attendees=["Other <other@unknown.invalid>"],
            ),
            "Routine working session.",
        )
        self.assertEqual([], proposal["proposed_audiences"])
        self.assertEqual("operator_input_required", proposal["audience_basis"])

    def test_report_is_deterministic_and_does_not_emit_sensitive_matches(self) -> None:
        source_text = "Synthetic SSN 123-45-6789 and pricing discussion."
        package = self._meeting()
        first = self._propose(package, source_text)
        second = self._propose(package, source_text)
        self.assertEqual(first, second)
        rendered = json.dumps(first, sort_keys=True)
        self.assertNotIn("123-45-6789", rendered)
        self.assertNotIn("pricing discussion", rendered)
        self.assertEqual(64, len(first["proposal_digest"]))
        self.assertEqual(64, len(first["draft_digest"]))
        self.assertEqual(64, len(first["policy"]["policy_file_sha256"]))

        changed_package = copy.deepcopy(package)
        changed_package["meeting"]["attendees"] = ["Architect <architect@enterprise.example.com>"]
        changed = self._propose(changed_package, source_text)
        self.assertNotEqual(first["draft_digest"], changed["draft_digest"])
        self.assertNotEqual(first["proposal_id"], changed["proposal_id"])

    def test_post_package_sensitive_shapes_use_word_boundaries(self) -> None:
        package = {
            "synthetic_test_values": [
                "123-45-6789",
                "AKIA" + "ABCDEFGHIJKLMNOP",
            ]
        }
        self.assertEqual(
            {
                ("cloud_access_key", "restricted"),
                ("us_social_security_number", "confidential"),
            },
            set(sensitive_signal_types(package)),
        )

    def test_invalid_regex_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-pre-intake-policy-") as directory:
            root = Path(directory)
            policy = copy.deepcopy(read_json(DEFAULT_POLICY_PATH))
            policy["signal_rules"][0]["pattern"] = "("
            path = root / "invalid-policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(PreIntakePolicyError, "pattern is invalid"):
                load_pre_intake_policy(path)

    def test_public_policy_default_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-pre-intake-policy-") as directory:
            root = Path(directory)
            policy = copy.deepcopy(read_json(DEFAULT_POLICY_PATH))
            policy["default_classification"] = "public"
            path = root / "unsafe-public-default.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(
                PreIntakePolicyError,
                "must not be public",
            ):
                load_pre_intake_policy(path)

    def test_missing_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-pre-intake-policy-") as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(PreIntakePolicyError, "policy is missing"):
                load_pre_intake_policy(missing)


if __name__ == "__main__":
    unittest.main()
