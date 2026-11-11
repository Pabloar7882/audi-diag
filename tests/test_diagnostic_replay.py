import unittest

from diagnostic_replay import ReplayScenario, scenarios
from diagnostic_models import DiagnosticError


class DiagnosticReplayTest(unittest.TestCase):
    def test_healthy_scenario_has_identity_and_valid_group_frames(self):
        scenario = scenarios()["healthy"]
        transport = scenario.create_transport()
        self.assertFalse(transport.closed)
        self.assertGreaterEqual(len(scenario.frames), 2)
        self.assertEqual(transport.read(512, 1.0)[1:3], b"ID")

    def test_bad_checksum_scenario_is_deterministic(self):
        transport = scenarios()["bad_checksum"].create_transport()
        transport.read(512, 1.0)
        with self.assertRaises(DiagnosticError) as raised:
            from vag_protocol import decode_frame
            decode_frame(transport.read(512, 1.0))
        self.assertEqual(raised.exception.code, "checksum_invalid")

    def test_timeout_and_disconnect_are_explicit(self):
        for name, code in (("timeout", "timeout"), ("disconnect", "transport_closed")):
            transport = scenarios()[name].create_transport()
            if name == "disconnect":
                transport.close()
            with self.assertRaises(DiagnosticError) as raised:
                transport.read(64, 0.1)
            self.assertEqual(raised.exception.code, code)


if __name__ == "__main__":
    unittest.main()
