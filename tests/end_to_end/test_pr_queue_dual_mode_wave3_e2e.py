"""Wave 3 output parity and table integrity checks."""
from tools.pr_governance_core.render import render_pr_queue_observer_table


def test_render_api_returns_only_the_ordered_nine_column_table():
    row = {"branch": "feat/complete-name", "sub_rank": "P1.1", "next_action": "Review"}
    output = render_pr_queue_observer_table([row], {"mode": "scheduled", "outcome": "COMPLETE"})
    lines = output.splitlines()
    assert lines[0] == "| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |"
    assert all(line.startswith("|") and line.endswith("|") for line in lines)
    assert len(lines) == 3

