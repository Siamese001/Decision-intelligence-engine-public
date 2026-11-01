"""
Adversarial tests for PR Governance GC and branch retirement.
"""

from __future__ import annotations

import pytest

from tools.retire_superseded_branch import retire_branch


def test_adversarial_retire_nonexistent_branch():
    with pytest.raises(ValueError, match="does not exist"):
        retire_branch("nonexistent_branch_xyz_12345", dry_run=True)


def test_adversarial_retire_empty_branch_name():
    with pytest.raises(ValueError):
        retire_branch("", dry_run=True)
