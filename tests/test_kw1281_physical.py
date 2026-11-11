import unittest

from diagnostic_models import DiagnosticError
from kw1281_physical import Kw1281PhysicalSession


def block(counter, title, *data):
    payload = bytes([counter, title, *data, 0x03])
    return bytes([len(payload)]) + payload


def acknowledgements(frame):
    return [bytes([value ^ 0xFF]) for value in frame[:-1]]


def ack_block(counter):
    return block(counter, 0x09)


class PhysicalTransport:
    def __init__(self, chunks, *, local_echo=False):
        self.chunks = list(chunks)
        self.buffer = bytearray()
        self.writes = []
        self.init_calls = []
        self.local_echo = local_echo

    def init_5baud(self, address, bit_time):
        self.init_calls.append((address, bit_time))

    def write(self, data):
        self.writes.append(data)
        if self.local_echo:
            self.buffer.extend(data)

    def read(self, size, timeout):
        while len(self.buffer) < size and self.chunks:
            self.buffer.extend(self.chunks.pop(0))
        if not self.buffer:
            raise DiagnosticError("timeout", "fixture timeout", recoverable=True)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


class PhysicalKw1281Test(unittest.TestCase):
    def test_component_continuation_flag_requests_extra_identity(self):
        transport = PhysicalTransport([
            bytes([0x55, 0x01, 0x8A]),
            block(0x00, 0xF6, *b"038906018AF"),
            *acknowledgements(ack_block(0x01)),
            block(0x02, 0xF6, 0xC5, *b"DC15VM+"),
            *acknowledgements(ack_block(0x03)),
            block(0x04, 0xF6, *b"0001"),
            *acknowledgements(ack_block(0x05)),
            block(0x06, 0x09),
            *acknowledgements(block(0x07, 0x00)),
            block(0x08, 0xF6, *b"extra data"),
            *acknowledgements(ack_block(0x09)),
            block(0x0A, 0x09),
        ])

        identity = Kw1281PhysicalSession().identify(transport)

        self.assertEqual(identity.part_number, "038906018AF")
        self.assertEqual(identity.component, "EDC15VM+")
        self.assertEqual(identity.software_version, "0001")
        self.assertEqual(identity.extra_identification, "extra data")
        self.assertEqual(transport.chunks, [])

    def test_identification_receives_ecu_startup_block_before_sending_any_request(self):
        startup = block(0x00, 0xF6, *b"038906018AF")
        transport = PhysicalTransport([
            bytes([0x55, 0x01, 0x8A]),
            startup,
            *acknowledgements(ack_block(0x01)),
            block(0x02, 0x09),
        ])

        identity = Kw1281PhysicalSession().identify(transport)

        self.assertEqual(transport.init_calls, [(0x01, 0.2)])
        self.assertEqual(transport.writes[0], bytes([0x75]))
        self.assertEqual(identity.part_number, "038906018AF")
        self.assertIsNone(identity.supplier)
        self.assertEqual(
            transport.writes[1:1 + len(startup) - 1],
            [bytes([value ^ 0xFF]) for value in startup[:-1]],
        )
        self.assertIn(bytes([0x09]), transport.writes)
        self.assertNotIn(bytes([0x00]), transport.writes)

    def test_identification_separates_part_component_and_extra_identity(self):
        first = block(0x00, 0xF6, 0xB0, *b"38906 018AF")
        component = block(0x02, 0xF6, *b"EDC15VM+")
        version = block(0x04, 0xF6, *b"0001")
        read_ident = block(0x07, 0x00)
        extra = block(0x08, 0xF6, *b"extra data")
        transport = PhysicalTransport([
            bytes([0x55, 0x01, 0x8A]),
            first,
            *acknowledgements(ack_block(0x01)),
            component,
            *acknowledgements(ack_block(0x03)),
            version,
            *acknowledgements(ack_block(0x05)),
            block(0x06, 0x09),
            *acknowledgements(read_ident),
            extra,
            *acknowledgements(ack_block(0x09)),
            block(0x0A, 0x09),
        ])

        identity = Kw1281PhysicalSession().identify(transport)

        self.assertEqual(identity.part_number, "038906 018AF")
        self.assertEqual(identity.component, "EDC15VM+")
        self.assertEqual(identity.software_version, "0001")
        self.assertEqual(identity.extra_identification, "extra data")
        self.assertEqual(transport.writes[0], bytes([0x75]))
        self.assertIn(bytes([0x00]), transport.writes)

    def test_keepalive_acknowledges_without_reading_a_group(self):
        startup = block(0x00, 0xF6, *b"038906018AF")
        transport = PhysicalTransport([
            bytes([0x55, 0x01, 0x8A]), startup,
            *acknowledgements(ack_block(0x01)), block(0x02, 0x09),
            *acknowledgements(ack_block(0x03)), block(0x04, 0x09),
        ])
        session = Kw1281PhysicalSession()
        session.identify(transport)

        session.keep_alive(transport)

        self.assertIn(bytes([0x09]), transport.writes)
        self.assertNotIn(bytes([0x29]), transport.writes)

    def test_configured_post_init_baud_is_selected_before_slow_init(self):
        class ConfigurableTransport(PhysicalTransport):
            def __init__(self, chunks):
                super().__init__(chunks)
                self.events = []

            def set_normal_baudrate(self, baudrate):
                self.events.append(("baud", baudrate))

            def init_5baud(self, address, bit_time):
                self.events.append(("slow_init", address, bit_time))
                super().init_5baud(address, bit_time)

        startup = block(0x00, 0xF6, *b"038906018AF")
        transport = ConfigurableTransport([
            bytes([0x55, 0x01, 0x8A]), startup,
            *acknowledgements(ack_block(0x01)), block(0x02, 0x09),
        ])

        Kw1281PhysicalSession(baudrate=9600).identify(transport)

        self.assertEqual(transport.events[:2], [
            ("baud", 9600), ("slow_init", 0x01, 0.2)
        ])

    def test_rejects_bad_local_echo_before_waiting_for_keyword_complement(self):
        class BadEchoTransport(PhysicalTransport):
            def write(self, data):
                self.writes.append(data)
                self.buffer.extend(bytes([data[0] ^ 0x01]))

        transport = BadEchoTransport([bytes([0x55, 0x01, 0x8A])], local_echo=True)

        with self.assertRaises(DiagnosticError) as raised:
            Kw1281PhysicalSession().identify(transport)

        self.assertEqual(raised.exception.code, "tx_echo_invalid")
        self.assertEqual(transport.writes, [bytes([0x75])])

    def test_reads_and_acknowledges_a_measurement_block_then_ends_response(self):
        startup = block(0x00, 0xF6, *b"038906018AF")
        request = block(0x03, 0x29, 0x01)
        response = block(0x04, 0xE7, 0x0C, 0x10, 0x20)
        transport = PhysicalTransport([
            bytes([0x55, 0x01, 0x8A]), startup,
            *acknowledgements(ack_block(0x01)), block(0x02, 0x09),
            *acknowledgements(request), response[:2], response[2:],
            *acknowledgements(ack_block(0x05)), block(0x06, 0x09),
        ])
        session = Kw1281PhysicalSession()
        session.identify(transport)

        values = session.request_group(transport, 1)

        sent = transport.writes
        request_bytes = [bytes([value]) for value in request]
        self.assertTrue(any(sent[i:i + len(request_bytes)] == request_bytes
                            for i in range(len(sent) - len(request_bytes) + 1)))
        self.assertEqual(values[0].raw, "0c1020")

    def test_rejects_measurement_groups_outside_supported_afn_allowlist_without_io(self):
        session = Kw1281PhysicalSession()
        session._identified = True
        transport = PhysicalTransport([])

        with self.assertRaises(DiagnosticError) as raised:
            session.request_group(transport, 42)

        self.assertEqual(raised.exception.code, "group_not_allowed")
        self.assertEqual(transport.writes, [])

    def test_rejects_non_kw1281_keywords_before_sending_blocks(self):
        transport = PhysicalTransport([bytes([0x55, 0x01, 0x89])])

        with self.assertRaises(DiagnosticError) as raised:
            Kw1281PhysicalSession().identify(transport)

        self.assertEqual(raised.exception.code, "protocol_unsupported")
        self.assertEqual(transport.writes, [bytes([0x76])])

    def test_forbidden_service_is_recorded_as_no_transport_write(self):
        transport = PhysicalTransport([])

        with self.assertRaises(DiagnosticError):
            Kw1281PhysicalSession().send_service(transport, 0x2E, b"\x01")

        self.assertEqual(transport.writes, [])

    def test_rejects_arbitrary_payloads_for_allowed_service_ids(self):
        transport = PhysicalTransport([])
        session = Kw1281PhysicalSession()
        with self.assertRaisesRegex(DiagnosticError, "payload"):
            session.send_service(transport, 0x01, b"unsafe")
        with self.assertRaisesRegex(DiagnosticError, "exactly one"):
            session.send_service(transport, 0x22, b"\x01\x02")
        with self.assertRaisesRegex(DiagnosticError, "measurement groups 001, 003 and 011"):
            session.send_service(transport, 0x22, b"\x2a")
        self.assertEqual(transport.writes, [])


if __name__ == "__main__":
    unittest.main()
