import unittest
import json

from diagnostic_models import DiagnosticError
from diagnostic_monitor import DiagnosticTrace, MonitoredTransport
from kw1281_physical import Kw1281PhysicalSession
from kw1281_simulator import SimulatedKw1281Ecu, SimulatedEcuTransport
from tools.compare_kw1281_traces import compare_trace_documents


class Kw1281IntegrationTest(unittest.TestCase):
    def test_monitored_kw1281_session_exports_semantic_identity_and_measurement_frames(self):
        trace = DiagnosticTrace()
        session_id = trace.begin_session("COM7", 10400, local_echo=False)
        transport = MonitoredTransport(SimulatedEcuTransport(SimulatedKw1281Ecu()), trace)
        session = Kw1281PhysicalSession()

        identity = session.identify(transport)
        session.request_group(transport, 1)

        payload = json.loads(trace.export_json())
        frames = [event for event in payload["events"] if event["direction"] == "rx_frame"]
        titles = [bytes.fromhex(event["data_hex"])[2] for event in frames]
        self.assertEqual(payload["sessions"][0]["id"], session_id)
        self.assertEqual(payload["sessions"][0]["ecu_part_number"], identity.part_number)
        self.assertIn(0xF6, titles)
        self.assertIn(0xE7, titles)
        self.assertEqual(
            [event["sequence"] for event in payload["events"]],
            sorted(event["sequence"] for event in payload["events"]),
        )
        comparison = compare_trace_documents(payload, payload)
        self.assertEqual(comparison["left"]["framing_errors"], 0)
        self.assertTrue(comparison["frame_sequences_equal"])

    def test_simulated_ecu_identifies_and_supports_repeated_explicit_group_reads(self):
        ecu = SimulatedKw1281Ecu()
        transport = SimulatedEcuTransport(ecu)
        session = Kw1281PhysicalSession()

        identity = session.identify(transport)
        session.keep_alive(transport)
        values = session.request_group(transport, 1)
        repeated_values = session.request_group(transport, 3)

        self.assertEqual(identity.part_number, "038906018AF")
        self.assertEqual(values[0].raw, "0c1020")
        self.assertEqual(repeated_values[0].raw, "0c1020")
        self.assertEqual(ecu.requested_groups, [1, 3])
        self.assertEqual(ecu.forbidden_services, [])

    def test_simulated_session_counter_wraps_after_repeated_reads(self):
        ecu = SimulatedKw1281Ecu()
        transport = SimulatedEcuTransport(ecu)
        session = Kw1281PhysicalSession()
        session.identify(transport)

        for _ in range(65):
            session.request_group(transport, 1)

        self.assertEqual(len(ecu.requested_groups), 65)
        self.assertEqual(session._counter, 7)

    def test_simulated_ecu_rejects_an_invalid_sync_byte(self):
        transport = SimulatedEcuTransport(SimulatedKw1281Ecu(sync_byte=0x00))

        with self.assertRaises(DiagnosticError) as raised:
            Kw1281PhysicalSession().identify(transport)

        self.assertEqual(raised.exception.code, "sync_invalid")

    def test_simulated_ecu_checks_the_keyword_complement(self):
        transport = SimulatedEcuTransport(SimulatedKw1281Ecu())
        transport.init_5baud(0x01, 0.2)

        with self.assertRaises(DiagnosticError) as raised:
            transport.write(b"\x00")

        self.assertEqual(raised.exception.code, "keyword_complement_invalid")

    def test_forbidden_service_is_recorded_as_no_transport_write(self):
        ecu = SimulatedKw1281Ecu()
        transport = SimulatedEcuTransport(ecu)

        with self.assertRaises(DiagnosticError):
            Kw1281PhysicalSession().send_service(transport, 0x2E, b"\x01")

        self.assertEqual(transport.writes, [])
        self.assertEqual(ecu.forbidden_services, [])
