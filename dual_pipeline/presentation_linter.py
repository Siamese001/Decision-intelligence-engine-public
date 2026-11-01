"""
dual_pipeline/presentation_linter.py

Programmatic presentation linter that scans generated decision tables,
BCG-style capability matrices, and executive summaries to detect and reject
compound workstream headers or attribution bleed before output emission.
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

VIO_ATTRIBUTION_SCOPE_BLEED = "VIO_ATTRIBUTION_SCOPE_BLEED"


class PresentationLintError(ValueError):
    """Raised when presentation text violates attribution boundaries or contains compound headers."""
    pass


@dataclass(frozen=True)
class LintViolation:
    rule_name: str
    line_number: int
    matched_text: str
    message: str
    required_correction: str

    def format_diagnostic(self) -> str:
        return (
            f"Line {self.line_number} [{self.rule_name}]: {self.message}\n"
            f"  Matched text: '{self.matched_text}'\n"
            f"  Required correction: '{self.required_correction}'"
        )


class PresentationLinter:
    """Lints Markdown documents, tables, and recaps for workstream conflation and attribution bleed."""

    COMPOUND_HEADER_REGEX = re.compile(
        r"\|\s*[^|]*(?:LENS\s*[/&]\s*Sherpa|Sherpa\s*[/&]\s*LENS)[^|]*\|",
        re.IGNORECASE,
    )

    SHERPA_SECTION_REGEX = re.compile(
        r"^(?:#{1,4}\s+|\|\s*)(?:.*Digital\s+Sherpa.*|.*Sherpa.*pod.*|.*Sherpa.*team.*)",
        re.IGNORECASE,
    )

    JEN_PAN_MENTION_REGEX = re.compile(
        r"\bJen\s+Pan\b",
        re.IGNORECASE,
    )

    def _check_compound_header(self, line: str, line_num: int) -> Optional[LintViolation]:
        match = self.COMPOUND_HEADER_REGEX.search(line)
        if not match:
            return None
        matched_header = match.group(0).strip("|").strip()
        return LintViolation(
            rule_name="RuleCompoundWorkstreamHeader",
            line_number=line_num,
            matched_text=matched_header,
            message=f"{VIO_ATTRIBUTION_SCOPE_BLEED}: Column header conflates Project LENS and Sherpa.",
            required_correction="Observed Field Reality (Project LENS — Jen Pan)",
        )

    def _check_attribution_bleed(
        self, line: str, line_num: int, in_sherpa_scope: bool, start_line: int
    ) -> Optional[LintViolation]:
        if not (in_sherpa_scope and self.JEN_PAN_MENTION_REGEX.search(line)):
            return None
        return LintViolation(
            rule_name="RuleAttributionBleed",
            line_number=line_num,
            matched_text=line.strip(),
            message=f"{VIO_ATTRIBUTION_SCOPE_BLEED}: Jen Pan referenced in Sherpa scope (started line {start_line}).",
            required_correction="Jen Pan was exclusively on Project LENS. Zero presence or role on Project Beacon.",
        )

    def _check_table_row_bleed(self, line: str, line_num: int) -> Optional[LintViolation]:
        if not (line.strip().startswith("|") and self.JEN_PAN_MENTION_REGEX.search(line)):
            return None
        cells = [c.strip().lower() for c in line.split("|")]
        if not any("sherpa" in c and "lens" not in c for c in cells):
            return None
        return LintViolation(
            rule_name="RuleAttributionBleed",
            line_number=line_num,
            matched_text=line.strip(),
            message=f"{VIO_ATTRIBUTION_SCOPE_BLEED}: Jen Pan co-located in Sherpa table row without LENS distinction.",
            required_correction="Partition row into Project LENS scope (Jen Pan) vs Project Beacon scope.",
        )

    def lint_text(self, text: str) -> List[LintViolation]:
        violations: List[LintViolation] = []
        current_sherpa_scope = False
        sherpa_scope_start_line = 0

        for line_num, line in enumerate(text.splitlines(), start=1):
            h_v = self._check_compound_header(line, line_num)
            if h_v:
                violations.append(h_v)

            if line.startswith("#"):
                is_sherpa = bool(re.search(r"\b(?:digital\s+sherpa|sherpa\s+pod|sherpa\s+team)\b", line, re.IGNORECASE))
                current_sherpa_scope = is_sherpa
                sherpa_scope_start_line = line_num if is_sherpa else 0

            b_v = self._check_attribution_bleed(line, line_num, current_sherpa_scope, sherpa_scope_start_line)
            if b_v:
                violations.append(b_v)

            t_v = self._check_table_row_bleed(line, line_num)
            if t_v:
                violations.append(t_v)

        return violations

    def lint_file(self, file_path: Union[str, Path]) -> List[LintViolation]:
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        return self.lint_text(p.read_text(encoding="utf-8"))

    def assert_clean_presentation(self, text: str) -> None:
        violations = self.lint_text(text)
        if violations:
            diagnostics = "\n".join(v.format_diagnostic() for v in violations)
            raise PresentationLintError(
                f"Presentation linter failed with {len(violations)} violation(s):\n{diagnostics}"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    linter = PresentationLinter()

    if not args or args[0] == "-":
        if sys.stdin.isatty():
            print("Error: Stdin is a terminal; provide a file path or pipe text.", file=sys.stderr)
            return 1
        import select
        try:
            r, _, _ = select.select([sys.stdin], [], [], 5.0)
            content = sys.stdin.read() if r else ""
        except (OSError, ValueError):
            content = ""
    else:
        path = Path(args[0])
        if not path.is_file():
            print(f"Error: file not found: {args[0]}", file=sys.stderr)
            return 1
        content = path.read_text(encoding="utf-8")

    violations = linter.lint_text(content)
    if violations:
        print(f"FAILED: {len(violations)} presentation violation(s) found:", file=sys.stderr)
        for v in violations:
            print(v.format_diagnostic(), file=sys.stderr)
        return 1

    print("Presentation linter: PASS (0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
