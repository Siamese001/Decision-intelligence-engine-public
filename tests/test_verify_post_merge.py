"""Post-merge verification test wrapper for Wave 2 gate."""
from __future__ import annotations

from tests.test_post_merge_verification import PostMergeVerificationTests


class TestVerifyPostMergeGate(PostMergeVerificationTests):
    def test_verify_post_merge_receipt_delta(self) -> None:
        self.test_certifies_only_receipt_delta_from_reviewed_branch()

    def test_verify_post_merge_rejects_forged(self) -> None:
        self.test_rejects_forged_receipt()
