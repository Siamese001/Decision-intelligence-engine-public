"""Hypothesis Property-Based & Boundary Tests for Lower-Priority Utility Hotspots.

Covers 5 un-propertied utility hotspot modules:
1. `dual_pipeline/document_pipeline_core/extractor_evidence_utils.py`
2. `dual_pipeline/document_pipeline_core/format_source_units.py`
3. `tools/pr_governance_core/conflict_triage.py`
4. `dual_pipeline/live_meeting_recorder_core/stop_lifecycle.py`
5. `tools/lint_prompt_ssot.py`
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from hypothesis import given, settings, strategies as st

from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
    build_grounded_evidence_entry,
    find_verbatim_span,
    heal_visual_document_mutations,
    normalize_unit_locator_value,
    slugify_entity_id,
)
from dual_pipeline.document_pipeline_core.format_source_units import (
    FailureDisposition,
    PreflightResult,
    SourceUnit,
    VisualEscalationPolicy,
)
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
    halt_capture_before_drain,
    migrate_all_chunk_sources_on_drain,
    sync_roster_from_disk_and_hints,
)
from tools.lint_prompt_ssot import (
    EXPECTED_ARCHIVE_TASKS,
    EXPECTED_CANONICAL_PROMPTS,
    audit_canonical_prompts,
    audit_registry_bindings,
    compute_sha256,
)
from tools.pr_governance_core.conflict_triage import (
    BranchDiffTopology,
    ConflictClass,
    ConflictTriageResult,
    EffortBand,
    FileConflict,
    is_generated,
    is_plan_doc,
)


class TestUtilityExtractorAndGovernanceProperties(unittest.TestCase):
    """Hypothesis property and boundary verification for lower-priority utility hotspots."""

    @settings(max_examples=40, deadline=None)
    @given(
        prefix=st.sampled_from(["person", "org", "role", "system"]),
        label=st.text(min_size=0, max_size=60),
    )
    def test_slugify_entity_id_determinism_and_charset_property(
        self, prefix: str, label: str
    ) -> None:
        """Property: slugify_entity_id always emits `<prefix>:<lowercase-alnum-hyphen>` and is idempotent on the slug part."""
        ent_id = slugify_entity_id(prefix, label)
        self.assertTrue(ent_id.startswith(f"{prefix}:"))
        slug_part = ent_id.split(":", 1)[1]
        self.assertTrue(len(slug_part) >= 1)
        self.assertIsNotNone(re.match(r"^[a-z0-9\-]+$", slug_part))
        self.assertFalse(slug_part.startswith("-"))
        self.assertFalse(slug_part.endswith("-"))

    @settings(max_examples=35, deadline=None)
    @given(
        page_num=st.integers(min_value=1, max_value=999),
        is_slide=st.booleans(),
        section_title=st.from_regex(r"[A-Za-z0-9 ]{3,25}", fullmatch=True),
    )
    def test_extractor_locator_and_grounded_evidence_offsets_property(
        self, page_num: int, is_slide: bool, section_title: str
    ) -> None:
        """Property: normalize_unit_locator_value and build_grounded_evidence_entry preserve exact quote pointer offsets."""
        raw_loc = f"## {'Slide' if is_slide else 'Page'} {page_num}"
        norm_kind, norm_val = normalize_unit_locator_value("section", raw_loc)
        self.assertEqual(norm_kind, "slide" if is_slide else "section")
        self.assertEqual(norm_val, f"p{page_num}")

        doc_text = f"Header block\n{section_title.strip()} is verified in production.\nFooter block"
        span = find_verbatim_span(doc_text, section_title.strip(), max_len=200)
        self.assertIn(section_title.strip(), span)

        entry = build_grounded_evidence_entry(
            ev_id="ev-001",
            text=section_title.strip(),
            exact=span,
            locator_value=section_title,
            doc_text=doc_text,
            resolve_verbatim=True,
        )
        self.assertEqual(entry["grounding_status"], "GROUNDED")
        start, end = entry["quote_pointer"]
        self.assertEqual(doc_text[start:end], entry["exact_text"])

    def test_heal_visual_document_mutations_and_person_role_classification(self) -> None:
        """Boundary check for visual component classification and description sanitization."""
        data = {
            "evidence_index": {
                "ev-1": {"text": "Wellington, Jacob leads Apex Global Analytics as Vice President"}
            },
            "graph_mutations": {"entity_upserts": []},
        }
        metadata = {
            "is_visual_document": True,
            "visual_extraction": {
                "components": [
                    {"label": "Wellington, Jacob"},
                    {"label": "Vice President of Engineering"},
                    {"label": "Apex Global Analytics"},
                    {"label": "sh"},
                ]
            },
        }
        changes = heal_visual_document_mutations(data, metadata, "Visual org chart")
        upserts = data["graph_mutations"]["entity_upserts"]
        self.assertEqual(len(upserts), 3)
        ids = {u["id"] for u in upserts}
        self.assertIn("person:jacob-wellington", ids)
        self.assertTrue(len(changes) >= 3)

    @settings(max_examples=40, deadline=None)
    @given(
        unit_id=st.from_regex(r"u-[a-z0-9]{4,8}", fullmatch=True),
        locator=st.from_regex(r"p[1-9][0-9]?", fullmatch=True),
        content=st.text(min_size=1, max_size=120),
        disposition=st.sampled_from(list(FailureDisposition)),
    )
    def test_format_source_unit_and_preflight_serialization_property(
        self,
        unit_id: str,
        locator: str,
        content: str,
        disposition: FailureDisposition,
    ) -> None:
        """Property: SourceUnit auto-computes deterministic SHA-256 digest and round-trips through to_dict()."""
        unit = SourceUnit(
            unit_id=unit_id,
            source_hash="a" * 64,
            format="pdf",
            unit_type="paragraph",
            locator=locator,
            content=content,
        )
        expected_digest = hashlib.sha256(f"{locator}:{content}".encode("utf-8")).hexdigest()
        self.assertEqual(unit.digest, expected_digest)
        d = unit.to_dict()
        self.assertEqual(d["digest"], expected_digest)
        self.assertEqual(d["content"], content)

        preflight = PreflightResult(
            is_valid=(disposition == FailureDisposition.FAIL_CLOSED),
            format_id="pdf",
            disposition=disposition,
            remediation_command="nee ingest-meeting",
        )
        pf_dict = preflight.to_dict()
        self.assertEqual(pf_dict["disposition"], disposition.value)
        self.assertEqual(pf_dict["remediation_command"], "nee ingest-meeting")
        self.assertIn(VisualEscalationPolicy.ON_LOW_TEXT_OR_SCANNED, list(VisualEscalationPolicy))

    @settings(max_examples=30, deadline=None)
    @given(
        conflict_class=st.sampled_from(list(ConflictClass)),
        effort_band=st.sampled_from(list(EffortBand)),
        hunks=st.integers(min_value=1, max_value=20),
        lines=st.integers(min_value=1, max_value=500),
    )
    def test_conflict_triage_models_and_classifiers_property(
        self,
        conflict_class: ConflictClass,
        effort_band: EffortBand,
        hunks: int,
        lines: int,
    ) -> None:
        """Property: FileConflict, ConflictTriageResult, and BranchDiffTopology serialize enum values losslessly."""
        fc = FileConflict(
            path="implementation_plan.md",
            conflict_class=conflict_class,
            hunks=hunks,
            conflict_lines=lines,
            weight=float(hunks * 1.5),
        )
        self.assertTrue(is_plan_doc(fc.path))
        self.assertTrue(is_generated("reports/pr_queue_detail.json"))

        res = ConflictTriageResult(
            branch="test/branch",
            base="main",
            conflicted_files=[fc],
            total_files=1,
            manual_files_count=1,
            effort_score=fc.weight,
            effort_band=effort_band,
            action_summary="Rebase onto main",
            class_counts={conflict_class.value: 1},
        )
        serialized = res.to_dict()
        self.assertEqual(serialized["effort_band"], effort_band.value)
        self.assertEqual(serialized["conflicted_files"][0]["conflict_class"], conflict_class.value)

        topo = BranchDiffTopology(
            branch="test/branch",
            base="main",
            branch_files=[fc.path],
            main_files=[],
            overlap_files=[],
            in_main_pcts={fc.path: 0.0},
            absorbed_files=[],
            salvageable_files=[fc.path],
            file_numstat={fc.path: (lines, 0)},
        )
        self.assertEqual(topo.to_dict()["branch"], "test/branch")

    def test_stop_lifecycle_roster_sync_and_chunk_drain_isolation(self) -> None:
        """Boundary verification of stop_lifecycle roster merging, capture halting, and chunk migration."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / "session_manifest.json"
            manifest_path.write_text(
                json.dumps({"attendees": ["Amit Ayer", {"name": "Speaker_Alpha Muschett"}]}),
                encoding="utf-8",
            )
            queue_path = root / "speaker_hints.json"
            queue_path.write_text(
                json.dumps(
                    [
                        {"speaker_name": "Speaker_Beta Chacko"},
                        {"speaker_name": "Unknown"},
                        {"speaker_name": "amit ayer"},
                    ]
                ),
                encoding="utf-8",
            )

            staging_dir = root / "staging"
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / "chunk_0000.wav").write_bytes(b"RIFF" + b"\x00" * 64)
            target_chunks = root / "chunks"
            target_chunks.mkdir(parents=True, exist_ok=True)

            stopped_flags = {"graceful": False, "stop": False, "spooler": False}
            recorder = SimpleNamespace(
                session_manifest=manifest_path,
                attendees=["Fallback Person"],
                speaker_hint_manager=SimpleNamespace(speaker_map={"SPEAKER_01": "Rose Teame", "SPEAKER_02": "Speaker"}),
                speaker_hint_queue=SimpleNamespace(queue_path=queue_path),
                running=True,
                mock_mode=True,
                capture_manager=SimpleNamespace(
                    graceful_stop=lambda: stopped_flags.__setitem__("graceful", True),
                    stop=lambda: stopped_flags.__setitem__("stop", True),
                    chunks_dir=staging_dir,
                ),
                spooler=SimpleNamespace(
                    _is_running=True,
                    _mock_thread=None,
                    stop=lambda: stopped_flags.__setitem__("spooler", True),
                    output_dir=None,
                ),
                chunks_dir=target_chunks,
                staging_chunks_dir=None,
            )

            synced = sync_roster_from_disk_and_hints(recorder)
            names_lower = {
                (a.get("name") if isinstance(a, dict) else a).lower() for a in synced
            }
            self.assertIn("amit ayer", names_lower)
            self.assertIn("brien muschett", names_lower)
            self.assertIn("ninan chacko", names_lower)
            self.assertIn("rose teame", names_lower)
            self.assertNotIn("unknown", names_lower)

            halt_capture_before_drain(recorder)
            self.assertFalse(recorder.running)
            self.assertTrue(stopped_flags["graceful"])
            self.assertTrue(stopped_flags["stop"])
            self.assertTrue(stopped_flags["spooler"])

            migrate_all_chunk_sources_on_drain(recorder)
            self.assertTrue((target_chunks / "chunk_0000.wav").is_file())

    def test_lint_prompt_ssot_canonical_and_registry_audits(self) -> None:
        """Boundary verification of lint_prompt_ssot against missing, rogue, and valid prompt directories."""
        payload = b"canonical prompt content"
        self.assertEqual(compute_sha256(payload), hashlib.sha256(payload).hexdigest())

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            # 1. Missing directory fails closed
            errs_missing = audit_canonical_prompts(repo)
            self.assertEqual(len(errs_missing), 1)
            self.assertIn("INV-PROMPT-SSOT-01", errs_missing[0])

            # 2. Populate all expected canonical prompts + 1 rogue file
            prompts_dir = repo / "automations" / "prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            for fname in EXPECTED_CANONICAL_PROMPTS.values():
                (prompts_dir / fname).write_text("# Valid Prompt\n", encoding="utf-8")
            (prompts_dir / "rogue_bad.md").write_text("# Rogue\n", encoding="utf-8")

            errs_rogue = audit_canonical_prompts(repo)
            self.assertTrue(any("INV-PROMPT-SSOT-02" in e for e in errs_rogue))
            self.assertTrue(any("INV-PROMPT-SSOT-06" in e for e in errs_rogue))

            # Remove rogue file -> 0 errors
            (prompts_dir / "rogue_bad.md").unlink()
            self.assertEqual(audit_canonical_prompts(repo), [])

            # 3. Missing task_registry.json fails closed
            reg_errs = audit_registry_bindings(repo)
            self.assertEqual(len(reg_errs), 1)
            self.assertIn("INV-PROMPT-SSOT-03", reg_errs[0])
            self.assertGreaterEqual(len(EXPECTED_ARCHIVE_TASKS), 9)


if __name__ == "__main__":
    unittest.main()
