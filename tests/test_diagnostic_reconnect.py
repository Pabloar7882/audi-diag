import time
import unittest

from diagnostic_models import DiagnosticError, EcuIdentity
from diagnostic_source import DiagnosticSource


class ReconnectTest(unittest.TestCase):
    def test_source_retries_recoverable_connection_and_reports_retry_count(self):
        attempts = []

        class FakeTransport:
            def close(self):
                pass

        class FakeSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018")

            def request_group(self, transport, group):
                return [{"label": "rpm", "value": 900}]

        def factory():
            attempts.append(len(attempts))
            if len(attempts) == 1:
                raise DiagnosticError("timeout", "temporary", recoverable=True)
            return FakeTransport()

        source = DiagnosticSource(
            factory,
            FakeSession,
            groups=[3],
            sample_interval=0.01,
            max_retries=2,
            retry_backoff=0.01,
        )
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "live", 1.0))
            self.assertEqual(source.snapshot()["retry_count"], 1)
            self.assertEqual(len(attempts), 2)
        finally:
            source.stop()

    def test_source_stops_after_retry_limit(self):
        def factory():
            raise DiagnosticError("timeout", "offline", recoverable=True)

        source = DiagnosticSource(factory, lambda: None, max_retries=1, retry_backoff=0.01)
        source.start()
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "error", 1.0))
            self.assertEqual(source.snapshot()["retry_count"], 1)
        finally:
            source.stop()


if __name__ == "__main__":
    unittest.main()
