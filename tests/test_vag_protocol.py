import unittest

from diagnostic_models import DiagnosticError
from vag_protocol import (
    Kw1281Session,
    checksum8,
    decode_frame,
    encode_frame,
    strip_echo,
)


class FixtureTransport:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def read(self, size, timeout):
        if not self.chunks:
            raise DiagnosticError("timeout", "fixture timeout", recoverable=True)
        return self.chunks.pop(0)[:size]


class VagProtocolTest(unittest.TestCase):
    def test_checksum_and_round_trip(self):
        payload = b"\x01\x02\x03"
        self.assertEqual(checksum8(payload), 6)
        self.assertEqual(decode_frame(encode_frame(payload)), payload)

    def test_rejects_bad_checksum(self):
        frame = bytearray(encode_frame(b"\x01\x02"))
        frame[-1] ^= 0xFF
        with self.assertRaises(DiagnosticError):
            decode_frame(bytes(frame))

    def test_rejects_truncated_frame(self):
        with self.assertRaises(DiagnosticError):
            decode_frame(b"\x03\x01")

    def test_rejects_oversized_frame(self):
        with self.assertRaises(DiagnosticError):
            decode_frame(encode_frame(b"\x00" * 10), max_length=4)

    def test_removes_only_matching_echo(self):
        self.assertEqual(strip_echo(b"\x01\x02", b"\x01\x02\x03"), b"\x03")
        self.assertEqual(strip_echo(b"\x01\x02", b"\x09"), b"\x09")

    def test_identifies_ecu_from_read_only_fixture(self):
        response = encode_frame(b"ID|038906018|038906018AF|Bosch")
        transport = FixtureTransport([response])
        identity = Kw1281Session().identify(transport)
        self.assertEqual(identity.part_number, "038906018")
        self.assertEqual(identity.software_version, "038906018AF")
        self.assertEqual(identity.protocol, "KW1281")
        self.assertEqual(transport.writes, [encode_frame(b"IDENTIFY")])

    def test_timeout_does_not_return_identity(self):
        with self.assertRaises(DiagnosticError) as raised:
            Kw1281Session().identify(FixtureTransport([]))
        self.assertEqual(raised.exception.code, "timeout")

    def test_reads_measurement_group_cells(self):
        response = encode_frame(b"GROUP|003|rpm=820|maf_actual_mg=410")
        transport = FixtureTransport([response])
        cells = Kw1281Session().request_group(transport, 3)
        self.assertEqual(cells, [
            {"label": "rpm", "value": 820.0},
            {"label": "maf_actual_mg", "value": 410.0},
        ])
        self.assertEqual(transport.writes, [encode_frame(bytes([3]))])

    def test_reads_typed_group_values_with_raw_provenance(self):
        response = encode_frame(b"GROUP|003|rpm=820|vendor_raw=x")
        transport = FixtureTransport([response])
        values = Kw1281Session().request_group_values(transport, 3)
        self.assertEqual(values[0].raw, "rpm=820")
        self.assertEqual(values[0].source.value, "replay")
        self.assertIsNone(values[1].value)
        self.assertEqual(values[1].raw, "vendor_raw=x")
        self.assertEqual(transport.writes, [encode_frame(bytes([3]))])

    def test_reads_maximum_response_after_echo(self):
        response_payload = b"1234567890"
        request = encode_frame(b"")
        response = encode_frame(response_payload)
        transport = FixtureTransport([request + response])
        session = Kw1281Session(max_frame_length=len(response_payload))
        self.assertEqual(session.send_service(transport, 0x01, b""), response_payload)

    def test_allowed_service_ids_cannot_carry_arbitrary_payloads(self):
        transport = FixtureTransport([])
        session = Kw1281Session()
        with self.assertRaisesRegex(DiagnosticError, "payload"):
            session.send_service(transport, 0x01, b"WRITE")
        with self.assertRaisesRegex(DiagnosticError, "exactly one"):
            session.send_service(transport, 0x22, b"GROUP|001")
        self.assertEqual(transport.writes, [])
