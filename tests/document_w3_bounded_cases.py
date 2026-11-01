"""Offline bounded patch cases, mixed into the governed Wave 3 suite."""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock, patch

from dual_pipeline.document_pipeline_core.bounded_patch import (
    build_bounded_patch_request, validate_patch_preservation, validate_patch_result,
)
from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer


def _fixture():
    source = "The platform validates the records.\nThe records are controlled."
    evidence = {}
    for identity, quote in zip(("ev-platform", "ev-records"), source.splitlines()):
        evidence[identity] = {
            "statement_id": identity, "speaker": "Document", "source_classification": "FACT",
            "kind": "technical", "text": quote, "exact_text": quote,
            "locator": {"kind": "section", "value": "Document Body"},
        }
    candidate = {
        "schema_version": "5.7", "meeting_id": "fixture-document", "classification": "internal",
        "audiences": ["fixture-team"], "evidence_index": evidence, "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:platform", "type": "system", "name": "Platform", "description": "Initial description.", "mutation": "CONFIRM", "evidence_ids": ["ev-platform"]},
                {"id": "system:records", "type": "system", "name": "Records", "description": "Controlled records.", "mutation": "CONFIRM", "evidence_ids": ["ev-records"]},
            ],
            "relationship_signals": [{"signal_id": "rel-validates", "from_id": "system:platform", "to_id": "system:records", "relationship": "VALIDATES", "predicate": "VALIDATES", "evidence_ids": ["ev-platform"]}],
            "decision_updates": [], "proposal_updates": [], "risk_updates": [], "action_updates": [],
        },
    }
    report = {"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL", "failed_claims": [
        {"collection": "entity_upserts", "id": "system:platform", "reason": "Description must reflect the validation role."},
    ]}}
    request = build_bounded_patch_request(candidate, report, source)
    replacement = copy.deepcopy(request["allowed_objects"][0])
    replacement["value"]["description"] = "The platform validates the records."
    response = {key: request[key] for key in ("contract_version", "candidate_sha256", "source_sha256", "request_sha256")}
    response["changes"] = [replacement]
    return source, candidate, request, response


class BoundedPatchCases:
    def test_bounded_patch_preserves_unrelated_objects_and_input(self):
        source, candidate, request, response = _fixture()
        before = copy.deepcopy(candidate)
        merged = json.loads(validate_patch_result(candidate, request, response, source))
        self.assertEqual(candidate, before)
        self.assertEqual(merged["evidence_index"], before["evidence_index"])
        self.assertEqual(merged["graph_mutations"]["entity_upserts"][1], before["graph_mutations"]["entity_upserts"][1])
        self.assertEqual(merged["graph_mutations"]["relationship_signals"], before["graph_mutations"]["relationship_signals"])
        self.assertEqual(merged["graph_mutations"]["entity_upserts"][0]["description"], source.splitlines()[0])
        validate_patch_preservation(candidate, request, merged)

    def test_bounded_patch_rejects_malformed_or_full_projection_response(self):
        source, candidate, request, _response = _fixture()
        for bad in ("not JSON", "[]", '{"changes":[],"changes":[]}', candidate):
            with self.subTest(response=type(bad).__name__), self.assertRaises(ValueError):
                validate_patch_result(candidate, request, bad, source)

    def test_bounded_patch_rejects_unrelated_or_spoofed_ids(self):
        source, candidate, request, response = _fixture()
        for identity in ("system:records", "system:invented", ["system:platform"]):
            bad = copy.deepcopy(response)
            bad["changes"][0]["id"] = identity
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                validate_patch_result(candidate, request, bad, source)
        bad = copy.deepcopy(response)
        bad["changes"][0]["value"]["id"] = "system:records"
        with self.assertRaises(ValueError):
            validate_patch_result(candidate, request, bad, source)

    def test_bounded_patch_rejects_candidate_source_request_and_before_digest_spoof(self):
        source, candidate, request, response = _fixture()
        for field in ("candidate_sha256", "source_sha256", "request_sha256"):
            bad = copy.deepcopy(response)
            bad[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_patch_result(candidate, request, bad, source)
        bad = copy.deepcopy(response)
        bad["changes"][0]["before_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_patch_result(candidate, request, bad, source)
        with self.assertRaises(ValueError):
            validate_patch_result(candidate, request, response, source + " changed")

    def test_bounded_patch_rejects_extra_fields_and_duplicate_changes(self):
        source, candidate, request, response = _fixture()
        bad = copy.deepcopy(response)
        bad["changes"][0]["value"]["unrelated_new_field"] = True
        with self.assertRaises(ValueError):
            validate_patch_result(candidate, request, bad, source)
        bad = copy.deepcopy(response)
        bad["changes"].append(copy.deepcopy(bad["changes"][0]))
        with self.assertRaises(ValueError):
            validate_patch_result(candidate, request, bad, source)

    def test_bounded_patch_rechecks_unchanged_evidence_grounding(self):
        source, candidate, _request, _response = _fixture()
        candidate["evidence_index"]["ev-records"]["exact_text"] = "The records are controlled by an invented council."
        report = {"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL", "actionable_remediation_diff": "Fix system:platform description."}}
        request = build_bounded_patch_request(candidate, report, source)
        response = {key: request[key] for key in ("contract_version", "candidate_sha256", "source_sha256", "request_sha256")}
        response["changes"] = [copy.deepcopy(request["allowed_objects"][0])]
        with self.assertRaisesRegex(ValueError, "verbatim grounded"):
            validate_patch_result(candidate, request, response, source)

    def test_bounded_patch_rechecks_references_and_source_coverage(self):
        source, candidate, request, response = _fixture()
        response["changes"][0]["value"]["evidence_ids"] = ["ev-invented"]
        with self.assertRaisesRegex(ValueError, "dangling evidence"):
            validate_patch_result(candidate, request, response, source)
        source, candidate, request, response = _fixture()
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_patch_result(candidate, request, response, source, {"source_coverage": {"complete": False}})

    def test_bounded_patch_rejects_duplicate_candidate_ids(self):
        source, candidate, _request, _response = _fixture()
        candidate["graph_mutations"]["entity_upserts"].append(copy.deepcopy(candidate["graph_mutations"]["entity_upserts"][0]))
        report = {"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL", "actionable_remediation_diff": "Fix system:platform."}}
        with self.assertRaisesRegex(ValueError, "Duplicate existing"):
            build_bounded_patch_request(candidate, report, source)

    def test_bounded_patch_preservation_rejects_healer_drift(self):
        _source, candidate, request, _response = _fixture()
        changed = copy.deepcopy(candidate)
        changed["evidence_index"]["ev-records"]["text"] = "Unrelated healing edit"
        with self.assertRaisesRegex(ValueError, "unrelated"):
            validate_patch_preservation(candidate, request, changed)
        changed = copy.deepcopy(candidate)
        changed["classification"] = "public"
        with self.assertRaisesRegex(ValueError, "unrelated"):
            validate_patch_preservation(candidate, request, changed)

    def test_bounded_patch_executor_fails_before_provider_for_invalid_request(self):
        source, candidate, request, _response = _fixture()
        provider = Mock()
        request["candidate_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            DocumentGraphExtractorExecutor(provider).execute(source, {"metadata": {}}, attempt=2,
                document_patch_request=request, prior_graph_candidate=candidate)
        provider.synthesize_graph.assert_not_called()
        with self.assertRaises(ValueError):
            DocumentGraphExtractorExecutor(provider).execute(source, {"metadata": {}}, attempt=2)
        provider.synthesize_graph.assert_not_called()

    def test_bounded_patch_executor_has_one_call_and_no_regeneration_fallback(self):
        source, candidate, request, response = _fixture()
        provider = Mock()
        provider.synthesize_graph.return_value = json.dumps(response)
        with patch("dual_pipeline.document_pipeline_core.executor._post_process_document_projection") as post:
            result = DocumentGraphExtractorExecutor(provider).execute(source, {"metadata": {}}, attempt=2,
                document_patch_request=request, prior_graph_candidate=candidate)
            self.assertEqual(json.loads(result)["meeting_id"], "fixture-document")
            self.assertEqual(provider.synthesize_graph.call_count, 1)
            post.assert_not_called()
            provider.synthesize_graph.return_value = json.dumps(candidate)
            with self.assertRaises(ValueError):
                DocumentGraphExtractorExecutor(provider).execute(source, {"metadata": {}}, attempt=2,
                    document_patch_request=request, prior_graph_candidate=candidate)
            self.assertEqual(provider.synthesize_graph.call_count, 2)
            post.assert_not_called()

    def test_normal_document_one_extraction_call_records_offline_fixture(self):
        source, candidate, _request, _response = _fixture()
        provider = OfflineTranscriptSynthesizer()
        receipt = {"requested_model": "fixture-request", "requested_effort": "low"}
        provider._document_execution_receipt = receipt
        metadata = {}
        with patch.object(provider, "synthesize_graph", return_value=json.dumps(candidate)) as call:
            with patch("dual_pipeline.document_pipeline_core.executor._post_process_document_projection", return_value=json.dumps(candidate)):
                DocumentGraphExtractorExecutor(provider).execute(source, {"metadata": metadata})
        self.assertEqual(call.call_count, 1)
        self.assertIs(metadata["_extraction_execution"], receipt)
        self.assertEqual(receipt["status"], "OFFLINE_FIXTURE")
        self.assertIsNone(receipt["effective_model"])
        self.assertIsNone(receipt["effective_effort"])
        self.assertEqual(receipt["provider_type"], "offline")
        self.assertEqual(receipt["requested_model"], "fixture-request")

    def test_bounded_patch_rejects_nested_citation_and_quote_spoof(self):
        source, candidate, _request, _response = _fixture()
        edge = candidate["graph_mutations"]["relationship_signals"][0]
        edge["evidence"] = {"statement_id": "ev-platform", "direct_quote": source.splitlines()[0],
                            "locator": {"kind": "section", "value": "Document Body"}}
        report = {"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL", "failed_claims": [
            {"collection": "relationship_signals", "id": "rel-validates", "reason": "Verify the citation."}]}}
        request = build_bounded_patch_request(candidate, report, source)
        response = {key: request[key] for key in ("contract_version", "candidate_sha256", "source_sha256", "request_sha256")}
        response["changes"] = [copy.deepcopy(request["allowed_objects"][0])]
        for changes in ({"statement_id": "ev-invented"}, {"direct_quote": "Invented validation claim."},
                        {"statement_id": "ev-records"}):
            bad = copy.deepcopy(response)
            bad["changes"][0]["value"]["evidence"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_patch_result(candidate, request, bad, source)
