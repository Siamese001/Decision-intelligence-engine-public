import unittest

from dual_pipeline.exceptions import ProviderAccessViolation
from dual_pipeline.l2_providers_core.sentinel import (
    authorize_provider_instantiation,
    require_l2d_authorization,
    reset_l2d_authorization,
)


class TestSentinel(unittest.TestCase):
    def setUp(self):
        reset_l2d_authorization()

    def tearDown(self):
        authorize_provider_instantiation()

    def test_sentinel_blocks(self):
        with self.assertRaises(ProviderAccessViolation):
            require_l2d_authorization()

    def test_sentinel_allows(self):
        authorize_provider_instantiation()
        require_l2d_authorization() # should not raise
