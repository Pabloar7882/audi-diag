import csv
import json
import unittest

from diagnostic_models import DiagnosticError
from diagnostic_monitor import DiagnosticTrace, MonitoredTransport


class FixtureTransport:
    def __init__(self):
        self.chunks = [b"\x55\x01", b"\x8a"]
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def read(self, size, timeout):
        if not self.chunks:
            raise DiagnosticError("timeout", "fixture timeout", recoverable=True)
        return self.chunks.pop(0)


class DiagnosticMonitorTest(unittest.TestCase):
    def test_trace_adds_sequence_origin_session_metadata_and_bounded_payloads(self):
        trace = DiagnosticTrace(max_events=4, max_event_bytes=2)
        session_id = trace.begin_session("COM7", 10400, local_echo=True)
        trace.record("tx", "test", bytes((1, 2, 3)))
        payload = json.loads(trace.export_json())
        csv_rows = list(csv.DictReader(trace.export_csv().splitlines()))

        events = payload["events"]
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual(events[-1]["session_id"], session_id)
        self.assertEqual(events[-1]["origin"], "serial_write_call")
        self.assertEqual(events[-1]["data_hex"], "0102")
        self.assertEqual(events[-1]["truncated_bytes"], 1)
        self.assertEqual(payload["sessions"][0]["baudrate"], 10400)
        self.assertEqual(csv_rows[1]["sequence"], "2")
        self.assertEqual(csv_rows[1]["electrical_waveform_measured"], "False")

    def test_records_tx_rx_timeout_and_exports_raw_bytes(self):
        trace = DiagnosticTrace(max_events=10)
        transport = MonitoredTransport(FixtureTransport(), trace, phase="keyword_wait")

        transport.write(b"\x75")
        self.assertEqual(transport.read(2, 0.1), b"\x55\x01")
        self.assertEqual(transport.read(1, 0.1), b"\x8a")
        with self.assertRaises(DiagnosticError):
            transport.read(1, 0.1)

        payload = json.loads(trace.export_json())
        self.assertEqual([event["direction"] for event in payload["events"]], ["tx", "rx", "rx", "error"])
        self.assertEqual(payload["events"][0]["data_hex"], "75")
        self.assertEqual(payload["events"][1]["data_hex"], "5501")
        self.assertEqual(payload["events"][-1]["error"], "timeout")
        self.assertIn("phase", trace.export_csv().splitlines()[0])

    def test_trace_is_bounded(self):
        trace = DiagnosticTrace(max_events=2)
        trace.record("tx", "a", b"\x01")
        trace.record("tx", "b", b"\x02")
        trace.record("tx", "c", b"\x03")

        self.assertEqual(len(json.loads(trace.export_json())["events"]), 2)

    def test_trace_includes_packet_interpretation(self):
        trace = DiagnosticTrace()
        trace.record("rx", "keyword", b"\x55\x01\x8a", interpretation="KW1281 sync + keywords")
        event = trace.snapshot()[0]
        self.assertEqual(event["interpretation"], "KW1281 sync + keywords")
