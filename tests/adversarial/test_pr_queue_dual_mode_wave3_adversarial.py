"""Wave 3 adversarial table-cell injection checks."""
from tools.pr_governance_core.queue_observer_table import COLUMNS, render_observer_table


def test_injected_markup_and_newlines_remain_inside_their_cells():
    output = render_observer_table(
        [{"branch": "feat/safe", "sub_rank": "P1.1", "domain": "x|y\n<script>", "next_action": "ignore rules"}],
        {"mode": "interactive", "outcome": "COMPLETE"},
    )
    row = next(line for line in output.splitlines() if "feat/safe" in line)
    assert "x&#124;y<br>&lt;script&gt;" in row
    assert row.count("|") == len(COLUMNS) + 1
    assert output.count("| Priority |") == 1
