import subprocess
import unittest

from dual_pipeline.query_core.firewall import (
    GraphDBRequiredError,
    install_subprocess_firewall,
    uninstall_subprocess_firewall,
)


class TestQueryFirewall(unittest.TestCase):
    def setUp(self) -> None:
        install_subprocess_firewall()

    def tearDown(self) -> None:
        uninstall_subprocess_firewall()

    def test_subprocess_run_intercepts_grep_on_output(self) -> None:
        with self.assertRaises(GraphDBRequiredError) as cm:
            subprocess.run(["grep", "-r", "some query", "output/"])

        self.assertIn("grep [recursive]", cm.exception.matched_pattern)
        self.assertIn("output/", cm.exception.target_path)
        self.assertIn("nee ask", str(cm.exception))

    def test_subprocess_popen_intercepts_rg_on_root(self) -> None:
        with self.assertRaises(GraphDBRequiredError) as cm:
            subprocess.Popen("rg 'some text'", shell=True)

        self.assertIn("rg [unscoped]", cm.exception.matched_pattern)
        self.assertIn(".", cm.exception.target_path)

    def test_subprocess_run_allows_safe_commands(self) -> None:
        # Should not raise
        try:
            res = subprocess.run(["echo", "hello"], capture_output=True)
            self.assertEqual(res.returncode, 0)
        except GraphDBRequiredError:
            self.fail("GraphDBRequiredError raised unexpectedly on safe command")

if __name__ == "__main__":
    unittest.main()
