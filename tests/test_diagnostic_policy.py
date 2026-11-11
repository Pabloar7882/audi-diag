import unittest
from diagnostic_policy import is_allowed_service, require_allowed_service
from diagnostic_models import DiagnosticError


class DiagnosticPolicyTest(unittest.TestCase):
    def test_allows_identification_and_measurement_reads(self):
        self.assertTrue(is_allowed_service(0x01))
        self.assertTrue(is_allowed_service(0x22))
        require_allowed_service(0x01)
        require_allowed_service(0x22)

    def test_rejects_unknown_service(self):
        self.assertFalse(is_allowed_service(0x99))
        with self.assertRaises(DiagnosticError):
            require_allowed_service(0x99)

    def test_rejects_write_capable_service(self):
        self.assertFalse(is_allowed_service(0x2E))
        with self.assertRaises(DiagnosticError):
            require_allowed_service(0x2E)
