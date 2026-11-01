"""
AST-based 3-way merge resolver for Python constant Set definitions.

Resolves non-colliding append conflicts on Python Set definitions (e.g. Set[str]).
"""

from __future__ import annotations

import ast
import re
from typing import Dict, Optional, Set, Tuple


def extract_set_literals(code: str) -> Dict[str, Set[str]]:
    """Extracts top-level Set[str] definitions from Python source code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}

    sets: Dict[str, Set[str]] = {}
    for node in tree.body:
        target_name: Optional[str] = None
        set_node: Optional[ast.Set] = None

        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target_name = node.targets[0].id
            if isinstance(node.value, ast.Set):
                set_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            if isinstance(node.value, ast.Set):
                set_node = node.value

        if target_name and set_node:
            elements: Set[str] = set()
            for elt in set_node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    elements.add(elt.value)
            sets[target_name] = elements

    return sets


def merge_python_ast_sets(
    ancestor_text: str,
    ours_text: str,
    theirs_text: str,
) -> Tuple[bool, str]:
    """Performs 3-way AST merge on Python files containing set literals.

    Computes 3-way union of set elements:
    merged = base + (ours - base) + (theirs - base) - (removals)
    """
    # Fail-closed if any non-empty input has invalid Python syntax
    try:
        if ancestor_text.strip():
            ast.parse(ancestor_text)
        if ours_text.strip():
            ast.parse(ours_text)
        if theirs_text.strip():
            ast.parse(theirs_text)
    except SyntaxError:
        return False, ""

    ancestor_sets = extract_set_literals(ancestor_text)
    ours_sets = extract_set_literals(ours_text)
    theirs_sets = extract_set_literals(theirs_text)

    # All sets found in ours and theirs
    all_var_names = set(ours_sets.keys()).union(theirs_sets.keys())
    if not all_var_names:
        return False, ""

    result_text = ours_text
    changed = False

    for var_name in all_var_names:
        base_set = ancestor_sets.get(var_name, set())
        ours_set = ours_sets.get(var_name, set())
        theirs_set = theirs_sets.get(var_name, set())
        theirs_added = theirs_set - base_set

        # If theirs didn't add anything to this set, ours is already fine
        if not theirs_added:
            continue

        merged_elements = sorted(list(ours_set.union(theirs_added)))

        # Find the set definition in result_text and replace its elements
        pattern = rf"({var_name}\s*(?::\s*[^=]+)?=\s*\{{)([\s\S]*?)(\}})"
        match = re.search(pattern, result_text)
        if match:
            # Format merged set elements cleanly
            formatted_elts = "\n" + "\n".join(f'    "{elt}",' for elt in merged_elements) + "\n"
            replacement = f"{match.group(1)}{formatted_elts}{match.group(3)}"
            result_text = result_text[: match.start()] + replacement + result_text[match.end() :]
            changed = True

    if not changed:
        return False, ""

    # Verify that the generated code compiles cleanly
    try:
        ast.parse(result_text)
        return True, result_text
    except SyntaxError:
        return False, ""
