import json
import threading
import time
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.request import build_opener, HTTPCookieProcessor
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from server import Engine, FISServer, WEB, EXAMPLE, find_active_session_url, resolve_application_root


class FakeDiagnosticSource:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.groups = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def set_groups(self, groups):
        self.groups = list(groups)

    def snapshot(self):
        return {
            "status": "live" if self.started and not self.stopped else "stopped",
            "values": {},
            "ecu": {"part_number": "038906018"},
            "freshness": "fresh" if self.started and not self.stopped else "unavailable",
            "source": "live",
            "last_update": 1.0,
            "error": None,
            "groups": {str(group): "fresh" for group in self.groups},
            "group_errors": {},
            "attempts": {str(group): 1 for group in self.groups},
        }


class IdentifyingDiagnosticSource(FakeDiagnosticSource):
    def snapshot(self):
        state = super().snapshot()
        state["status"] = "identifying"
        state["ecu"] = None
        return state


class StaleDiagnosticSource(FakeDiagnosticSource):
    def __init__(self):
        super().__init__()
        self.freshness = "fresh"

    def snapshot(self):
        state = super().snapshot()
        state["values"] = {"rpm": 900.0}
        state["freshness"] = self.freshness
        state["status"] = "live" if self.freshness == "fresh" else "stale"
        return state


class Tests(unittest.TestCase):
    def test_find_active_session_url_returns_live_authenticated_url(self):
        e = Engine()
        server = FISServer(("127.0.0.1", 0), e, "current-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            path = Path("unused-session-file")
            url = f"http://127.0.0.1:{server.server_port}/?k=current-token"
            with patch.object(Path, "read_text", return_value=url):
                self.assertEqual(url, find_active_session_url(path))
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_find_active_session_url_ignores_expired_token(self):
        e = Engine()
        server = FISServer(("127.0.0.1", 0), e, "current-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            path = Path("unused-session-file")
            url = f"http://127.0.0.1:{server.server_port}/?k=old-token"
            with patch.object(Path, "read_text", return_value=url):
                self.assertIsNone(find_active_session_url(path, attempts=1))
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_server_disables_address_reuse_to_keep_auth_tokens_isolated(self):
        self.assertFalse(FISServer.allow_reuse_address)

    def test_source_application_root_uses_server_location(self):
        root = resolve_application_root("project/server.py", frozen=False)

        self.assertEqual(Path("project/server.py").resolve().parent, root)

    def test_frozen_application_root_uses_executable_location(self):
        root = resolve_application_root(
            "temporary/server.py",
            frozen=True,
            executable_file="portable/FIS AFN Universal.exe",
        )

        self.assertEqual(Path("portable/FIS AFN Universal.exe").resolve().parent, root)

    def test_label_catalog_api_exposes_metadata_without_expanding_physical_groups(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "label_integration"
        e = Engine(labels_root=fixture_root)
        try:
            payload = e.diagnostic_catalog(query="G28", group_id=1, page=1, page_size=10)
            groups = {item["id"]: item for item in payload["groups"]}

            self.assertEqual(payload["summary"]["files_scanned"], 1)
            self.assertEqual(payload["summary"]["metadata_rows"], 4)
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["items"][0]["status"], "METADATA_ONLY")
            self.assertIsNone(payload["items"][0]["decoder_id"])
            self.assertFalse(payload["items"][0]["live_readable"])
            self.assertEqual(groups[1]["status"], "ACTIVE_ALLOWED")
            self.assertTrue(groups[1]["physical_read_allowed"])
            self.assertEqual(groups[2]["status"], "METADATA_ONLY")
            self.assertFalse(groups[2]["physical_read_allowed"])
            self.assertEqual(groups[4]["status"], "UNKNOWN")
            self.assertEqual(len(groups), 256)
        finally:
            e.stop.set()

    def test_http_label_catalog_is_authorized_and_searchable(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "label_integration"
        e = Engine(labels_root=fixture_root)
        server = FISServer(("127.0.0.1", 0), e, "secret")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(f"http://127.0.0.1:{server.server_port}/api/diagnostic/catalog", timeout=2)
            self.assertEqual(unauthorized.exception.code, 403)
            unauthorized.exception.close()

            url = f"http://127.0.0.1:{server.server_port}/api/diagnostic/catalog?k=secret&q=G28&group=1"
            payload = json.loads(urlopen(url, timeout=3).read())
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["items"][0]["source_file"], "engine.lbl")
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_diagnostic_connect_requires_an_explicit_port_by_default(self):
        e = Engine()
        try:
            with self.assertRaisesRegex(ValueError, "porta"):
                e.diagnostic_connect()
        finally:
            e.stop.set()

    def test_serial_diagnostic_connect_uses_selected_post_init_baudrate(self):
        for baudrate in (4800, 9600, 10400):
            source = FakeDiagnosticSource()
            e = Engine()
            try:
                with patch("server.create_serial_diagnostic_source", return_value=source) as create:
                    e.diagnostic_connect("COM7", baudrate=baudrate)
                self.assertEqual(create.call_args.kwargs["baudrate"], baudrate)
                self.assertTrue(source.started)
            finally:
                e.stop.set()

    def test_serial_diagnostic_connect_defaults_to_9600_for_vag_kw1281(self):
        source = FakeDiagnosticSource()
        e = Engine()
        try:
            with patch("server.create_serial_diagnostic_source", return_value=source) as create:
                e.diagnostic_connect("COM7")
            self.assertEqual(create.call_args.kwargs["baudrate"], 9600)
            self.assertTrue(source.started)
        finally:
            e.stop.set()

    def test_diagnostic_connect_rejects_unsupported_post_init_baudrate(self):
        e = Engine()
        try:
            with patch("server.create_serial_diagnostic_source") as create:
                with self.assertRaisesRegex(ValueError, "baud rate"):
                    e.diagnostic_connect("COM7", baudrate=5000)
            create.assert_not_called()
        finally:
            e.stop.set()

    def test_http_diagnostic_connect_forwards_post_init_baudrate(self):
        class BaudCaptureEngine(Engine):
            def diagnostic_connect(self, device=None, groups=None, baudrate=10400):
                self.connect_args = (device, groups, baudrate)
                return {"mode": "diagnostic"}

        e = BaudCaptureEngine()
        server = FISServer(("127.0.0.1", 0), e, "secret")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/connect?k=secret",
                data=json.dumps({"device": "COM7", "baudrate": 9600}).encode(),
                headers={"Content-Type": "application/json"},
            )
            payload = json.loads(urlopen(request, timeout=2).read())
            self.assertEqual(payload["mode"], "diagnostic")
            self.assertEqual(e.connect_args, ("COM7", None, 9600))
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_http_diagnostic_connect_defaults_to_9600_when_baud_is_omitted(self):
        class BaudCaptureEngine(Engine):
            def diagnostic_connect(self, device=None, groups=None, baudrate=10400):
                self.connect_args = (device, groups, baudrate)
                return {"mode": "diagnostic"}

        e = BaudCaptureEngine()
        server = FISServer(("127.0.0.1", 0), e, "secret")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/connect?k=secret",
                data=json.dumps({"device": "COM7"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            payload = json.loads(urlopen(request, timeout=2).read())
            self.assertEqual(payload["mode"], "diagnostic")
            self.assertEqual(e.connect_args, ("COM7", None, 9600))
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_csv_and_engine(self):
        e=Engine()
        try:
            res=e.upload(EXAMPLE.read_bytes(), 'demo.csv')
            self.assertEqual(res['samples'], 90)
            self.assertEqual(e.state()['mode'], 'replay')
            e._set({'rpm': 3200, 'boost_actual_mbar':1950, 'something_fake': 100})
            self.assertEqual(e.state()['values']['rpm'], 3200)
            self.assertNotIn('something_fake',e.state()['values'])
            e.recording=True
            e._set({'rpm': 3100,'boost_actual_mbar':1820})
            self.assertIn(b'boost_actual_mbar',e.export_csv())
            with self.assertRaises(ValueError):e.select('write_eeprom')
        finally:
            e.stop.set()

    def test_http(self):
        e=Engine(); server=FISServer(('127.0.0.1',0),e,'secret'); t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            with self.assertRaises(HTTPError) as ctx:urlopen(base+'/api/state',timeout=2)
            self.assertEqual(ctx.exception.code,403)
            payload=json.loads(urlopen(base+'/api/state?k=secret',timeout=2).read())
            self.assertEqual(payload['mode'],'idle')
            self.assertIn("source", payload)
            self.assertIn("freshness", payload)
            self.assertIn("error", payload)
            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            page = opener.open(base+'/?k=secret',timeout=2)
            self.assertIn(b'AFN',page.read())
            self.assertEqual(page.getheader("Set-Cookie").split("; ")[0], "fis_auth=secret")
            css = opener.open(base+'/style.css',timeout=2)
            script = opener.open(base+'/app.js',timeout=2)
            self.assertEqual(css.status, 200)
            self.assertEqual(css.headers.get_content_type(), "text/css")
            self.assertEqual(script.status, 200)
            self.assertIn("javascript", script.headers.get_content_type())
            req=Request(base+'/api/mode?k=secret',data=json.dumps({'mode':'demo'}).encode(),headers={'Content-Type':'application/json'})
            self.assertEqual(json.loads(urlopen(req,timeout=2).read())['mode'],'demo')
        finally:
            server.shutdown();server.server_close();e.stop.set()

    def test_http_lists_diagnostic_ports_with_get(self):
        class PortListingEngine(Engine):
            def diagnostic_ports(self):
                return [{"device": "COM7", "description": "FT232R USB UART"}]

        e = PortListingEngine()
        server = FISServer(("127.0.0.1", 0), e, "secret")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/diagnostic/ports?k=secret"
            payload = json.loads(urlopen(url, timeout=2).read())
            self.assertEqual(payload["ports"][0]["device"], "COM7")
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_http_lists_only_enabled_diagnostic_groups(self):
        e = Engine()
        server = FISServer(("127.0.0.1", 0), e, "secret")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/diagnostic/groups?k=secret"
            payload = json.loads(urlopen(url, timeout=2).read())
            self.assertEqual([group["id"] for group in payload["groups"]], [1, 3, 11])
            self.assertTrue(all(group["provisional"] for group in payload["groups"]))
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_diagnostic_lifecycle_is_injected_and_read_only(self):
        source = FakeDiagnosticSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            e.diagnostic_connect()
            self.assertEqual(e.state()["mode"], "diagnostic")
            self.assertEqual(e.state()["diagnostic"]["status"], "live")
            e.diagnostic_disconnect()
            self.assertEqual(e.state()["diagnostic"]["status"], "stopped")
        finally:
            e.stop.set()

    def test_diagnostic_connect_starts_identification_only_and_reads_one_group_explicitly(self):
        source = FakeDiagnosticSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            e.diagnostic_connect()
            self.assertEqual(source.groups, [])
            e.diagnostic_read_group(1)
            self.assertEqual(source.groups, [1])
            with self.assertRaisesRegex(ValueError, "um grupo"):
                e.diagnostic_read_group([1, 3])
            with self.assertRaisesRegex(ValueError, "não disponível"):
                e.diagnostic_read_group(42)
        finally:
            e.stop.set()

    def test_diagnostic_connect_rejects_groups_to_prevent_api_scan_bypass(self):
        e = Engine(diagnostic_source_factory=lambda: FakeDiagnosticSource())
        try:
            with self.assertRaisesRegex(ValueError, "identificação"):
                e.diagnostic_connect(groups=[1, 3])
        finally:
            e.stop.set()

    def test_diagnostic_read_group_rejects_until_ecu_identification_exists(self):
        source = IdentifyingDiagnosticSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            e.diagnostic_connect()
            with self.assertRaisesRegex(ValueError, "identificação"):
                e.diagnostic_read_group(1)
            self.assertEqual(source.groups, [])
        finally:
            e.stop.set()

    def test_diagnostic_monitor_api_is_explicit_and_allowlisted(self):
        class MonitorSource(FakeDiagnosticSource):
            def __init__(self):
                super().__init__()
                self.monitor = {"active": False, "status": "stopped"}
                self.started = None

            def start_monitor(self, groups, min_period_s=1.0):
                self.started = (tuple(groups), min_period_s)
                self.monitor = {"active": True, "status": "running"}

            def stop_monitor(self):
                self.monitor = {"active": False, "status": "stopped"}

            def snapshot(self):
                result = super().snapshot()
                result["monitor"] = dict(self.monitor)
                return result

        source = MonitorSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        server = FISServer(("127.0.0.1", 0), e, "secret")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            e.diagnostic_connect()
            self.assertFalse(source.monitor["active"])

            payload = json.dumps({"groups": [11, 1, 3], "min_period_s": 2.0}).encode()
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/monitor/start?k=secret",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            response = json.loads(urlopen(request, timeout=2).read())
            self.assertEqual(source.started, ((1, 3, 11), 2.0))
            self.assertTrue(response["diagnostic"]["monitor"]["active"])

            bad_payload = json.dumps({"groups": [1, 42]}).encode()
            bad_request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/monitor/start?k=secret",
                data=bad_payload,
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(bad_request, timeout=2)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

            stop_request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/monitor/stop?k=secret",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            stopped = json.loads(urlopen(stop_request, timeout=2).read())
            self.assertFalse(stopped["diagnostic"]["monitor"]["active"])
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_monitor_requires_identification_and_physical_allowlist(self):
        class ToggleIdentitySource(FakeDiagnosticSource):
            def __init__(self):
                super().__init__()
                self.identified = False

            def snapshot(self):
                result = super().snapshot()
                if not self.identified:
                    result["ecu"] = None
                return result

            def start_monitor(self, groups, min_period_s=1.0):
                self.started_groups = tuple(groups)

            def stop_monitor(self):
                pass

        source = ToggleIdentitySource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            with self.assertRaisesRegex(ValueError, "identificação"):
                e.diagnostic_start_monitor([1])
            source.identified = True
            with self.assertRaisesRegex(ValueError, "001, 003 ou 011"):
                e.diagnostic_start_monitor([1, 99])
            with self.assertRaisesRegex(ValueError, "intervalo"):
                e.diagnostic_start_monitor([1], min_period_s=0.1)
        finally:
            e.stop.set()

    def test_monitor_api_rejects_identified_but_failed_source(self):
        class FailedSource(FakeDiagnosticSource):
            def __init__(self):
                super().__init__()
                self.monitor_start_called = False

            def snapshot(self):
                result = super().snapshot()
                result.update(status="error", session_active=False)
                return result

            def start_monitor(self, groups, min_period_s=1.0):
                self.monitor_start_called = True

        source = FailedSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            e.diagnostic_connect()
            with self.assertRaisesRegex(ValueError, "sessão.*ativa"):
                e.diagnostic_start_monitor([1])
            self.assertFalse(source.monitor_start_called)
        finally:
            e.stop.set()

    def test_http_exports_bounded_diagnostic_trace(self):
        e = Engine()
        server = FISServer(("127.0.0.1", 0), e, "secret")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            e.diagnostic_trace.record("rx", "identify", b"\x55")
            url = f"http://127.0.0.1:{server.server_port}/api/diagnostic/trace?k=secret"
            response = urlopen(url, timeout=2)
            self.assertIn("application/json", response.headers["Content-Type"])
            payload = json.loads(response.read())
            self.assertEqual(payload["schema"], "fis-afn-diagnostic-trace/v1")
            self.assertEqual(payload["events"][0]["data_hex"], "55")
        finally:
            server.shutdown()
            server.server_close()
            e.stop.set()

    def test_dashboard_declares_diagnostic_mode_and_truthful_state_fields(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-mode="diagnostic"', html)
        self.assertIn('id="diagnostic-port"', html)
        self.assertIn("diagnostic/ports", script)
        self.assertIn("freshness", script)
        self.assertIn("diagnostic", script)
        self.assertIn('id="diagnostic-groups"', html)
        self.assertIn("diagnostic/groups", script)
        self.assertIn('id="diagnostic-details"', html)
        self.assertIn("retry_count", script)

    def test_engine_clears_diagnostic_values_when_source_is_not_fresh(self):
        source = StaleDiagnosticSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            e.diagnostic_connect()
            deadline = time.time() + 1
            while time.time() < deadline and "rpm" not in e.state()["values"]:
                time.sleep(0.01)
            source.freshness = "stale"
            deadline = time.time() + 1
            while time.time() < deadline and e.state()["values"]:
                time.sleep(0.01)
            self.assertEqual(e.state()["values"], {})
        finally:
            e.stop.set()

    def test_engine_keeps_stale_group_values_out_of_current_snapshot(self):
        source = FakeDiagnosticSource()
        e = Engine()
        try:
            with e.lock:
                e.mode = "diagnostic"
                e.diagnostic_source = source
            e._apply_diagnostic_snapshot(source, {
                "status": "live",
                "values": {"rpm": 900.0, "maf_actual_mg": 410.0},
                "value_freshness": {"rpm": "stale", "maf_actual_mg": "fresh"},
                "freshness": "fresh",
                "last_update": 1.0,
            })
            self.assertEqual(e.state()["values"], {"maf_actual_mg": 410.0})
        finally:
            e.stop.set()

    def test_engine_drops_snapshot_when_source_is_switched_before_apply(self):
        snapshot_taken = threading.Event()
        release = threading.Event()

        class RacingSource(FakeDiagnosticSource):
            calls = 0

            def snapshot(self):
                self.calls += 1
                if self.calls == 1:
                    return super().snapshot()
                snapshot_taken.set()
                release.wait(1.0)
                return {
                    "status": "live",
                    "values": {"rpm": 900.0},
                    "ecu": None,
                    "freshness": "fresh",
                    "source": "live",
                    "last_update": 1.0,
                    "error": None,
                }

        source = RacingSource()
        e = Engine(diagnostic_source_factory=lambda: source)
        try:
            e.diagnostic_connect()
            self.assertTrue(snapshot_taken.wait(1.0))
            e.select("idle")
            release.set()
            deadline = time.time() + 1.0
            while time.time() < deadline and not e.state()["history"]:
                time.sleep(0.01)
            self.assertEqual(e.state()["values"], {})
            self.assertEqual(e.state()["history"], [])
        finally:
            e.stop.set()

    def test_engine_does_not_apply_snapshot_from_an_inactive_source(self):
        source = FakeDiagnosticSource()
        e = Engine()
        try:
            with e.lock:
                e.mode = "diagnostic"
                e.diagnostic_source = source
            with e.lock:
                e.diagnostic_source = None
            e._apply_diagnostic_snapshot(source, {
                "values": {"rpm": 900.0},
                "freshness": "fresh",
                "last_update": 1.0,
            })
            self.assertEqual(e.state()["values"], {})
            self.assertEqual(e.state()["history"], [])
        finally:
            e.stop.set()

if __name__=='__main__':unittest.main()
