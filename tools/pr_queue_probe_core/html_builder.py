"""
HTML document generator and template builder for layout probe variants.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple
from html import escape


def generate_variant_html(
    variant_name: str,
    row: Dict[str, Any],
    viewport_width: int,
) -> Tuple[str, int]:
    """
    Generate an HTML document modeling the specified layout variant.
    Returns (html_string, branch_column_1_indexed).
    """
    is_break_all = "break_all" in variant_name or "current" in variant_name
    branch_val = row["branch"]

    if variant_name == "codespan_branch":
        branch_cell = f"<code>{branch_val}</code>"
        branch_col_idx = 3
    elif variant_name == "nowrap_branch":
        branch_cell = f'<span style="white-space: nowrap">{branch_val}</span>'
        branch_col_idx = 3
    else:
        branch_cell = branch_val
        branch_col_idx = 3

    if "compact_7col" in variant_name or "winning_composite" in variant_name:
        branch_col_idx = 3
        # 7-column layout: Priority, Domain, Branch, Readiness, Review, Ahead, Behind
        table_html = f"""
        <table>
        <thead>
        <tr>
          <th>Priority</th><th>Domain</th><th>Branch</th><th>Readiness</th><th>Review</th><th>Ahead</th><th>Behind</th>
        </tr>
        </thead>
        <tbody>
        <tr>
          <td>{row['priority']}</td>
          <td>{row['domain']}</td>
          <td>{branch_cell}</td>
          <td>{row['readiness']}</td>
          <td>{row['review']}</td>
          <td>{row['ahead']}</td>
          <td>{row['behind']}</td>
        </tr>
        </tbody>
        </table>
        """
    elif "prose_capped" in variant_name:
        capped_action = (row["next_action"][:57] + "...") if len(row["next_action"]) > 60 else row["next_action"]
        capped_rationale = (row["rationale"][:37] + "...") if len(row["rationale"]) > 40 else row["rationale"]
        table_html = f"""
        <table>
        <thead>
        <tr>
          <th>Priority</th><th>Domain</th><th>Branch</th><th>Next Action</th><th>Ranking Rationale</th><th>Readiness</th><th>Review</th><th>Effort</th><th>Completed Waves</th><th>Open Waves</th><th>Ahead</th><th>Behind</th>
        </tr>
        </thead>
        <tbody>
        <tr>
          <td>{row['priority']}</td>
          <td>{row['domain']}</td>
          <td>{branch_cell}</td>
          <td>{capped_action}</td>
          <td>{capped_rationale}</td>
          <td>{row['readiness']}</td>
          <td>{row['review']}</td>
          <td>{row['effort']}</td>
          <td>{row['completed_waves']}</td>
          <td>{row['open_waves']}</td>
          <td>{row['ahead']}</td>
          <td>{row['behind']}</td>
        </tr>
        </tbody>
        </table>
        """
    else:
        # Standard 12-column layout
        table_html = f"""
        <table>
        <thead>
        <tr>
          <th>Priority</th><th>Domain</th><th>Branch</th><th>Next Action</th><th>Ranking Rationale</th><th>Readiness</th><th>Review</th><th>Effort</th><th>Completed Waves</th><th>Open Waves</th><th>Ahead</th><th>Behind</th>
        </tr>
        </thead>
        <tbody>
        <tr>
          <td>{row['priority']}</td>
          <td>{row['domain']}</td>
          <td>{branch_cell}</td>
          <td>{row['next_action']}</td>
          <td>{row['rationale']}</td>
          <td>{row['readiness']}</td>
          <td>{row['review']}</td>
          <td>{row['effort']}</td>
          <td>{row['completed_waves']}</td>
          <td>{row['open_waves']}</td>
          <td>{row['ahead']}</td>
          <td>{row['behind']}</td>
        </tr>
        </tbody>
        </table>
        """

    break_style = "word-break: break-all; overflow-wrap: anywhere;" if is_break_all else "word-break: normal;"
    code_style = "code { font-family: monospace; white-space: pre-wrap; }" if "codespan" in variant_name else ""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  margin: 0;
  padding: 16px;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  table-layout: auto;
}}
th, td {{
  border: 1px solid #d0d7de;
  padding: 6px 13px;
  font-size: 14px;
  {break_style}
}}
{code_style}
</style>
</head>
<body>
<div style="width: {viewport_width}px;" id="container">
{table_html}
</div>
<pre id="probe-results"></pre>
<script>
(function() {{
  const td = document.querySelector("#container tbody tr td:nth-child({branch_col_idx})");
  if (!td) {{
    document.getElementById("probe-results").textContent = JSON.stringify({{ error: "td not found" }});
    return;
  }}
  const rect = td.getBoundingClientRect();
  const textNode = td.querySelector("code, span") || td;
  const rawText = textNode.textContent.trim();

  // Measure line breaks by stepping through characters
  const range = document.createRange();
  let lines = [];
  let curLineY = null;
  let curLineText = "";

  const textTarget = textNode.firstChild || textNode;
  const len = textTarget.length || rawText.length;

  for (let i = 0; i < len; i++) {{
    try {{
      range.setStart(textTarget, i);
      range.setEnd(textTarget, i + 1);
      const r = range.getBoundingClientRect();
      if (curLineY === null || Math.abs(r.top - curLineY) > 4) {{
        if (curLineText) lines.push(curLineText);
        curLineY = r.top;
        curLineText = rawText[i] || "";
      }} else {{
        curLineText += rawText[i] || "";
      }}
    }} catch(e) {{
      curLineText += rawText[i] || "";
    }}
  }}
  if (curLineText) lines.push(curLineText);

  if (lines.length === 0) lines = [rawText];

  const minChars = Math.min(...lines.map(l => l.length));
  const maxChars = Math.max(...lines.map(l => l.length));
  const avgChars = (rawText.length / lines.length).toFixed(1);

  const res = {{
    col_width_px: Math.round(rect.width),
    line_count: lines.length,
    lines: lines,
    min_chars: minChars,
    max_chars: maxChars,
    avg_chars: parseFloat(avgChars),
    is_collapsed: minChars <= 4
  }};
  document.getElementById("probe-results").textContent = JSON.stringify(res);
}})();
</script>
</body>
</html>
"""
    return html, branch_col_idx


def generate_observer_table_html(branch: str, viewport_width: int = 800) -> str:
    """Build the exact 10-column executive observer layout for a browser qualification probe."""
    from tools.pr_governance_core.queue_observer_table import COLUMNS
    safe_branch = escape(branch, quote=False)
    head = "".join(f"<th>{name}</th>" for name in COLUMNS)
    cells = "<td>**1**</td><td>" + safe_branch + "</td><td>Enhancement</td><td>XS</td><td>Y</td><td>N</td><td>Y</td><td>N</td><td>Promote to Main</td><td>Run review</td>"
    return f"""<!doctype html><meta charset="utf-8"><style>
#viewport {{ width:{viewport_width}px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; table-layout:auto; }}
th,td {{ border:1px solid #999; padding:6px; white-space:normal; }}
</style><div id="viewport"><table><thead><tr>{head}</tr></thead><tbody><tr>{cells}</tr></tbody></table></div>"""

