"""FIS AFN Universal — único painel web PC + Android (via browser).
Servidor local, sem bibliotecas externas. Não realiza comandos de escrita na ECU.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import mimetypes
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from bridge_client import read_stream
from diagnostic_models import DiagnosticError
from diagnostic_monitor import DiagnosticTrace
from diagnostic_source import (
    MONITOR_DEFAULT_PERIOD_S,
    create_serial_diagnostic_source,
    validate_monitor_groups,
    validate_monitor_period,
)
from diagnostic_groups import SUPPORTED_GROUP_IDS, default_afn_groups
from label_catalog import scan_labels
from label_catalog_import import JsonImportHistoryStore, LabelCatalogImporter
from label_catalog_integration import LabelMetadataCatalog, LabelQueryError, MAX_QUERY_CHARS
from kline_transport import discover_ports
from vcds_parser import load_log, parse_text

def resolve_application_root(
    script_file: str | Path,
    *,
    frozen: bool | None = None,
    executable_file: str | Path | None = None,
) -> Path:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if is_frozen:
        executable = executable_file if executable_file is not None else sys.executable
        return Path(executable).resolve().parent
    return Path(script_file).resolve().parent


ROOT = resolve_application_root(__file__)
WEB = ROOT / "web"
EXAMPLE = ROOT / "exemplos" / "LOG_DEMO_003_011_001.CSV"
ACTIVE_SESSION_URL = ROOT / "data" / "fis-active-session.url"
FIELDS = ("rpm", "boost_actual_mbar", "boost_requested_mbar", "maf_actual_mg", "maf_requested_mg", "iq_mg", "coolant_c", "n75_percent", "atmospheric_mbar", "fuel_temp_c")
MAX_BYTES = 12_000_000


class Engine:
    def __init__(
        self,
        diagnostic_source_factory=None,
        *,
        labels_root: Path | None = None,
        label_history_path: Path | None = None,
    ):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.mode = "idle"
        self.status = "Pronto — sem ligação à ECU"
        self.filename = ""
        self.values: dict[str, float] = {}
        self.history = deque(maxlen=150)
        self.recording = False
        self.recorded = []
        self.row_index = 0
        self.log = None
        self.last_update = None
        self.diagnostic_source_factory = diagnostic_source_factory
        self.labels_root = Path(labels_root) if labels_root is not None else ROOT / "Labels"
        self.label_history_path = (
            Path(label_history_path) if label_history_path is not None
            else ROOT / "data" / "label-import-history.v1.json"
        )
        self._label_catalog_lock = threading.Lock()
        self._label_catalog_cache = None
        self.diagnostic_source = None
        self.diagnostic_trace = DiagnosticTrace()
        self.diagnostic_state = {
            "status": "unavailable", "values": {}, "ecu": None, "freshness": "unavailable",
            "source": "live", "last_update": None, "error": None,
        }
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _set(self, values: dict, ts: float | None = None):
        filtered = {}
        for k, v in values.items():
            if k in FIELDS:
                try:
                    v = float(v)
                    if math.isfinite(v):
                        filtered[k] = v
                except (ValueError, TypeError):
                    pass
        if not filtered:
            return
        with self.lock:
            self.values = filtered
            self.last_update = time.time()
            entry = {"t": ts if ts is not None else self.last_update, **filtered}
            self.history.append(entry)
            if self.recording and self.mode not in ("idle",):
                self.recorded.append({"epoch": self.last_update, "mode": self.mode, **filtered})
                if len(self.recorded) > 100_000:
                    self.recording = False
                    self.status = "Gravação parada: limite de 100 000 amostras"

    def _apply_diagnostic_snapshot(self, source, snapshot):
        with self.lock:
            if self.mode != "diagnostic" or self.diagnostic_source is not source:
                return
            self.status = str(snapshot.get("status", "diagnóstico"))[:220]
            values = snapshot.get("values") or {}
            if values and snapshot.get("freshness") == "fresh":
                freshness = snapshot.get("value_freshness") or {}
                current_values = {
                    key: value for key, value in values.items()
                    if freshness.get(key, "fresh") == "fresh"
                }
                if current_values:
                    self._set(current_values, snapshot.get("last_update"))
                else:
                    self.values = {}
                    self.last_update = None
            else:
                self.values = {}
                self.last_update = None

    def state(self):
        with self.lock:
            diagnostic = self.diagnostic_source.snapshot() if self.diagnostic_source else dict(self.diagnostic_state)
            self.diagnostic_state = dict(diagnostic)
            source = diagnostic.get("source") if self.mode == "diagnostic" else self.mode
            freshness = diagnostic.get("freshness") if self.mode == "diagnostic" else (
                "fresh" if self.last_update is not None else "unavailable"
            )
            return {"mode": self.mode, "status": self.status, "source": source,
                    "freshness": freshness, "ecu": diagnostic.get("ecu"),
                    "error": diagnostic.get("error"), "values": self.values.copy(), "history": list(self.history),
                    "recording": self.recording, "recorded": len(self.recorded), "file": self.filename,
                    "samples": len(self.log.samples) if self.log else 0, "position": self.row_index,
                    "last_update": self.last_update, "diagnostic": diagnostic}

    def select(self, mode):
        if mode not in ("idle", "demo", "replay", "bridge", "diagnostic"):
            raise ValueError("Modo desconhecido")
        if mode != "diagnostic":
            self.diagnostic_disconnect()
        with self.lock:
            if mode == "replay" and not self.log:
                raise ValueError("Carrega primeiro um CSV VCDS")
            self.mode = mode
            self.row_index = 0
            self.values = {}
            self.history.clear()
            self.last_update = None
            self.status = {"idle": "Parado — sem ligação à ECU", "demo": "Demonstração • dados fictícios",
                           "replay": "Reprodução de CSV • dados gravados",
                           "bridge": "A estabelecer ligação à ponte K-Line experimental…",
                           "diagnostic": "A preparar diagnóstico K-Line somente leitura…"}[mode]

    def diagnostic_connect(self, device=None, groups=None, baudrate=9600):
        if type(baudrate) is not int or baudrate not in (4800, 9600, 10400):
            raise ValueError("baud rate pós-inicialização inválido; escolhe 4800, 9600 ou 10400.")
        selected_groups = tuple(groups) if groups is not None else ()
        if selected_groups:
            raise ValueError("A ligação começa apenas pela identificação; seleciona o grupo depois.")
        self.diagnostic_disconnect()
        session_id = self.diagnostic_trace.begin_session(
            str(device or ""),
            baudrate,
            local_echo=True,
        )
        if self.diagnostic_source_factory is not None:
            source = self.diagnostic_source_factory()
            if hasattr(source, "set_groups"):
                source.set_groups(selected_groups)
        else:
            source = create_serial_diagnostic_source(
                str(device or ""), groups=selected_groups, trace=self.diagnostic_trace,
                baudrate=baudrate,
            )
        with self.lock:
            self.diagnostic_source = source
            self.mode = "diagnostic"
            self.status = "A estabelecer sessão de diagnóstico somente leitura…"
            self.values = {}
            self.history.clear()
            self.last_update = None
        self.diagnostic_trace.record(
            "state", "identify", result="started", session_id=session_id,
        )
        source.start()
        return self.state()

    def diagnostic_read_group(self, group):
        if isinstance(group, (list, tuple)):
            selected = list(group)
        else:
            selected = [group]
        if len(selected) != 1:
            raise ValueError("Seleciona exatamente um grupo para a primeira leitura.")
        if type(selected[0]) is not int or selected[0] not in SUPPORTED_GROUP_IDS:
            raise ValueError("Grupo não disponível; usa 001, 003 ou 011.")
        with self.lock:
            source = self.diagnostic_source
        if source is None or self.mode != "diagnostic":
            raise ValueError("Inicia primeiro a identificação da ECU.")
        snapshot = source.snapshot()
        if not snapshot.get("ecu"):
            raise ValueError("Confirma primeiro a identificação da ECU.")
        setter = getattr(source, "set_groups", None)
        if setter is None:
            raise ValueError("A fonte atual não permite leitura incremental.")
        setter(selected)
        return self.state()

    def diagnostic_start_monitor(self, groups, min_period_s=MONITOR_DEFAULT_PERIOD_S):
        selected = validate_monitor_groups(groups)
        period = validate_monitor_period(min_period_s)
        with self.lock:
            source = self.diagnostic_source
            mode = self.mode
        if source is None or mode != "diagnostic":
            raise ValueError("Inicia primeiro a identificação da ECU.")
        snapshot = source.snapshot()
        if not snapshot.get("ecu"):
            raise ValueError("Confirma primeiro a identificação da ECU.")
        session_active = snapshot.get("session_active")
        if session_active is None:
            session_active = snapshot.get("status") in {"identified", "live"}
        if not session_active:
            raise ValueError("A sessão da ECU não está ativa; volta a identificar a ECU.")
        starter = getattr(source, "start_monitor", None)
        if starter is None:
            raise ValueError("A fonte atual não permite monitorização contínua.")
        starter(selected, min_period_s=period)
        return self.state()

    def diagnostic_stop_monitor(self):
        with self.lock:
            source = self.diagnostic_source
            mode = self.mode
        if source is None or mode != "diagnostic":
            raise ValueError("Inicia primeiro a identificação da ECU.")
        stopper = getattr(source, "stop_monitor", None)
        if stopper is None:
            raise ValueError("A fonte atual não permite monitorização contínua.")
        stopper()
        return self.state()

    def diagnostic_disconnect(self):
        with self.lock:
            source = self.diagnostic_source
            if source is not None:
                self.diagnostic_state = source.snapshot()
            self.diagnostic_source = None
        if source is not None:
            self.diagnostic_trace.record("state", "session_disconnect", result="requested")
            source.stop()
            self.diagnostic_trace.record("state", "session_disconnect", result="returned")
        with self.lock:
            if source is not None:
                self.diagnostic_state = source.snapshot()
                self.diagnostic_state.update(status="stopped", freshness="unavailable")
            if self.mode == "diagnostic":
                self.mode = "idle"
                self.status = "Parado — sem ligação à ECU"

    def diagnostic_trace_export(self, fmt="json"):
        if fmt == "csv":
            return self.diagnostic_trace.export_csv(), "text/csv; charset=utf-8"
        if fmt != "json":
            raise ValueError("Formato de trace inválido.")
        return self.diagnostic_trace.export_json(), "application/json; charset=utf-8"

    def upload(self, raw: bytes, name: str):
        if len(raw) > MAX_BYTES:
            raise ValueError("CSV demasiado grande (máx. 12 MB)")
        last_error = None
        for enc in ("utf-8-sig", "cp1252"):
            try:
                log = parse_text(raw.decode(enc), name)
                if not log.samples:
                    raise ValueError("CSV sem amostras válidas")
                with self.lock:
                    self.log = log
                    self.filename = Path(name).name[:120]
                    self.row_index = 0
                self.select("replay")
                return {"samples": len(log.samples), "fields": list(log.mapping), "filename": self.filename}
            except UnicodeDecodeError as e:
                last_error = e
            except ValueError:
                raise
        raise ValueError(f"Codificação CSV inválida: {last_error}")

    def _loop(self):
        phase = 0
        bridge_thread = None
        bridge_stop = threading.Event()
        prev_mode = None
        while not self.stop.is_set():
            with self.lock:
                mode, log, index = self.mode, self.log, self.row_index
            if prev_mode != mode:
                bridge_stop.set()
                bridge_stop = threading.Event()
                bridge_thread = None
                if mode == "bridge":
                    def on_data(v):
                        with self.lock:
                            active = self.mode == "bridge"
                        if active:
                            self._set(v)
                    def on_status(s):
                        with self.lock:
                            if self.mode == "bridge":
                                self.status = str(s)[:220]
                    bridge_thread = threading.Thread(target=read_stream, args=(bridge_stop, on_data, on_status), daemon=True)
                    bridge_thread.start()
                prev_mode = mode
            if mode == "demo":
                # Dados fictícios com valores absolutos plausíveis, não são leitura da ECU.
                rpm = 840 + 2750 * (0.5 + 0.5 * math.sin(phase * 0.055))
                ask = 1040 + max(0, rpm - 950) * 0.35
                boost = ask + math.sin(phase * 0.16) * 85
                self._set({"rpm": rpm, "boost_actual_mbar": boost, "boost_requested_mbar": ask,
                           "maf_actual_mg": 280 + (rpm - 840)*0.2, "maf_requested_mg": 310+(rpm-840)*0.2,
                           "iq_mg": 6+(rpm-840)*0.011, "coolant_c": 89.0, "n75_percent": 45+20*math.sin(phase*.05)})
                phase += 1
                self.stop.wait(0.5)
            elif mode == "replay" and log:
                if index < len(log.samples):
                    sample = log.samples[index]
                    self._set(log.mapped(sample), sample.time)
                    with self.lock:
                        if self.mode == "replay":
                            self.row_index = index + 1
                    next_time = log.samples[index+1].time if index+1 < len(log.samples) else sample.time+0.5
                    self.stop.wait(min(2.0, max(0.1, next_time-sample.time)))
                else:
                    with self.lock:
                        if self.mode == "replay":
                            self.status = "Reprodução terminada · inicia de novo para repetir"
                            self.mode = "finished"
                    self.stop.wait(0.2)
            elif mode == "diagnostic":
                source = self.diagnostic_source
                if source:
                    snapshot = source.snapshot()
                    self._apply_diagnostic_snapshot(source, snapshot)
                self.stop.wait(0.15)
            else:
                self.stop.wait(0.15)
        bridge_stop.set()

    def export_csv(self):
        with self.lock:
            rows = list(self.recorded)
        if not rows:
            raise ValueError("Ainda não existem amostras gravadas")
        dest = io.StringIO(newline="")
        wr = csv.DictWriter(dest, fieldnames=("epoch", "mode", *FIELDS))
        wr.writeheader()
        wr.writerows(rows)
        return dest.getvalue().encode("utf-8-sig")

    def diagnostic_ports(self):
        return [port.__dict__ for port in discover_ports()]

    def diagnostic_groups(self):
        return [
            {
                "id": group.group_id,
                "label": group.label,
                "provisional": group.provisional,
                "signals": [
                    {"key": signal.key, "label": signal.label, "unit": signal.unit}
                    for signal in group.signals
                ],
            }
            for group in default_afn_groups()
            if group.group_id in SUPPORTED_GROUP_IDS
        ]

    def diagnostic_catalog(
        self,
        *,
        query="",
        group_id=None,
        group_status=None,
        page=1,
        page_size=50,
        refresh=False,
    ):
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            raise LabelQueryError(f"A pesquisa tem de ter até {MAX_QUERY_CHARS} caracteres.")
        with self._label_catalog_lock:
            if refresh or self._label_catalog_cache is None:
                inventory = scan_labels(self.labels_root)
                catalog = LabelMetadataCatalog.from_inventory(inventory)
                summary = {
                    "source": "Labels/ local",
                    "files_scanned": len(inventory.files),
                    "files_loaded": sum(1 for document in inventory.files if document.loaded),
                    "metadata_rows": catalog.candidate_count,
                    "warning_count": len(inventory.warnings),
                    "warnings": list(inventory.warnings[:20]),
                }
                self._label_catalog_cache = (catalog, summary)
            catalog, summary = self._label_catalog_cache
        result = catalog.query(
            query=query,
            group_id=group_id,
            group_status=group_status,
            page=page,
            page_size=page_size,
        )
        result.update({
            "schema_version": 1,
            "summary": dict(summary),
            "groups": catalog.groups(),
        })
        return result

    def import_label_catalog(self):
        importer = LabelCatalogImporter(self.labels_root, JsonImportHistoryStore(self.label_history_path))
        with self._label_catalog_lock:
            catalog, report = importer.import_now()
            summary = {
                "source": "Labels/ local",
                "files_scanned": report["files_scanned"],
                "files_loaded": report["files_loaded"],
                "metadata_rows": report["metadata_rows"],
                "warning_count": report["warning_count"],
                "warnings": report["warnings"],
            }
            self._label_catalog_cache = (catalog, summary)
        return {"report": report, "summary": summary, "groups": catalog.groups()}


class FISServer(ThreadingHTTPServer):
    daemon_threads = True
    # A second process must not share this port with its own auth token.
    allow_reuse_address = False
    def __init__(self, addr, engine, token):
        super().__init__(addr, Handler)
        self.engine = engine
        self.token = token


def find_active_session_url(
    session_url_path: Path = ACTIVE_SESSION_URL,
    *,
    attempts: int = 1,
    retry_interval_s: float = 0.1,
) -> str | None:
    path = Path(session_url_path)
    for attempt in range(max(1, attempts)):
        try:
            url = path.read_text(encoding="utf-8").strip()
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            is_valid = (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.port is not None
                and parsed.path == "/"
                and bool(query.get("k", [""])[0])
            )
            if is_valid:
                with urlopen(url, timeout=0.5) as response:
                    if response.status == 200 and response.headers.get_content_type() == "text/html":
                        return url
        except HTTPError as exc:
            exc.close()
        except (OSError, URLError, ValueError):
            pass
        if attempt + 1 < max(1, attempts):
            time.sleep(retry_interval_s)
    return None


def write_active_session_url(session_url_path: Path, url: str) -> None:
    path = Path(session_url_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(url + "\n", encoding="utf-8")
    temporary_path.replace(path)


def remove_active_session_url(session_url_path: Path, url: str) -> None:
    path = Path(session_url_path)
    try:
        if path.read_text(encoding="utf-8").strip() == url:
            path.unlink()
    except FileNotFoundError:
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _authorized(self):
        query = parse_qs(urlsplit(self.path).query)
        if secrets.compare_digest(query.get("k", [""])[0], self.server.token):
            return True
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        cookie = cookies.get("fis_auth")
        return bool(cookie and secrets.compare_digest(cookie.value, self.server.token))

    def _write(self, code, body, typ="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for k, v in extra.items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._write(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        route = urlsplit(self.path).path
        if not self._authorized():
            return self._json(403, {"error":"Acesso não autorizado. Usa o endereço completo mostrado no PC."})
        if route == "/api/state":
            return self._json(200, self.server.engine.state())
        if route == "/api/diagnostic/ports":
            try:
                return self._json(200, {"ports": self.server.engine.diagnostic_ports()})
            except DiagnosticError as e:
                return self._json(503, {"error": e.message, "code": e.code})
        if route == "/api/diagnostic/groups":
            return self._json(200, {"groups": self.server.engine.diagnostic_groups()})
        if route == "/api/diagnostic/catalog":
            try:
                params = parse_qs(urlsplit(self.path).query)
                group_value = params.get("group", [None])[0]
                group_id = None if group_value in (None, "") else int(group_value, 10)
                page = int(params.get("page", ["1"])[0], 10)
                page_size = int(params.get("page_size", ["50"])[0], 10)
                refresh = params.get("refresh", ["0"])[0].casefold() in {"1", "true", "yes"}
                payload = self.server.engine.diagnostic_catalog(
                    query=params.get("q", [""])[0],
                    group_id=group_id,
                    group_status=params.get("status", [None])[0] or None,
                    page=page,
                    page_size=page_size,
                    refresh=refresh,
                )
                return self._json(200, payload)
            except (ValueError, TypeError) as e:
                return self._json(400, {"error": str(e)})
        if route == "/api/diagnostic/trace":
            try:
                fmt = parse_qs(urlsplit(self.path).query).get("format", ["json"])[0]
                data, typ = self.server.engine.diagnostic_trace_export(fmt)
                extension = "csv" if fmt == "csv" else "json"
                return self._write(
                    200,
                    data.encode("utf-8"),
                    typ,
                    {"Content-Disposition": f"attachment; filename=FIS_AFN_trace.{extension}"},
                )
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if route == "/api/export":
            try:
                data = self.server.engine.export_csv()
                return self._write(200, data, "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=FIS_AFN_registo.csv"})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if route == "/" : route = "/index.html"
        path = (WEB / route.lstrip("/")).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self._json(404, {"error": "Ficheiro não encontrado"})
        typ = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        extra = None
        if route == "/index.html":
            extra = {
                "Set-Cookie": f"fis_auth={self.server.token}; Path=/; HttpOnly; SameSite=Strict"
            }
        return self._write(200, path.read_bytes(), typ, extra)

    def do_POST(self):
        route = urlsplit(self.path).path
        if not self._authorized():
            return self._json(403, {"error": "Não autorizado"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BYTES or length < 0: raise ValueError("Tamanho inválido; máximo 12 MB")
            raw = self.rfile.read(length)
            if route == "/api/upload":
                name = self.headers.get("X-Filename", "log.csv")
                return self._json(200, self.server.engine.upload(raw, name))
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if route == "/api/mode":
                self.server.engine.select(str(data.get("mode", "idle")))
                return self._json(200, self.server.engine.state())
            if route == "/api/diagnostic/connect":
                return self._json(200, self.server.engine.diagnostic_connect(
                    data.get("device"), data.get("groups"), data.get("baudrate", 9600)
                ))
            if route == "/api/diagnostic/read-group":
                return self._json(200, self.server.engine.diagnostic_read_group(data.get("group")))
            if route == "/api/diagnostic/monitor/start":
                return self._json(200, self.server.engine.diagnostic_start_monitor(
                    data.get("groups", ()), data.get("min_period_s", MONITOR_DEFAULT_PERIOD_S)
                ))
            if route == "/api/diagnostic/monitor/stop":
                return self._json(200, self.server.engine.diagnostic_stop_monitor())
            if route == "/api/diagnostic/catalog/import":
                payload = self.server.engine.import_label_catalog()
                return self._json(200, payload)
            if route == "/api/diagnostic/disconnect":
                self.server.engine.diagnostic_disconnect()
                return self._json(200, self.server.engine.state())
            if route == "/api/diagnostic/ports":
                return self._json(200, {"ports": self.server.engine.diagnostic_ports()})
            if route == "/api/record":
                with self.server.engine.lock:
                    self.server.engine.recording = bool(data.get("active", False))
                return self._json(200, self.server.engine.state())
            if route == "/api/clear":
                with self.server.engine.lock:
                    self.server.engine.recording = False
                    self.server.engine.recorded.clear()
                return self._json(200, {"ok":True})
            return self._json(404, {"error": "Ação desconhecida"})
        except DiagnosticError as e:
            return self._json(503, {"error": e.message, "code": e.code})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 1))
        out = s.getsockname()[0]
        s.close()
        return out
    except OSError:
        return "IP_DO_PORTATIL"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="Partilhar a página na rede local com acesso por token")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    engine = Engine()
    token = secrets.token_urlsafe(16)
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = FISServer((host, args.port), engine, token)
    except OSError:
        engine.stop.set()
        existing_url = find_active_session_url(ACTIVE_SESSION_URL, attempts=10)
        if existing_url is None:
            raise
        print("\nFIS AFN UNIVERSAL já está aberta.", flush=True)
        print("Windows:", existing_url, flush=True)
        if not args.no_browser:
            webbrowser.open(existing_url, new=2)
        return
    url = f"http://127.0.0.1:{server.server_port}/?k={token}"
    write_active_session_url(ACTIVE_SESSION_URL, url)
    print("\nFIS AFN UNIVERSAL · LIGAÇÃO SOMENTE LEITURA", flush=True)
    print("Windows:", url, flush=True)
    if args.lan:
        print("Telemóvel (mesma rede Wi-Fi):", f"http://{lan_ip()}:{server.server_port}/?k={token}", flush=True)
        print("Não encaminhes esta porta na Internet. Para parar: Ctrl+C.\n", flush=True)
    if not args.no_browser: webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        engine.diagnostic_disconnect()
        engine.stop.set()
        server.server_close()
        remove_active_session_url(ACTIVE_SESSION_URL, url)


if __name__ == "__main__":
    main()
