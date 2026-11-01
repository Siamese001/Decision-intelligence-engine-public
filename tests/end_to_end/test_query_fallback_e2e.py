import subprocess
import unittest

from dual_pipeline.query_core.firewall import (
    GraphDBRequiredError,
    install_subprocess_firewall,
    uninstall_subprocess_firewall,
)
from dual_pipeline.query_router import route_query
from dual_pipeline.query_router_core import QueryDestination


class QueryFallbackE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        install_subprocess_firewall()

    def tearDown(self) -> None:
        uninstall_subprocess_firewall()

    def test_e2e_query_router_graphdb_pre_flight(self) -> None:
        """Verify the router intercepts and fails if GraphDB evidence is missing,
        or succeeds in routing if the environment is valid.
        (Since this E2E runs in CI, it may have a valid test runtime or fail cleanly).
        """
        try:
            decision = route_query("What were the actions from the last meeting?")
            # If it routes successfully, it should recognize a general/decision query
            self.assertIn(
                decision.destination,
                [QueryDestination.NEE_DECISION_INTELLIGENCE, QueryDestination.GENERAL_ASSISTANT_HANDOFF, QueryDestination.GRAPH_EVIDENCE_UTILITY, QueryDestination.ROUTE_AMBIGUOUS]
            )
        except RuntimeError as e:
            self.assertIn("GraphDB unavailable", str(e))
            self.assertIn("runtime_evidence inventory check failed", str(e))

    def test_e2e_strict_shell_exec_interception(self) -> None:
        """Verify that an agent attempting to shell out to grep for historical meetings
        is strictly blocked at the subprocess level with a GraphDBRequiredError.
        """
        with self.assertRaises(GraphDBRequiredError) as cm:
            subprocess.run(["grep", "-i", "decision", "output/01_hitl_resolved_transcript.md"])

        self.assertIn("grep [direct directory]", cm.exception.matched_pattern)
        self.assertIn("output/", cm.exception.target_path)
        self.assertIn("nee ask", str(cm.exception))

        # Verify the same for rg (ripgrep)
        with self.assertRaises(GraphDBRequiredError) as cm:
            subprocess.Popen(["rg", "action items", "output/"], shell=False)

        self.assertIn("rg [protected search]", cm.exception.matched_pattern)

if __name__ == "__main__":
    unittest.main()
