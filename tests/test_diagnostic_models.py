import unittest
from diagnostic_models import (
    DiagnosticError,
    EcuIdentity,
    Freshness,
    SourceKind,
    TelemetryValue,
)


class DiagnosticModelsTest(unittest.TestCase):
    def test_telemetry_value_preserves_provenance_and_freshness(self):
        sample = TelemetryValue(
            key="rpm",
            value=820.0,
            unit="rpm",
            timestamp=100.5,
            source=SourceKind.LIVE,
            freshness=Freshness.FRESH,
        )
        self.assertEqual(sample.as_dict()["source"], "live")
        self.assertEqual(sample.as_dict()["freshness"], "fresh")
        self.assertEqual(sample.as_dict()["value"], 820.0)

    def test_unavailable_value_has_no_numeric_value(self):
        sample = TelemetryValue.unavailable("boost_actual_mbar", "mbar", SourceKind.LIVE)
        self.assertIsNone(sample.value)
        self.assertEqual(sample.freshness, Freshness.UNAVAILABLE)

    def test_ecu_identity_serializes_optional_fields(self):
        identity = EcuIdentity(part_number="038906018", protocol="KW1281")
        self.assertEqual(identity.as_dict()["part_number"], "038906018")
        self.assertIsNone(identity.as_dict()["software_version"])

    def test_diagnostic_error_is_structured(self):
        error = DiagnosticError("timeout", "ECU did not respond", recoverable=True)
        self.assertEqual(error.code, "timeout")
        self.assertTrue(error.recoverable)
