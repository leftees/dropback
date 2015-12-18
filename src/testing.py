# -*- coding: utf-8 -*-
"""
    dropback.testing
    ~~~~~~~~~~~~~~

    Test various parts of the application framework

    :copyright: (c) 2015 by Jonathan Love.
    :license: See README.md and LICENSE for more details
"""

import unittest
from node_test import TestNFile, TestNFolder

class TestOther(unittest.TestCase):
    """Tests bits that don't belong in *_test files"""

    def test_loaded(self):
        """Dummy test to check Test is run"""
        return True

if __name__ == '__main__':
    unittest.main()
