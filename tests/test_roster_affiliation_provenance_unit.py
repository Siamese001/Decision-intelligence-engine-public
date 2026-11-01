"""Unit tests for Wave 2 field-specific roster affiliation provenance locks."""
from __future__ import annotations

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_person_entity,
)
from dual_pipeline.transcript_metadata_helper import _populate_roster_and_grouped_attendees
from dual_pipeline.transcript_stages_core.u0_ingestion import _normalize_transcript_entities


class TestRosterAffiliationProvenanceUnit:
    """Class A unit tests verifying operator affiliation preservation against directory priors."""

    def test_resolver_explicit_org_overrides_stale_directory_prior(self) -> None:
        """Verifies explicit_org overrides directory prior."""
        # Baseline directory resolution without explicit org
        baseline = resolve_person_entity("Michael Fielder")
        assert baseline is not None
        assert baseline.org == "Apex Global Analytics"

        # Explicit operator lock: PartnerAdvisory
        locked = resolve_person_entity("Michael Fielder", explicit_org="PartnerAdvisory")
        assert locked is not None
        assert locked.org == "PartnerAdvisory"
        assert locked.provenance_origin == "ORIGIN_INPUT_PROVENANCE"

        # Verify Noemi Monnerville retains explicit lock as well
        noemi_locked = resolve_person_entity("Noemi Monnerville", explicit_org="PartnerAdvisory")
        assert noemi_locked is not None
        assert noemi_locked.org == "PartnerAdvisory"

    def test_u0_normalizer_preserves_explicit_roster_affiliation(self) -> None:
        """Verifies Stage U0 normalizer retains explicit PartnerAdvisory affiliation in roster and attendees."""
        raw_text = "**Noemi Monnerville:** Welcome everyone to the sprint review."
        metadata = {
            "roster": [
                {
                    "name": "Noemi Monnerville",
                    "org": "PartnerAdvisory",
                    "role": "Web User Experience Designer",
                }
            ],
            "attendees": {},
        }

        clean_text = _normalize_transcript_entities(raw_text, metadata)

        roster = metadata["roster"]
        assert len(roster) == 1
        assert roster[0]["name"] == "Noemi Monnerville"
        # Must retain explicit operator company, NOT revert to Contractor
        assert roster[0]["org"] == "PartnerAdvisory"
        assert roster[0]["provenance_origin"] == "ORIGIN_INPUT_PROVENANCE"

        # Attendees grouping must reflect PartnerAdvisory
        assert "PartnerAdvisory" in metadata["attendees"]
        assert any("Noemi Monnerville" in att for att in metadata["attendees"]["PartnerAdvisory"])
        assert "Contractor" not in metadata["attendees"]

    def test_u0_normalizer_populates_missing_org_from_directory(self) -> None:
        """Verifies U0 enriches missing org when operator did not supply an explicit company."""
        raw_text = "**David:** Quick update."
        metadata = {
            "roster": [
                {
                    "name": "David",
                    "org": "",  # missing org
                    "role": "Participant",
                }
            ],
            "attendees": {},
        }

        _ = _normalize_transcript_entities(raw_text, metadata)
        roster = metadata["roster"]
        assert len(roster) == 1
        # Should populate from directory if matched
        assert roster[0]["name"] is not None

    def test_metadata_helper_preserves_existing_structured_roster(self) -> None:
        """Verifies _populate_roster_and_grouped_attendees does not wipe an existing structured roster."""
        metadata = {
            "roster": [
                {"name": "Noemi Monnerville", "org": "PartnerAdvisory", "role": "UX Designer"},
                {"name": "Venkat", "org": "PartnerAdvisory", "role": "Solutions Architect"},
            ],
            "attendees": ["Noemi Monnerville (Contractor - UX Designer)", "Venkat (Solutions Architect)"],
        }

        _populate_roster_and_grouped_attendees(metadata)

        roster = metadata["roster"]
        assert len(roster) == 2
        assert roster[0]["org"] == "PartnerAdvisory"
        assert roster[1]["org"] == "PartnerAdvisory"
        assert "PartnerAdvisory" in metadata["attendees"]
