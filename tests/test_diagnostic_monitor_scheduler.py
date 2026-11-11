import threading
import time
import unittest

from diagnostic_models import DiagnosticError, EcuIdentity
from diagnostic_source import DiagnosticSource
from kline_transport import ReplayTransport


class MonitorSchedulerTest(unittest.TestCase):
    def make_source(self, session_factory, *, sample_interval=0.01):
        source = DiagnosticSource(
            lambda: ReplayTransport([]),
            session_factory,
            sample_interval=sample_interval,
            retry_backoff=0,
        )
        source.start()
        self.assertTrue(source.wait_for(lambda state: state["ecu"] is not None, 1.0))
        return source

    def test_monitor_is_opt_in_and_orders_groups_canonically(self):
        calls = []
        idle = threading.Event()

        class Session:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                calls.append(group)
                return [{"label": f"g{group}", "value": group}]

            def keep_alive(self, transport):
                idle.set()

        source = self.make_source(Session)
        try:
            self.assertTrue(idle.wait(0.5))
            self.assertEqual(calls, [])
            self.assertFalse(source.snapshot()["monitor"]["active"])

            source.start_monitor([11, 1, 3], min_period_s=0.5)
            self.assertTrue(source.wait_for(lambda state: len(calls) >= 3, 1.0))
            self.assertTrue(source.wait_for(
                lambda state: set(state["group_timestamps"]) == {"1", "3", "11"},
                1.0,
            ))
            source.stop_monitor()
            self.assertEqual(calls[:3], [1, 3, 11])
            self.assertEqual(source.snapshot()["monitor"]["status"], "stopped")
            snapshot = source.snapshot()
            self.assertTrue(all(value >= 0 for value in snapshot["group_latencies"].values()))
            self.assertEqual(set(snapshot["group_latencies"]), {"1", "3", "11"})
        finally:
            source.stop()

    def test_requests_never_overlap_and_stop_cancels_the_next_group(self):
        entered = threading.Event()
        release = threading.Event()
        idle = threading.Event()
        calls = []
        active = 0
        max_active = 0
        guard = threading.Lock()

        class BlockingSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                nonlocal active, max_active
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                calls.append(group)
                if group == 1:
                    entered.set()
                    release.wait(1.0)
                with guard:
                    active -= 1
                return []

            def keep_alive(self, transport):
                idle.set()

        source = self.make_source(BlockingSession)
        try:
            source.start_monitor([1, 3], min_period_s=0.5)
            self.assertTrue(entered.wait(0.5))
            source.stop_monitor()
            release.set()
            self.assertTrue(idle.wait(0.5))
            self.assertEqual(calls, [1])
            self.assertEqual(max_active, 1)
        finally:
            release.set()
            source.stop()

    def test_active_monitor_rejects_duplicate_start_without_resetting_cadence(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        starts = []
        completions = []

        class BlockingFirstSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                starts.append(time.monotonic())
                if len(starts) == 1:
                    first_entered.set()
                    release_first.wait(1.5)
                else:
                    second_started.set()
                completions.append(time.monotonic())
                return []

            def keep_alive(self, transport):
                pass

        source = self.make_source(BlockingFirstSession)
        try:
            source.start_monitor([1], min_period_s=0.5)
            self.assertTrue(first_entered.wait(0.5))
            with self.assertRaisesRegex(ValueError, "monitorização.*ativa"):
                source.start_monitor([1, 3], min_period_s=0.5)
            self.assertEqual(source.snapshot()["monitor"]["groups"], [1])

            release_first.set()
            self.assertTrue(second_started.wait(1.5))
            self.assertGreaterEqual(starts[1] - completions[0], 0.45)
        finally:
            release_first.set()
            source.stop()

    def test_minimum_period_is_measured_from_request_completion(self):
        starts = []
        completions = []
        second_start = threading.Event()

        class TimedSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                starts.append(time.monotonic())
                if len(starts) == 1:
                    time.sleep(0.08)
                    completions.append(time.monotonic())
                else:
                    second_start.set()
                    completions.append(time.monotonic())
                return []

            def keep_alive(self, transport):
                pass

        source = self.make_source(TimedSession)
        try:
            source.start_monitor([1], min_period_s=0.5)
            self.assertTrue(second_start.wait(1.5))
            self.assertGreaterEqual(starts[1] - completions[0], 0.45)
            self.assertLess(starts[1] - completions[0], 1.0)
        finally:
            source.stop()

    def test_group_error_suspends_monitor_without_requesting_next_group(self):
        calls = []
        keepalive = threading.Event()

        class RejectingSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                calls.append(group)
                raise DiagnosticError("group_rejected", "ECU rejected this group", recoverable=True)

            def keep_alive(self, transport):
                keepalive.set()

        source = self.make_source(RejectingSession)
        try:
            source.start_monitor([1, 3], min_period_s=0.5)
            self.assertTrue(source.wait_for(
                lambda state: state["monitor"]["status"] == "suspended", 0.75
            ))
            self.assertEqual(calls, [1])
            self.assertEqual(source.snapshot()["monitor"]["error"]["code"], "group_rejected")
            self.assertTrue(keepalive.wait(0.5))
        finally:
            source.stop()

    def test_old_group_values_age_independently_while_newer_group_updates(self):
        group_three_entered = threading.Event()
        release_group_three = threading.Event()
        second_group_one_entered = threading.Event()
        release_second_group_one = threading.Event()
        group_one_reads = 0

        class StaggeredSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                nonlocal group_one_reads
                if group == 1:
                    group_one_reads += 1
                    if group_one_reads == 2:
                        second_group_one_entered.set()
                        release_second_group_one.wait(2.0)
                if group == 3:
                    group_three_entered.set()
                    release_group_three.wait(2.0)
                return [{"label": f"g{group}", "value": group * 10.0}]

            def keep_alive(self, transport):
                pass

        source = self.make_source(StaggeredSession)
        try:
            source.start_monitor([1, 3], min_period_s=0.5)
            self.assertTrue(group_three_entered.wait(0.5))
            self.assertTrue(source.wait_for(
                lambda state: state.get("group_samples", {}).get("1", {}).get("timestamp") is not None,
                0.5,
            ))
            time.sleep(1.1)
            release_group_three.set()
            self.assertTrue(second_group_one_entered.wait(0.5))
            self.assertTrue(source.wait_for(
                lambda state: state.get("group_samples", {}).get("3", {}).get("timestamp", 0)
                > state.get("group_samples", {}).get("1", {}).get("timestamp", float("inf")),
                0.5,
            ))
            snapshot = source.snapshot()
            self.assertEqual(snapshot["group_samples"]["1"]["freshness"], "stale")
            self.assertEqual(snapshot["group_samples"]["3"]["freshness"], "fresh")
            self.assertEqual(snapshot["value_freshness"]["g1"], "stale")
            self.assertEqual(snapshot["value_freshness"]["g3"], "fresh")
            self.assertEqual(snapshot["value_metadata"]["g1"]["group_id"], 1)
            self.assertEqual(
                snapshot["value_metadata"]["g1"]["timestamp"],
                snapshot["group_samples"]["1"]["timestamp"],
            )
        finally:
            release_group_three.set()
            release_second_group_one.set()
            source.stop()

    def test_fatal_worker_error_prevents_monitor_start_after_identity_remains(self):
        class FatalKeepAliveSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def keep_alive(self, transport):
                raise DiagnosticError("fatal_keepalive", "session is no longer usable")

        source = self.make_source(FatalKeepAliveSession)
        try:
            self.assertTrue(source.wait_for(lambda state: state["status"] == "error", 0.5))
            source._thread.join(timeout=0.5)
            self.assertFalse(source._thread.is_alive())
            self.assertIsNotNone(source.snapshot()["ecu"])
            with self.assertRaisesRegex(ValueError, "sessão.*ativa"):
                source.start_monitor([1], min_period_s=0.5)
        finally:
            source.stop()

    def test_monitor_requires_identification_and_rejects_unapproved_or_duplicate_groups(self):
        source = DiagnosticSource(lambda: ReplayTransport([]), lambda: None)
        with self.assertRaisesRegex(ValueError, "identificação"):
            source.start_monitor([1], min_period_s=0.5)

        source._state["ecu"] = {"part_number": "038906018AF"}
        with self.assertRaisesRegex(ValueError, "001, 003 ou 011"):
            source.start_monitor([42], min_period_s=0.5)
        with self.assertRaisesRegex(ValueError, "repitas"):
            source.start_monitor([1, 1], min_period_s=0.5)
        with self.assertRaisesRegex(ValueError, "intervalo"):
            source.start_monitor([1], min_period_s=0.1)

    def test_manual_one_shot_cannot_be_replaced_by_monitor(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingSession:
            def identify(self, transport):
                return EcuIdentity(part_number="038906018AF")

            def request_group(self, transport, group):
                entered.set()
                release.wait(1.0)
                return []

            def keep_alive(self, transport):
                pass

        source = self.make_source(BlockingSession)
        try:
            source.set_groups([1])
            self.assertTrue(entered.wait(0.5))
            with self.assertRaisesRegex(ValueError, "leitura pontual"):
                source.start_monitor([3], min_period_s=0.5)
        finally:
            release.set()
            source.stop()


if __name__ == "__main__":
    unittest.main()
