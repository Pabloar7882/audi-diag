import csv
from contextlib import redirect_stderr
import io
import json
import unittest

from tools.compare_kw1281_traces import TraceInputError, compare_trace_documents, load_trace_text, main


class Kw1281TraceComparatorTest(unittest.TestCase):
    def test_json_and_csv_accept_old_v1_and_ignore_usb_read_chunk_boundaries(self):
        json_text = json.dumps({
            "schema": "fis-afn-diagnostic-trace/v1",
            "events": [event("tx", "03"), event("tx", "010003"), event("rx", "5501"), event("rx", "8a")],
        })
        csv_text = io.StringIO()
        writer = csv.DictWriter(csv_text, fieldnames=("timestamp", "monotonic", "direction", "phase", "data_hex", "result", "error", "interpretation"))
        writer.writeheader()
        writer.writerows([
            event("tx", "03010003"),
            event("rx", "55018a", result="fragment,\ncontinued"),
        ])
        left = load_trace_text(json_text, ".json", "old-windows.json")
        right = load_trace_text(csv_text.getvalue(), ".csv", "android.csv")
        result = compare_trace_documents(left, right)

        self.assertTrue(result["byte_comparison"]["tx"]["equal"])
        self.assertTrue(result["byte_comparison"]["rx"]["equal"])
        self.assertFalse(result["frames_compared"])
        self.assertIn("checksum not present", result["report"].lower())

    def test_complete_semantic_frames_compare_and_count_identity_and_measurements(self):
        frames = [
            event("rx_frame", frame(0, 0xF6, b"AFN"), sequence=1),
            event("tx_frame", frame(1, 0x09, b""), sequence=2),
            event("rx_frame", frame(2, 0xE7, bytes((1, 2, 3))), sequence=3),
        ]
        result = compare_trace_documents(
            document(frames),
            document([dict(item) for item in frames]),
        )

        self.assertTrue(result["frames_compared"])
        self.assertTrue(result["frame_sequences_equal"])
        self.assertEqual(result["left"]["identity_frames"], 1)
        self.assertEqual(result["left"]["measurement_frames"], 1)

    def test_reports_first_frame_and_byte_mismatch_without_using_timestamps(self):
        left = document([
            event("tx", "0102", sequence=1, monotonic=100),
            event("tx", "03", sequence=2, monotonic=101),
            event("tx_frame", frame(1, 0x29, b"\x01"), sequence=3, monotonic=102),
        ])
        right = document([
            event("tx", "0102", sequence=1, monotonic=5),
            event("tx", "04", sequence=2, monotonic=6),
            event("tx_frame", frame(1, 0x29, b"\x03"), sequence=3, monotonic=7),
        ])
        result = compare_trace_documents(left, right)

        self.assertEqual(result["byte_comparison"]["tx"]["first_difference"]["offset"], 2)
        self.assertFalse(result["frame_sequences_equal"])
        self.assertEqual(result["first_frame_difference"]["index"], 0)
        self.assertEqual(result["config_mismatches"], [])

    def test_reports_ack_timeout_counter_etx_and_checksum_scope(self):
        bad = bytearray(frame(9, 0xE7, b"\x01"))
        bad[-1] = 0x00
        doc = document([
            event("error", "", error="byte_ack_invalid", phase="byte_complement"),
            event("rx", "", result="empty_read", phase="group_response"),
            event("rx_frame", bad, phase="block_payload_rx"),
            event("error", "", error="block_counter_invalid"),
        ])
        report = compare_trace_documents(doc, document([]))["report"].lower()

        self.assertIn("ack/complement", report)
        self.assertIn("timeout", report)
        self.assertIn("counter", report)
        self.assertIn("etx", report)
        self.assertIn("checksum not present", report)

    def test_configuration_mismatches_are_reported_only_when_both_sides_have_values(self):
        left = document([], sessions=[{"baudrate": 10400, "local_echo": True}])
        right = document([], sessions=[{"baudrate": 9600, "local_echo": False}])
        result = compare_trace_documents(left, right)

        self.assertEqual(
            {item["field"] for item in result["config_mismatches"]},
            {"baudrate", "local_echo"},
        )

    def test_rejects_malformed_json_schema_hex_and_cli_input(self):
        with self.assertRaises(TraceInputError):
            load_trace_text('{"schema":"other","events":[]}', ".json", "bad.json")
        malformed_hex = json.dumps({
            "schema": "fis-afn-diagnostic-trace/v1", "events": [event("rx", "0xz1")],
        })
        with self.assertRaises(TraceInputError):
            load_trace_text(malformed_hex, ".json", "bad-hex.json")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["missing-reference.json", "missing-candidate.json"]), 2)

    def test_semantic_frame_validation_reports_length_and_counter_order(self):
        malformed = bytearray(frame(2, 0xF6, b"A"))
        malformed[0] += 1
        result = compare_trace_documents(document([
            event("rx_frame", frame(0, 0x09, b""), sequence=1),
            event("rx_frame", frame(4, 0xF6, b"A"), sequence=2),
            event("rx_frame", malformed, sequence=3),
        ]), document([]))
        report = result["report"].lower()

        self.assertIn("counter", report)
        self.assertIn("length", report)


def event(direction, data=b"", *, sequence=None, monotonic=1.0, phase="test", result="ok", error=""):
    if isinstance(data, (bytes, bytearray)):
        data_hex = bytes(data).hex()
    else:
        data_hex = data
    item = {
        "timestamp": 1.0,
        "monotonic": monotonic,
        "direction": direction,
        "phase": phase,
        "data_hex": data_hex,
        "result": result,
        "error": error,
        "interpretation": "",
    }
    if sequence is not None:
        item["sequence"] = sequence
    return item


def frame(counter, title, data):
    body = bytes((counter, title)) + data + b"\x03"
    return bytes((len(body),)) + body


def document(events, sessions=None):
    return {
        "schema": "fis-afn-diagnostic-trace/v1",
        "platform": "test",
        "sessions": sessions or [],
        "events": events,
    }


if __name__ == "__main__":
    unittest.main()
