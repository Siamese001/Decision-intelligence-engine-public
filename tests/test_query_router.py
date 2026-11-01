from __future__ import annotations

import unittest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.query_router import (
    MAX_QUESTION_LENGTH,
    QueryDestination,
    _routing_text,
    load_query_routing_policy,
    route_query,
)


class QueryRouterTests(unittest.TestCase):
    def test_meeting_summary_routes_out(self) -> None:
        decision = route_query("Summarize these meeting notes into actions.")
        self.assertEqual(QueryDestination.GENERAL_ASSISTANT_HANDOFF, decision.destination)
        self.assertFalse(decision.enters_query_runtime)
        self.assertEqual("NOT_ELIGIBLE", decision.eligibility_status)

    def test_complex_workbook_is_operational_not_nee(self) -> None:
        decision = route_query(
            "Rebuild the complex staffing workbook with formulas, allocations, risks, and an executive summary."
        )
        self.assertEqual(QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, decision.destination)
        self.assertFalse(decision.enters_query_runtime)

    def test_bounded_record_lookup_uses_evidence_utility(self) -> None:
        decision = route_query("Show governed record record-123.")
        self.assertEqual(QueryDestination.GRAPH_EVIDENCE_UTILITY, decision.destination)
        self.assertEqual("exact_lookup", decision.query_type)

    def test_short_graph_essential_diagnostic_uses_nee(self) -> None:
        decision = route_query(
            "Diagnose the cross-source governed graph relationships and counterevidence."
        )
        self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, decision.destination)
        self.assertEqual("gap_risk_inconsistency", decision.query_type)
        self.assertTrue(all(value == "PASS" for value in decision.eligibility_checks.values()))

    def test_ambiguous_request_does_not_call_a_classifier(self) -> None:
        decision = route_query("Tell me about Chakra.")
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)
        self.assertFalse(decision.enters_query_runtime)

    def test_destination_artifact_is_contract_valid(self) -> None:
        decision = route_query(
            "Recommend a future-state A2A architecture across governed current state, patterns, risks, and gaps.",
            delivery_profile="client_executive",
            artifact_type="architecture_deck",
        )
        artifact = decision.to_artifact(
            request_id="request-router-test",
            run_id="run-router-test",
            trace_id="0123456789abcdef0123456789abcdef",
            decided_at="2026-08-01T12:00:00Z",
        )
        validate_artifact(artifact)
        self.assertEqual("client_executive", artifact["delivery_profile"])
        self.assertEqual(0, artifact["automatic_egress_count"])

    def test_operator_override_cannot_mix_handoff_and_query_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "incompatible"):
            route_query(
                "Explain the governed relationship.",
                query_type_override="relationship_explanation",
                destination_override="GENERAL_ASSISTANT_HANDOFF",
            )

    def test_router_rejects_non_string_overlong_and_non_boolean_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "question must be a string"):
            route_query(None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            route_query("x" * (MAX_QUESTION_LENGTH + 1))
        for value in ("false", 1, 0, None, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "boolean"):
                route_query(
                    "Diagnose governed NEE graph relationships across evidence.",
                    authorized=value,  # type: ignore[arg-type]
                )

    def test_router_accepts_question_at_contract_limit(self) -> None:
        seed = "Tell me about Chakra. "
        decision = route_query(seed + "x" * (MAX_QUESTION_LENGTH - len(seed)))
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)

    def test_incompatible_graph_and_nee_overrides_are_rejected(self) -> None:
        cases = (
            ("GRAPH_EVIDENCE_UTILITY", "relationship_explanation"),
            ("GRAPH_EVIDENCE_UTILITY", "cross_workstream_synthesis"),
            ("NEE_DECISION_INTELLIGENCE", "exact_lookup"),
            ("NEE_DECISION_INTELLIGENCE", "current_state_corroboration"),
        )
        for destination, query_type in cases:
            with self.subTest(destination=destination, query_type=query_type):
                with self.assertRaisesRegex(ValueError, "requires"):
                    route_query(
                        "Diagnose governed NEE graph relationships across evidence.",
                        destination_override=destination,
                        query_type_override=query_type,
                    )

    def test_mixed_destination_actions_fail_closed(self) -> None:
        decision = route_query(
            "List exact governed NEE records and recommend an architecture."
        )
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)
        self.assertIn("MULTI_DESTINATION_CONFLICT", decision.reason_codes)
        self.assertFalse(decision.enters_query_runtime)

    def test_quoted_and_negated_payload_signals_do_not_route_work(self) -> None:
        quoted = route_query("Summarize this sentence only: `commit the repository`.")
        self.assertEqual(QueryDestination.GENERAL_ASSISTANT_HANDOFF, quoted.destination)
        self.assertIn("NON_INSTRUCTION_CONTEXT_IGNORED", quoted.reason_codes)
        negated = route_query("Never schedule anything; explain NEE graph relationships.")
        self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, negated.destination)
        self.assertIn("NEGATED_ROUTING_SIGNAL_IGNORED", negated.reason_codes)

    def test_single_outer_quote_or_code_wrapper_preserves_the_route(self) -> None:
        cases = (
            ('"Summarize these meeting notes into decisions and actions."', QueryDestination.GENERAL_ASSISTANT_HANDOFF),
            ("'Commit the files, merge the branch, and push to origin main.'", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF),
            ("`Show governed record record-123.`", QueryDestination.GRAPH_EVIDENCE_UTILITY),
            ('"Diagnose the cross-source governed graph relationships and counterevidence."', QueryDestination.NEE_DECISION_INTELLIGENCE),
        )
        for question, destination in cases:
            with self.subTest(question=question):
                decision = route_query(question)
                self.assertEqual(destination, decision.destination)
                self.assertIn("OUTER_INSTRUCTION_WRAPPER_UNWRAPPED", decision.reason_codes)
                self.assertNotIn("NON_INSTRUCTION_CONTEXT_IGNORED", decision.reason_codes)

    def test_outer_wrapper_unwraps_once_and_preserves_other_shields(self) -> None:
        embedded = route_query('Summarize this sentence only: "commit the repository".')
        self.assertEqual(QueryDestination.GENERAL_ASSISTANT_HANDOFF, embedded.destination)
        self.assertIn("NON_INSTRUCTION_CONTEXT_IGNORED", embedded.reason_codes)
        self.assertNotIn("OUTER_INSTRUCTION_WRAPPER_UNWRAPPED", embedded.reason_codes)

        negated = route_query('"Do not upload anything; list exact governed NEE records."')
        self.assertEqual(QueryDestination.GRAPH_EVIDENCE_UTILITY, negated.destination)
        self.assertIn("OUTER_INSTRUCTION_WRAPPER_UNWRAPPED", negated.reason_codes)
        self.assertIn("NEGATED_ROUTING_SIGNAL_IGNORED", negated.reason_codes)

        delegated = route_query('Request text: "Fetch assertion A-44 from NEE."')
        self.assertIn("EXPLICIT_REQUEST_ENVELOPE_UNWRAPPED", delegated.reason_codes)
        self.assertNotIn("OUTER_INSTRUCTION_WRAPPER_UNWRAPPED", delegated.reason_codes)

        with self.assertRaisesRegex(ValueError, "outside quoted"):
            route_query('"`commit the repository`"')

    def test_outer_wrapper_scope_does_not_unwrap_ambiguous_delimiters(self) -> None:
        self.assertTrue(
            _routing_text('"Commit the files, merge the branch, and push to origin main."').outer_wrapper_unwrapped
        )
        for question in (
            'Summarize this sentence only: "commit the repository".',
            '"Commit the files, then "push" to origin main."',
            "'Don't schedule anything; summarize the meeting notes.'",
            '"Commit the files, merge the branch, and push to origin main.',
        ):
            with self.subTest(question=question):
                self.assertFalse(_routing_text(question).outer_wrapper_unwrapped)

        nested = _routing_text('"`commit the repository`"')
        self.assertTrue(nested.outer_wrapper_unwrapped)
        self.assertEqual("", nested.instruction)

        meta = route_query(
            '"Ignore the word schedule; diagnose the cross-source governed graph relationships and counterevidence."'
        )
        self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, meta.destination)
        self.assertIn("OUTER_INSTRUCTION_WRAPPER_UNWRAPPED", meta.reason_codes)

    def test_unicode_and_morphology_variants_preserve_route(self) -> None:
        cases = (
            ("Create a summary of the supplied discussion.", QueryDestination.GENERAL_ASSISTANT_HANDOFF),
            ("Scheduling the team review is the requested workflow.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF),
            ("Listing governed NEE records is the only request.", QueryDestination.GRAPH_EVIDENCE_UTILITY),
            ("Explain Digital‐Sherpa relationships across evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE),
        )
        for question, destination in cases:
            with self.subTest(question=question):
                self.assertEqual(destination, route_query(question).destination)

    def test_query_type_uses_primary_intent_not_global_keyword_precedence(self) -> None:
        cases = (
            ("Recommend actions after diagnosing risks across governed NEE relationships.", "opportunity_nba"),
            ("Diagnose risks and then recommend actions across governed NEE relationships.", "gap_risk_inconsistency"),
            ("Assess candidate promotion and recommend next steps across governed NEE evidence.", "candidate_promotion_review"),
            ("Explain why risks exist across governed NEE graph relationships.", "relationship_explanation"),
        )
        for question, query_type in cases:
            with self.subTest(question=question):
                decision = route_query(question)
                self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, decision.destination)
                self.assertEqual(query_type, decision.query_type)

    def test_remediation_lexicon_paraphrases_are_classified(self) -> None:
        cases = (
            ("Condense the meeting discussion into action items.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Polish this paragraph for an executive audience.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Edit this message for clarity and tone.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Generate workshop ideas for a customer session.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Compose a short note to the delivery team.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Create concise minutes from today's conversation.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Turn this transcript into decisions and actions.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Shorten and clarify the supplied paragraph.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Copyedit the client narrative for readability.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Outline discussion topics for a workshop.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Modify the source files to add a routing test.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Open a GitHub review for this change.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Publish the current branch to the remote.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Book thirty minutes with the engineering team.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Arrange a meeting for Monday.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Deliver this note to the team by email.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Attach the CSV to the shared file.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Revise the spreadsheet formulas.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Allocate consultants across delivery teams.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Store the report in SharePoint.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low"),
            ("Enumerate governed artifacts for Project Beacon.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Retrieve the approved NEE assertion identified as A-17.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Give me the citations behind Project Beacon status.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Export evidence references for A2A.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Return the governed fact with identifier record-900.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Count the approved NEE records.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Which sources substantiate this governed A2A fact?", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Provide a factual projection of Project Beacon status.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Fetch assertion A-44 from NEE.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Show citation C-19 for the governed graph.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low"),
            ("Advise on a target-state A2A design using related governed NEE evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "cross_workstream_synthesis", "high"),
            ("Evaluate which Project Beacon option best fits corroborating governed records.", QueryDestination.NEE_DECISION_INTELLIGENCE, "opportunity_nba", "medium"),
            ("Determine why approved and candidate NEE facts disagree.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "medium"),
            ("Choose a preferred A2A platform based on relationships among governed records.", QueryDestination.NEE_DECISION_INTELLIGENCE, "opportunity_nba", "medium"),
            ("Rank Project Beacon investments using connected graph evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "opportunity_nba", "high"),
            ("Identify the main delivery failure from cross-source governed evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "medium"),
            ("Propose the best next action from Project Beacon relationships and counterevidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "opportunity_nba", "medium"),
            ("Evaluate candidate patterns against approved NEE facts.", QueryDestination.NEE_DECISION_INTELLIGENCE, "candidate_promotion_review", "high"),
            ("Develop a client target architecture from related A2A evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "cross_workstream_synthesis", "high"),
            ("Analyze the implications of conflicting Project Beacon graph evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "medium"),
        )
        for question, destination, query_type, materiality in cases:
            with self.subTest(question=question):
                decision = route_query(question)
                self.assertEqual(destination, decision.destination)
                self.assertEqual(query_type, decision.query_type)
                self.assertEqual(materiality, decision.materiality)
                self.assertEqual(
                    destination
                    in {
                        QueryDestination.GRAPH_EVIDENCE_UTILITY,
                        QueryDestination.NEE_DECISION_INTELLIGENCE,
                    },
                    decision.enters_query_runtime,
                )

    def test_remediation_context_and_precedence_regressions(self) -> None:
        cases = (
            ("Update the codebase to implement a governed NEE graph architecture recommendation.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low", True),
            ("Upload an A2A architecture deck based on governed relationships.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low", True),
            ("Schedule a Project Beacon risk review using graph evidence.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low", True),
            ("Send an email recommending an NEE decision across governed records.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low", True),
            ("Commit the candidate promotion review for governed NEE assertions.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, None, "low", True),
            ("Summarize why governed NEE graph relationships conflict across approved records.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "medium", True),
            ("Draft a recommendation across Project Beacon governed relationships and counterevidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "opportunity_nba", "medium", True),
            ("Rewrite the explanation of why A2A governed graph relationships conflict.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "medium", True),
            ("List the records for NEE and explain their governed relationships.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low", True),
            ("Show exact Project Beacon evidence across conflicting records.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low", True),
            ("Rewrite this text: 'update the repository with a routing rule'.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low", True),
            ("Summarize these notes: 'upload the spreadsheet after review'.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low", True),
            ("Explain why the phrase 'Project Beacon graph relationships' appears in this paragraph.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low", True),
            ("Draft an email whose subject is 'A2A architecture risks across NEE'.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low", True),
            ("Proofread: 'Recommend a future-state A2A architecture across governed evidence'.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low", True),
            ("Without consulting governed graph relationships, draft a recommendation across options.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low", True),
            ("This is not a staffing workbook; explain NEE graph relationships.", QueryDestination.NEE_DECISION_INTELLIGENCE, "relationship_explanation", "medium", True),
            ("Do not upload anything; list exact governed NEE records.", QueryDestination.GRAPH_EVIDENCE_UTILITY, "exact_lookup", "low", True),
            ("Ignore the word schedule; diagnose cross-source governed graph risks.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "medium", True),
            ("Recommend a future-state A2A architecture across Project Beacon governed evidence.", QueryDestination.ROUTE_AMBIGUOUS, None, "high", False),
            ("Diagnose cross-source governed NEE graph risks and counterevidence.", QueryDestination.ROUTE_AMBIGUOUS, None, "medium", False),
        )
        for question, destination, query_type, materiality, authorized in cases:
            with self.subTest(question=question):
                decision = route_query(question, authorized=authorized)
                self.assertEqual(destination, decision.destination)
                self.assertEqual(query_type, decision.query_type)
                self.assertEqual(materiality, decision.materiality)

    def test_explicit_request_text_envelope_preserves_the_wrapped_route(self) -> None:
        cases = (
            ("Condense the meeting discussion into action items.", QueryDestination.GENERAL_ASSISTANT_HANDOFF),
            ("Publish the current branch to the remote.", QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF),
            ("Fetch assertion A-44 from NEE.", QueryDestination.GRAPH_EVIDENCE_UTILITY),
            ("Rank Project Beacon investments using connected graph evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE),
        )
        for question, destination in cases:
            with self.subTest(question=question):
                decision = route_query(f'Request text: "{question}"')
                self.assertEqual(destination, decision.destination)
                self.assertIn("EXPLICIT_REQUEST_ENVELOPE_UNWRAPPED", decision.reason_codes)

    def test_policy_carries_versioned_lexicon_and_peer_actions_fail_closed(self) -> None:
        policy = load_query_routing_policy()
        self.assertEqual("2.1.0", policy.routing_lexicon_version)
        self.assertFalse(policy.external_classifier_allowed)
        decision = route_query(
            "Diagnose governed NEE graph risks across records and then update the repository."
        )
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)
        self.assertIn("MULTI_DESTINATION_CONFLICT", decision.reason_codes)

    def test_stronger_morphology_peer_action_and_intent_regressions(self) -> None:
        cases = (
            ("Rewriting the paragraph for clarity is the requested task.", QueryDestination.GENERAL_ASSISTANT_HANDOFF, None, "low"),
            ("Assessment of candidate patterns versus approved NEE facts is required.", QueryDestination.NEE_DECISION_INTELLIGENCE, "candidate_promotion_review", "high"),
            ("Find the root-cause across governed NEE relationships.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "high"),
            ("Schedule a meeting and diagnose cross-source NEE graph risks.", QueryDestination.ROUTE_AMBIGUOUS, None, "medium"),
            ("Draft an email and recommend future-state A2A architecture across governed evidence.", QueryDestination.ROUTE_AMBIGUOUS, None, "high"),
            ("Proofread the paragraph and explain NEE graph relationships.", QueryDestination.ROUTE_AMBIGUOUS, None, "medium"),
            ("Rebuild the spreadsheet and assess candidate promotion across governed NEE facts.", QueryDestination.ROUTE_AMBIGUOUS, None, "high"),
            ("Download the report and recommend an investment across NEE evidence.", QueryDestination.ROUTE_AMBIGUOUS, None, "high"),
            ("Explain NEE graph relationships and merge the branch.", QueryDestination.ROUTE_AMBIGUOUS, None, "medium"),
            ("Recommend an NEE decision and rewrite the paragraph.", QueryDestination.ROUTE_AMBIGUOUS, None, "medium"),
            ("Compare governed NEE patterns and upload the spreadsheet.", QueryDestination.ROUTE_AMBIGUOUS, None, "medium"),
            ("Recommend an option while reviewing candidate promotion across governed NEE evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "opportunity_nba", "high"),
            ("Compare conflicting evidence across governed NEE graph relationships.", QueryDestination.NEE_DECISION_INTELLIGENCE, "cross_workstream_synthesis", "medium"),
            ("Diagnose candidate promotion conflicts across governed NEE evidence.", QueryDestination.NEE_DECISION_INTELLIGENCE, "gap_risk_inconsistency", "high"),
            ("Explain architecture implications across governed NEE relationships.", QueryDestination.NEE_DECISION_INTELLIGENCE, "relationship_explanation", "high"),
        )
        for question, destination, query_type, materiality in cases:
            with self.subTest(question=question):
                decision = route_query(question)
                self.assertEqual(destination, decision.destination)
                self.assertEqual(query_type, decision.query_type)
                self.assertEqual(materiality, decision.materiality)

    def test_decision_intelligence_adversarial_boundaries(self) -> None:
        cases = (
            (
                "Should NEE fund program A or program B?",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
            ),
            (
                "Which of these options is best for NEE?",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
            ),
            (
                "Design an NEE logo across sources.",
                QueryDestination.GENERAL_ASSISTANT_HANDOFF,
            ),
            (
                "Recommend a restaurant using NEE evidence across sources.",
                QueryDestination.GENERAL_ASSISTANT_HANDOFF,
            ),
            (
                "Analyze NEE evidence across records for grammatical style.",
                QueryDestination.GENERAL_ASSISTANT_HANDOFF,
            ),
            (
                "Restart the failed graph ingestion workflow now.",
                QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF,
            ),
            (
                "Compare NEE evidence across options and recommend which to choose.",
                QueryDestination.NEE_DECISION_INTELLIGENCE,
            ),
        )
        for question, destination in cases:
            with self.subTest(question=question):
                self.assertEqual(destination, route_query(question).destination)

    def test_system_directory_routing_and_context_shielding(self) -> None:
        from dual_pipeline.query_router import determine_query_project
        decision = route_query("LAMS can support writeback to source systems?")
        self.assertEqual(QueryDestination.GRAPH_EVIDENCE_UTILITY, decision.destination)
        self.assertEqual("exact_lookup", decision.query_type)
        ambig = route_query("Tell me about Sanskriti Lamsal.")
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, ambig.destination)
        meta = {"active_document": "output/project_lens/09-15-2026/01_live_transcript.md"}
        self.assertEqual("project_nexus", determine_query_project("LAMS writeback capabilities?", meta))
        self.assertEqual("project_lens", determine_query_project("Who attended the meeting?", meta))


if __name__ == "__main__":
    unittest.main()
