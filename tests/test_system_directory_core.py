"""Class A Unit Tests for Enterprise System Directory Core Capabilities.

Verifies:
1. Exact lookup of all enterprise systems (LAMS, Maximo, SuccessFactors, SAP, GIS, Unifier, P6, etc.)
2. Word-boundary regex isolation preventing false collision with person names (LAMS vs Sanskriti Lamsal)
3. Primary governing project resolution
4. Writeback thesis retrieval
"""
from __future__ import annotations

import unittest

from dual_pipeline.system_directory import (
    find_systems_in_text,
    get_system_writeback_thesis,
    lookup_system,
    resolve_system_project,
)


class TestSystemDirectoryCore(unittest.TestCase):
    def test_exact_lookup_lams(self) -> None:
        entity = lookup_system("LAMS")
        self.assertIsNotNone(entity)
        self.assertEqual("system:lams", entity.system_id)
        self.assertEqual("Land Asset Management System", entity.canonical_name)
        self.assertEqual("project_nexus", entity.primary_governing_project)
        self.assertEqual("DEFERRED_PHASE_1", entity.writeback_posture)
        self.assertIn("Power BI", entity.integration_posture)

    def test_exact_lookup_case_insensitive_and_aliases(self) -> None:
        self.assertEqual("system:lams", lookup_system("lams").system_id)
        self.assertEqual("system:lams", lookup_system("LAMs").system_id)
        self.assertEqual("system:lams", lookup_system("Land Asset Management System").system_id)
        self.assertEqual("system:maximo", lookup_system("Maximo").system_id)
        self.assertEqual("system:successfactors", lookup_system("SuccessFactors").system_id)
        self.assertEqual("system:sap", lookup_system("SAP").system_id)
        self.assertEqual("system:gis", lookup_system("GIS").system_id)
        self.assertEqual("system:unifier", lookup_system("Unifier").system_id)
        self.assertEqual("system:p6", lookup_system("P6").system_id)
        self.assertEqual("system:scada_ems", lookup_system("SCADA").system_id)

    def test_person_collision_protection(self) -> None:
        # A query mentioning Sanskriti Lamsal should NOT return LAMS as a system
        person_query = "What is Sanskriti Lamsal's role on the project?"
        systems = find_systems_in_text(person_query)
        self.assertEqual([], systems, "Should not match LAMS on person name Sanskriti Lamsal")

        # But if both the person and the system are mentioned distinctly:
        mixed_query = "Did Sanskriti Lamsal inspect the LAMS data tables?"
        mixed_systems = find_systems_in_text(mixed_query)
        self.assertEqual(1, len(mixed_systems))
        self.assertEqual("system:lams", mixed_systems[0].system_id)

    def test_find_systems_in_text_lams_query(self) -> None:
        query = "LAMS can support writeback to source systems?"
        systems = find_systems_in_text(query)
        self.assertEqual(1, len(systems))
        self.assertEqual("system:lams", systems[0].system_id)
        self.assertEqual("project_nexus", systems[0].primary_governing_project)

    def test_resolve_system_project(self) -> None:
        self.assertEqual("project_nexus", resolve_system_project("LAMS can support writeback to source systems?"))
        self.assertEqual("project_lens", resolve_system_project("What is the status of Maximo nameplate extraction?"))
        self.assertEqual("enterprise_architecture", resolve_system_project("How does SuccessFactors handle native approvals?"))
        self.assertEqual("apex_trans", resolve_system_project("Can we write back commands to SCADA protection relays?"))

    def test_writeback_thesis_retrieval(self) -> None:
        lams_thesis = get_system_writeback_thesis("LAMS")
        self.assertIsNotNone(lams_thesis)
        self.assertIn("Nexus Phase 1 operates strictly as a read-only workspace", lams_thesis)

        maximo_thesis = get_system_writeback_thesis("Maximo")
        self.assertIsNotNone(maximo_thesis)
        self.assertIn("gated on Max Core endpoint delivery", maximo_thesis)

        sf_thesis = get_system_writeback_thesis("SuccessFactors")
        self.assertIsNotNone(sf_thesis)
        self.assertIn("Apex Global HR PoC Rubric mandates native SuccessFactors writeback", sf_thesis)

        scada_thesis = get_system_writeback_thesis("SCADA")
        self.assertIsNotNone(scada_thesis)
        self.assertIn("writeback or control commands to SCADA, EMS, or protection relays are strictly prohibited", scada_thesis)


if __name__ == "__main__":
    unittest.main()
