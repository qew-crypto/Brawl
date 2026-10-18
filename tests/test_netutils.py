import unittest
from netutils import checked_ipv4

class NetworkTests(unittest.TestCase):
    def test_ipv4(self):
        self.assertEqual(checked_ipv4('203.0.113.15\n'),'203.0.113.15')

    def test_reject_non_ipv4(self):
        for value in ('::1','<html>blocked</html>','example.org','999.1.1.1',''):
            with self.subTest(value=value),self.assertRaises(ValueError):
                checked_ipv4(value)
