import unittest

from diagnostic_models import DiagnosticError
from kline_transport import ReplayTransport
from vag_protocol import Kw1281Session, decode_frame, encode_frame


class ReadOnlyRegressionTest(unittest.TestCase):
    def test_protocol_fixture_emits_only_identification_and_group_reads(self):
        identity = b"ID|038906018|038906018AF|Bosch"
        group = b"GROUP|003|rpm=820"
        transport = ReplayTransport([encode_frame(identity), encode_frame(group)])
        session = Kw1281Session()
        session.identify(transport)
        session.request_group(transport, 3)
        payloads = [decode_frame(write) for write in transport.writes]
        self.assertEqual(payloads, [b"IDENTIFY", bytes([3])])
        self.assertNotIn(b"WRITE", b"".join(payloads))

    def test_write_capable_service_is_blocked_before_transport_write(self):
        transport = ReplayTransport([])
        with self.assertRaises(DiagnosticError):
            Kw1281Session().send_service(transport, 0x2E, b"WRITE")
        self.assertEqual(transport.writes, [])
