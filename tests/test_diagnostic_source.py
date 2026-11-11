import time
import threading
import unittest

from diagnostic_models import DiagnosticError, EcuIdentity
from diagnostic_source import DiagnosticSource, create_serial_diagnostic_source
from kline_transport import ReplayTransport
from vag_protocol import Kw1281Session


class SerialTestTransport(ReplayTransport):
    def __init__(self):
        super().__init__([])

    def open(self, device, *, baudrate, timeout):
        pass


class SourceTest(unittest.TestCase):
    def test_fatal_group_protocol_error_ends_session_without_retry(self):
        for code in ("block_counter_invalid", "byte_ack_invalid"):
            with self.subTest(code=code):
                requests = []
                opened = []

                class FatalSession:
                    def identify(self, transport):
                        return EcuIdentity(part_number="038906018AF")

                    def request_group(self, transport, group):
                        requests.append(group)
                        raise DiagnosticError(code, "fatal protocol error")

                def make_transport():
                    opened.append(ReplayTransport([]))
                    return opened[-1]

                source = DiagnosticSource(
                    make_transport, FatalSession, groups=[1],
                    sample_interval=0.01, group_max_attempts=3,
                    max_retries=2, retry_backoff=0,
                )
                source.start()
                try:
                    source._thread.join(timeout=0.15)
                    self.assertFalse(source._thread.is_alive())
                    self.assertEqual(requests, [1])
                    self.assertEqual(len(opened), 1)
                    self.assertEqual(source.snapshot()["error"]["code"], code)
                    self.assertEqual(source.snapshot()["values"], {})
                    self.assertEqual(source.snapshot()["freshness"], "unavailable")
                finally:
                    source.stop()

    def test_serial_selection_is_read_once_then_keepalive_continues(self):
        calls = []
        kept_alive = threading.Event()

        class SingleReadSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                calls.append(("group", group))
                return [{"label": "rpm", "value": 820.0}]

            def keep_alive(self, transport):
                calls.append("keepalive")
                if calls.count("keepalive") >= 2:
                    kept_alive.set()

        source = create_serial_diagnostic_source(
            "COM7", transport_factory=SerialTestTransport,
            session_factory=SingleReadSession, groups=[1],
        )
        source.start()
        try:
            self.assertTrue(kept_alive.wait(0.9))
            self.assertEqual(calls[:3], [("group", 1), "keepalive", "keepalive"])
            self.assertEqual(calls.count(("group", 1)), 1)
            self.assertEqual(source.snapshot()["values"], {"rpm": 820.0})
        finally:
            source.stop()
        self.assertEqual(source.status(), "stopped")

    def test_selection_during_inflight_read_is_preserved_even_for_same_group(self):
        for next_group in (1, 3):
            with self.subTest(next_group=next_group):
                entered = threading.Event()
                release = threading.Event()
                idle = threading.Event()
                reads = []

                class BlockingSession:
                    def identify(self, transport):
                        return EcuIdentity(part_number="038906018AF")

                    def request_group(self, transport, group):
                        reads.append(group)
                        if len(reads) == 1:
                            entered.set()
                            release.wait(1.0)
                        return [{"label": "rpm", "value": 820.0}]

                    def keep_alive(self, transport):
                        idle.set()

                source = DiagnosticSource(
                    lambda: ReplayTransport([]), BlockingSession,
                    groups=[1], sample_interval=0.01,
                )
                source.start()
                try:
                    self.assertTrue(entered.wait(1.0))
                    source.set_groups([next_group])
                    release.set()
                    self.assertTrue(idle.wait(0.5))
                    self.assertEqual(reads, [1, next_group])
                finally:
                    release.set()
                    source.stop()

    def test_set_groups_rejects_before_identification(self):
        source = DiagnosticSource(lambda: None, lambda: None)
        with self.assertRaisesRegex(ValueError, "identificação"):
            source.set_groups([1])

    def test_set_groups_rejects_multiple_or_unsupported_groups(self):
        source = DiagnosticSource(lambda: None, lambda: None)
        source._state["ecu"] = {"part_number": "038906018AF"}
        with self.assertRaisesRegex(ValueError, "um grupo"):
            source.set_groups([1, 3])
        with self.assertRaisesRegex(ValueError, "001, 003 ou 011"):
            source.set_groups([42])

    def test_constructor_rejects_automatic_group_scan(self):
        with self.assertRaisesRegex(ValueError, "um grupo"):
            DiagnosticSource(lambda: None, lambda: None, groups=[1, 3])

    def test_publishes_samples_for_one_explicit_group(self):
        requested_groups = []

        class PartialSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018")

            def request_group(self, transport, group):
                requested_groups.append(group)
                return [{"label": "maf_actual_mg", "value": 410.0}]

        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([]),
            session_factory=PartialSession,
            groups=[3],
            sample_interval=0.01,
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(
                lambda state: state["status"] == "live"
                and "maf_actual_mg" in state["values"],
                1.0,
            ))
            snapshot = source.snapshot()
            self.assertEqual(snapshot["values"]["maf_actual_mg"], 410.0)
            self.assertEqual(snapshot["attempts"]["3"], 1)
            self.assertTrue(requested_groups)
            self.assertEqual(set(requested_groups), {3})
        finally:
            source.stop()

    def test_cancellation_during_retry_does_not_reconnect_or_set_error(self):
        created = []

        def make_transport():
            created.append(len(created))
            return ReplayTransport([])

        source = DiagnosticSource(
            transport_factory=make_transport,
            session_factory=lambda: Kw1281Session(),
            groups=[],
            max_retries=5,
            retry_backoff=1.0,
        )
        source.start()
        self.assertTrue(source.wait_for(lambda state: state["status"] == "reconnecting", 1.0))
        source.stop()
        self.assertEqual(source.snapshot()["status"], "stopped")
        self.assertEqual(len(created), 1)

    def test_stop_during_transport_creation_closes_transport_without_identifying(self):
        created = threading.Event()
        release = threading.Event()
        identified = []

        class FakeTransport:
            def close(self):
                identified.append("closed")

        def make_transport():
            created.set()
            release.wait(1.0)
            return FakeTransport()

        class FakeSession:
            def identify(self, transport):
                identified.append("identified")
                return EcuIdentity(part_number="038906018")

        source = DiagnosticSource(make_transport, FakeSession)
        source.start()
        self.assertTrue(created.wait(1.0))
        source.stop()
        release.set()
        deadline = time.time() + 1.0
        while time.time() < deadline and "closed" not in identified:
            time.sleep(0.01)
        self.assertNotIn("identified", identified)
        self.assertIn("closed", identified)

    def test_serial_source_opens_the_selected_device_lazily(self):
        created = []

        class FakeTransport:
            def open(self, device, *, baudrate, timeout):
                created.append((device, baudrate, timeout))

            def close(self):
                pass

        class FakeSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018")

            def close(self, transport):
                pass

        source = create_serial_diagnostic_source(
            "COM7",
            transport_factory=FakeTransport,
            session_factory=FakeSession,
            groups=(),
        )
        source.start()
        self.assertTrue(source.wait_for(lambda state: state["status"] == "identified", 1.0))
        source.stop()
        self.assertEqual(created, [("COM7", 9600, 1.0)])

    def test_source_identifies_ecu_and_reports_live_snapshot(self):
        payload = b"ID|038906018|038906018AF|Bosch"
        response = bytes([len(payload)]) + payload + bytes([sum(payload) & 0xFF])
        group_payload = b"GROUP|003|rpm=820"
        group_response = bytes([len(group_payload)]) + group_payload + bytes([sum(group_payload) & 0xFF])
        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([response, group_response]),
            session_factory=lambda: Kw1281Session(),
            groups=[3],
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "live", 1.0))
            snapshot = source.snapshot()
            self.assertEqual(snapshot["ecu"]["part_number"], "038906018")
            self.assertEqual(snapshot["freshness"], "fresh")
        finally:
            source.stop()

    def test_source_timeout_reports_error_without_values(self):
        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([]),
            session_factory=lambda: Kw1281Session(),
            groups=[],
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "error", 1.0))
            snapshot = source.snapshot()
            self.assertEqual(snapshot["values"], {})
            self.assertEqual(snapshot["freshness"], "unavailable")
        finally:
            source.stop()

    def test_stop_is_idempotent_and_cancels_worker(self):
        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([]),
            session_factory=lambda: Kw1281Session(),
            groups=[],
        )
        source.start()
        source.stop()
        source.stop()
        self.assertIn(source.snapshot()["status"], {"stopped", "error"})

    def test_identification_alone_is_not_fresh_telemetry(self):
        response = bytes([30]) + b"ID|038906018|038906018AF|Bosch" + bytes([sum(b"ID|038906018|038906018AF|Bosch") & 0xFF])
        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([response]),
            session_factory=lambda: Kw1281Session(),
            groups=[],
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "identified", 1.0))
            snapshot = source.snapshot()
            self.assertEqual(snapshot["freshness"], "unavailable")
            self.assertIsNone(snapshot["last_update"])
        finally:
            source.stop()

    def test_idle_identified_session_sends_keepalive_without_group_reads(self):
        calls = []

        class KeepAliveSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018")

            def keep_alive(self, transport):
                calls.append("keepalive")

            def request_group(self, transport, group):
                calls.append(("group", group))
                return []

        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([]),
            session_factory=KeepAliveSession,
            sample_interval=0.01,
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "identified", 1.0))
            self.assertTrue(source.wait_for(lambda state: calls.count("keepalive") >= 2, 1.0))
            self.assertNotIn(("group", 1), calls)
        finally:
            source.stop()

    def test_explicit_second_read_error_clears_previous_values(self):
        class FlakySession:
            def identify(self, transport):
                from diagnostic_models import EcuIdentity
                return EcuIdentity(part_number=" ECU")

            def request_group(self, transport, group):
                if not hasattr(self, "called"):
                    self.called = True
                    return [{"label": "rpm", "value": 820.0}]
                raise DiagnosticError("timeout", "lost ECU", recoverable=True)

        source = DiagnosticSource(
            transport_factory=lambda: ReplayTransport([]),
            session_factory=FlakySession,
            groups=[3],
            sample_interval=0.01,
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "live", 1.0))
            source.set_groups([3])
            self.assertTrue(source.wait_for(lambda state: state["status"] == "error", 1.0))
            self.assertEqual(source.snapshot()["values"], {})
        finally:
            source.stop()

    def test_recoverable_group_transport_error_reopens_the_session(self):
        created = []

        class FakeTransport(ReplayTransport):
            def close(self):
                created.append("closed")
                super().close()

        class ReconnectingSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018")

            def request_group(self, transport, group):
                if len(created) == 0:
                    raise DiagnosticError("serial_read_failed", "USB serial connection lost", recoverable=True)
                return [{"label": "rpm", "value": 820.0}]

        opened = []

        def make_transport():
            opened.append(len(opened))
            return FakeTransport([])

        source = DiagnosticSource(
            transport_factory=make_transport,
            session_factory=ReconnectingSession,
            groups=[1],
            max_retries=1,
            retry_backoff=0.01,
            sample_interval=0.01,
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(
                lambda state: state["status"] == "live" and state["values"].get("rpm") == 820.0,
                1.0,
            ))
            self.assertEqual(len(opened), 2)
            self.assertEqual(source.snapshot()["retry_count"], 1)
        finally:
            source.stop()

    def test_group_timeout_restarts_session_before_another_request(self):
        created = []

        class TimeoutThenSuccessSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018")

            def request_group(self, transport, group):
                if len(created) == 1:
                    raise DiagnosticError("timeout", "response was incomplete", recoverable=True)
                return [{"label": "rpm", "value": 820.0}]

        def make_transport():
            created.append(ReplayTransport([]))
            return created[-1]

        source = DiagnosticSource(
            transport_factory=make_transport,
            session_factory=TimeoutThenSuccessSession,
            groups=[1],
            max_retries=1,
            retry_backoff=0.01,
            sample_interval=0.01,
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(
                lambda state: state["status"] == "live" and state["values"].get("rpm") == 820.0,
                1.0,
            ))
            self.assertEqual(len(created), 2)
            self.assertEqual(source.snapshot()["retry_count"], 1)
        finally:
            source.stop()
