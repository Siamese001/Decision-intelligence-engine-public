"""
tests/unit/test_prompt_cache_prefix_quarantine.py

Comprehensive unit test suite validating Static Prompt Prefix Quarantine for L2 Extraction:
1. Static prefix byte-for-byte immutability across different meeting runs.
2. Dynamic metadata variance (dates, attendees, titles, run IDs) does not alter quarantined prefix.
3. Prefix token length exceeds the 1,024-token provider cache threshold across Stage 01, 02, and 03.
4. Deterministic SHA-256 prefix hash parity matching telemetry gen_ai.prompt.sha256 conventions.
5. Promotion of static blocks from user content into static prefix for legacy payloads.
6. Volatile injection neutralization and quarantine into dynamic user suffix.
7. Integration with PromptCacheOptimizer and L2 Stage Executors.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dual_pipeline.prompts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
)
from dual_pipeline.token_optimization.cache_optimizer import PromptCacheOptimizer
from dual_pipeline.token_optimization.profiler import TokenProfiler
from dual_pipeline.token_optimization.prompt_cache import (
    OPENAI_MIN_CACHE_PREFIX_TOKENS,
    PromptPrefixQuarantine,
)
from dual_pipeline.transcript_stages_core.constants import _load_stage_prompt
from dual_pipeline.transcript_stages_core.graph_executor import build_stage03_user_content


class TestPromptCachePrefixQuarantine(unittest.TestCase):
    def setUp(self):
        self.profiler = TokenProfiler()
        self.quarantine = PromptPrefixQuarantine(self.profiler)
        self.optimizer = PromptCacheOptimizer(self.profiler)

    def test_static_prefix_byte_for_byte_immutability_across_different_meetings(self):
        """Verify two distinct meetings with different dates, attendees, and transcripts share identical prefix."""
        arch = MeetingArchetype.ARCHITECTURE_DEEP_DIVE
        base_prompt = _load_stage_prompt("recap", archetype=arch)
        gov_ctx = OUTPUT2_CONTRACT.compile_governance_context(arch)
        mand_structure = "<mandatory_structure>Must include: Action & Governance Ledger</mandatory_structure>"

        static_blocks = [gov_ctx, mand_structure]

        meeting_a_dynamic = [
            '<meeting_metadata>{"date": "2026-10-01", "project": "Project Alpha", "run_id": "run-001"}</meeting_metadata>',
            "<forensic_transcript>Alice [00:00:01]: Alpha discussion.</forensic_transcript>",
        ]
        meeting_b_dynamic = [
            '<meeting_metadata>{"date": "2026-10-05", "project": "Project Beta", "run_id": "run-999"}</meeting_metadata>',
            "<forensic_transcript>Bob [00:00:01]: Beta discussion with different topics.</forensic_transcript>",
        ]

        prefix_a, suffix_a, sha_a, is_cacheable_a = self.quarantine.quarantine_blocks(
            static_blocks=[base_prompt] + static_blocks,
            dynamic_blocks=meeting_a_dynamic,
        )
        prefix_b, suffix_b, sha_b, is_cacheable_b = self.quarantine.quarantine_blocks(
            static_blocks=[base_prompt] + static_blocks,
            dynamic_blocks=meeting_b_dynamic,
        )

        # Invariant 1: Byte-for-byte identical prefix
        self.assertEqual(prefix_a, prefix_b)
        # Invariant 2: Identical SHA-256 hash
        self.assertEqual(sha_a, sha_b)
        # Invariant 3: Dynamic suffixes differ
        self.assertNotEqual(suffix_a, suffix_b)
        self.assertIn("Project Alpha", suffix_a)
        self.assertIn("Project Beta", suffix_b)
        # Invariant 4: Cacheable (> 1,024 tokens)
        self.assertTrue(is_cacheable_a)
        self.assertTrue(is_cacheable_b)

    def test_prefix_token_length_exceeds_1024_across_all_three_stages(self):
        """Verify Stage 01, Stage 02, and Stage 03 static prefixes each comfortably exceed 1,024 tokens."""
        arch = MeetingArchetype.GENERAL

        # Stage 01
        p1 = _load_stage_prompt("forensic", archetype=arch)
        ctx1 = OUTPUT1_CONTRACT.compile_governance_context(arch)
        prefix1, _, _, cacheable1 = self.quarantine.quarantine_blocks([p1, ctx1], [])
        tokens1 = self.profiler.count_tokens(prefix1)
        self.assertGreater(tokens1, OPENAI_MIN_CACHE_PREFIX_TOKENS)
        self.assertTrue(cacheable1)

        # Stage 02
        p2 = _load_stage_prompt("recap", archetype=arch)
        ctx2 = OUTPUT2_CONTRACT.compile_governance_context(arch)
        prefix2, _, _, cacheable2 = self.quarantine.quarantine_blocks([p2, ctx2], [])
        tokens2 = self.profiler.count_tokens(prefix2)
        self.assertGreater(tokens2, OPENAI_MIN_CACHE_PREFIX_TOKENS)
        self.assertTrue(cacheable2)

        # Stage 03
        p3 = _load_stage_prompt("graph", archetype=arch)
        ctx3 = OUTPUT3_CONTRACT.compile_governance_context(arch)
        prefix3, _, _, cacheable3 = self.quarantine.quarantine_blocks([p3, ctx3], [])
        tokens3 = self.profiler.count_tokens(prefix3)
        self.assertGreater(tokens3, OPENAI_MIN_CACHE_PREFIX_TOKENS)
        self.assertTrue(cacheable3)

    def test_dynamic_metadata_variance_does_not_alter_prefix_hash(self):
        """Verify modifying volatile headers, dates, and attendee lists does not mutate prefix hash."""
        arch = MeetingArchetype.TACTICAL_ALIGNMENT
        base_prompt = _load_stage_prompt("recap", archetype=arch)
        gov_ctx = OUTPUT2_CONTRACT.compile_governance_context(arch)

        runs = [
            {"date": "2026-09-01", "attendees": ["Alice", "Bob"], "uuid": "trace-1111-aaaa"},
            {"date": "2026-09-15", "attendees": ["Charlie", "Dana", "Evan"], "uuid": "trace-2222-bbbb"},
            {"date": "2026-10-06", "attendees": ["Fiona"], "uuid": "trace-3333-cccc"},
        ]

        hashes = []
        for r in runs:
            meta_json = f'<meeting_metadata>{r}</meeting_metadata>'
            _, _, prefix_sha, _ = self.quarantine.quarantine_blocks(
                static_blocks=[base_prompt, gov_ctx],
                dynamic_blocks=[meta_json, "<forensic_transcript>Meeting content</forensic_transcript>"],
            )
            hashes.append(prefix_sha)

        # Invariant: All runs produce the exact same prefix SHA-256 digest
        self.assertEqual(len(set(hashes)), 1)

    def test_promotion_of_static_blocks_from_user_content(self):
        """Verify static XML blocks accidentally joined into user_content are promoted to static prefix."""
        system_prompt = "You are a synthesis agent.\n" * 60
        user_content = (
            "<governance_context><output_id>2</output_id><meeting_archetype>GENERAL</meeting_archetype></governance_context>\n\n"
            "<mandatory_structure>Must include: ## Key Discussion Points</mandatory_structure>\n\n"
            "<meeting_metadata>{\"project\": \"LENS\"}</meeting_metadata>\n\n"
            "<forensic_transcript>Dialogue</forensic_transcript>"
        )

        messages, components, prefix_sha = self.quarantine.build_quarantined_messages(
            system_prompt=system_prompt,
            user_content=user_content,
        )

        self.assertEqual(len(messages), 2)
        system_msg = messages[0]["content"]
        user_msg = messages[1]["content"]

        # Promoted blocks reside in system prefix
        self.assertIn("<governance_context>", system_msg)
        self.assertIn("<mandatory_structure>", system_msg)

        # Promoted blocks stripped from user suffix
        self.assertNotIn("<governance_context>", user_msg)
        self.assertNotIn("<mandatory_structure>", user_msg)

        # Dynamic blocks remain in user suffix
        self.assertIn("<meeting_metadata>", user_msg)
        self.assertIn("<forensic_transcript>", user_msg)

    def test_volatile_injection_neutralization_in_prefix(self):
        """Verify dynamic timestamps, execution IDs, and transcripts injected into system prompt are quarantined."""
        tainted_system = (
            "System instructions.\n" * 50
            + "Current Date/Time: 2026-10-05 14:00:00\n"
            + "Execution Run ID: run-8888-xyz\n"
            + "<meeting_metadata>{\"date\": \"10/05/2026\"}</meeting_metadata>\n"
            + "<raw_transcript>Alice: We need to ship today.</raw_transcript>\n"
            + "Final instructions."
        )
        user_content = "Please synthesize the meeting."

        messages, components, prefix_sha = self.quarantine.build_quarantined_messages(
            system_prompt=tainted_system,
            user_content=user_content,
        )

        system_msg = messages[0]["content"]
        user_msg = messages[1]["content"]

        # System message is cleaned of volatile injections
        self.assertNotIn("Current Date/Time:", system_msg)
        self.assertNotIn("Execution Run ID:", system_msg)
        self.assertNotIn("<meeting_metadata>", system_msg)
        self.assertNotIn("<raw_transcript>", system_msg)

        # Volatile injections moved to user suffix
        self.assertIn("Current Date/Time:", user_msg)
        self.assertIn("Execution Run ID:", user_msg)
        self.assertIn("<meeting_metadata>", user_msg)
        self.assertIn("<raw_transcript>", user_msg)

    def test_prompt_cache_optimizer_integration(self):
        """Verify PromptCacheOptimizer uses PromptPrefixQuarantine to generate stable prefix hashes."""
        sys_prompt = "You are an executive recap synthesizer.\n" * 200 + "Current Date/Time: 2026-10-06 10:00:00\n"
        user_prompt = "<meeting_metadata>{\"project\": \"Atlas\"}</meeting_metadata>\n<forensic_transcript>Transcript</forensic_transcript>"

        messages, comp = self.optimizer.build_cacheable_messages(sys_prompt, user_prompt)

        self.assertEqual(len(messages), 2)
        self.assertNotIn("Current Date/Time:", messages[0]["content"])
        self.assertIn("Current Date/Time:", messages[1]["content"])
        self.assertTrue(comp.is_cacheable_prefix)
        self.assertGreater(comp.estimated_prefix_tokens, OPENAI_MIN_CACHE_PREFIX_TOKENS)

    def test_stage03_build_user_content_component_separation(self):
        """Verify build_stage03_user_content return_components cleanly separates static and dynamic blocks."""
        meta = {
            "archetype": "INTERNAL_TECHNICAL_SYNC",
            "attendees": ["Alice (PartnerAdvisory)", "Bob (Client)"],
            "date": "10/06/2026",
            "project": "LENS",
        }
        static_prefix, dynamic_suffix = build_stage03_user_content(
            forensic_transcript="Alice [00:00:01]: Let us review.",
            metadata=meta,
            return_components=True,
        )

        # Static prefix contains governance and instructions
        self.assertIn("<governance_context>", static_prefix)
        self.assertIn("INSTRUCTIONS FOR ATTENDEES", static_prefix)
        self.assertNotIn("<meeting_metadata>", static_prefix)
        self.assertNotIn("<forensic_transcript>", static_prefix)

        # Dynamic suffix contains metadata and attendees roster
        self.assertIn("<meeting_metadata>", dynamic_suffix)
        self.assertIn("<meeting_attendees_roster>", dynamic_suffix)
        self.assertIn("<forensic_transcript>", dynamic_suffix)
        self.assertNotIn("<governance_context>", dynamic_suffix)


if __name__ == "__main__":
    unittest.main()
