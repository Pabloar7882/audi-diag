"""Controlled background diagnostic source with explicit lifecycle states."""

from __future__ import annotations

import threading
import time
import math
from typing import Callable, Iterable, Any

from diagnostic_models import DiagnosticError
from diagnostic_models import SourceKind
from diagnostic_groups import SUPPORTED_GROUP_IDS
from kline_transport import SerialKLineTransport
from vag_protocol import Kw1281Session
from kw1281_physical import Kw1281PhysicalSession
from diagnostic_monitor import DiagnosticTrace, MonitoredTransport


MONITOR_DEFAULT_PERIOD_S = 1.0
MONITOR_MIN_PERIOD_S = 0.5
MONITOR_MAX_PERIOD_S = 60.0


def _validated_groups(groups: Iterable[int]) -> tuple[int, ...]:
    selected = tuple(groups)
    if len(selected) > 1:
        raise ValueError("A fonte aceita apenas um grupo explícito por pedido.")
    if any(type(group) is not int or group not in SUPPORTED_GROUP_IDS for group in selected):
        raise ValueError("Grupo não disponível; usa 001, 003 ou 011.")
    return selected


def validate_monitor_groups(groups: Iterable[int]) -> tuple[int, ...]:
    try:
        selected = tuple(groups)
    except TypeError as exc:
        raise ValueError("Seleciona um ou mais grupos para monitorização.") from exc
    if not selected:
        raise ValueError("Seleciona um ou mais grupos para monitorização.")
    if any(type(group) is not int or group not in SUPPORTED_GROUP_IDS for group in selected):
        raise ValueError("Grupo não disponível; usa 001, 003 ou 011.")
    if len(set(selected)) != len(selected):
        raise ValueError("Não repitas grupos na monitorização.")
    return tuple(group for group in SUPPORTED_GROUP_IDS if group in selected)


def validate_monitor_period(period: float) -> float:
    if isinstance(period, bool) or not isinstance(period, (int, float)):
        raise ValueError("O intervalo de monitorização tem de ser numérico.")
    value = float(period)
    if not math.isfinite(value) or not MONITOR_MIN_PERIOD_S <= value <= MONITOR_MAX_PERIOD_S:
        raise ValueError(
            f"O intervalo de monitorização tem de estar entre {MONITOR_MIN_PERIOD_S:g} e "
            f"{MONITOR_MAX_PERIOD_S:g} segundos."
        )
    return value


def create_serial_diagnostic_source(
    device: str,
    *,
    transport_factory: Callable[[], Any] = SerialKLineTransport,
    session_factory: Callable[[], Any] = Kw1281PhysicalSession,
    groups: Iterable[int] = (),
    baudrate: int = 9600,
    timeout: float = 1.0,
    max_retries: int = 3,
    retry_backoff: float = 0.5,
    trace: DiagnosticTrace | None = None,
) -> "DiagnosticSource":
    if not device or not device.strip():
        raise ValueError("É necessário selecionar uma porta série.")

    def make_transport() -> Any:
        transport = transport_factory()
        transport.open(device, baudrate=baudrate, timeout=timeout)
        return transport

    return DiagnosticSource(
        make_transport,
        session_factory,
        groups,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        trace=trace,
    )


class DiagnosticSource:
    def __init__(
        self,
        transport_factory: Callable[[], Any],
        session_factory: Callable[[], Any],
        groups: Iterable[int] = (),
        *,
        sample_interval: float = 0.25,
        max_retries: int = 0,
        retry_backoff: float = 0.5,
        group_max_attempts: int = 1,
        trace: DiagnosticTrace | None = None,
    ):
        self._transport_factory = transport_factory
        self._session_factory = session_factory
        self._groups = _validated_groups(groups)
        self._groups_generation = 0
        self._sample_interval = sample_interval
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff))
        self._group_max_attempts = max(1, int(group_max_attempts))
        self.trace = trace or DiagnosticTrace()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._transport = None
        self._monitor_groups: tuple[int, ...] | None = None
        self._monitor_generation = 0
        self._monitor_cursor = 0
        self._monitor_period_s = MONITOR_DEFAULT_PERIOD_S
        self._monitor_last_completed: dict[int, float] = {}
        self._state: dict[str, Any] = {
            "status": "idle",
            "session_active": False,
            "values": {},
            "raw_values": {},
            "value_groups": {},
            "raw_value_groups": {},
            "ecu": None,
            "freshness": "unavailable",
            "source": "live",
            "last_update": None,
            "error": None,
            "retry_count": 0,
            "groups": {},
            "group_errors": {},
            "attempts": {},
            "group_timestamps": {},
            "group_latencies": {},
            "group_samples": {},
            "monitor": {
                "active": False,
                "status": "stopped",
                "groups": [],
                "min_period_s": None,
                "expected_cycle_s": None,
                "current_group": None,
                "last_group": None,
                "error": None,
            },
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._state.update(status="connecting", session_active=False, error=None)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._monitor_groups = None
            self._monitor_generation += 1
            self._state["session_active"] = False
            self._state["monitor"].update(
                active=False,
                status="stopped",
                current_group=None,
            )
            self._condition.notify_all()
            transport = self._transport
            thread = self._thread
        if transport is not None:
            close = getattr(transport, "close", None)
            if close:
                close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            self._state["status"] = "stopped"
            self._state["freshness"] = "unavailable"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._state)
            result["values"] = dict(self._state["values"])
            result["ecu"] = dict(self._state["ecu"]) if self._state["ecu"] else None
            result["groups"] = dict(self._state["groups"])
            result["group_errors"] = {
                key: dict(value) for key, value in self._state["group_errors"].items()
            }
            result["attempts"] = dict(self._state["attempts"])
            result["raw_values"] = dict(self._state["raw_values"])
            result["value_groups"] = dict(self._state["value_groups"])
            result["raw_value_groups"] = dict(self._state["raw_value_groups"])
            result["group_timestamps"] = dict(self._state["group_timestamps"])
            result["group_latencies"] = dict(self._state["group_latencies"])
            result["monitor"] = dict(self._state["monitor"])
            period = result["monitor"].get("min_period_s") or MONITOR_DEFAULT_PERIOD_S
            selected_groups = result["monitor"].get("groups") or []
            expected_cycle_s = len(selected_groups) * period if selected_groups else period
            stale_after_s = expected_cycle_s
            result["monitor"]["expected_cycle_s"] = expected_cycle_s
            now = time.time()
            session_active = bool(result.get("session_active"))
            group_samples: dict[str, dict[str, Any]] = {}
            group_freshness: dict[str, str] = {}
            value_freshness: dict[str, str] = {}
            value_timestamps: dict[str, float] = {}
            value_metadata: dict[str, dict[str, Any]] = {}
            raw_value_freshness: dict[str, str] = {}
            raw_value_timestamps: dict[str, float] = {}
            raw_value_metadata: dict[str, dict[str, Any]] = {}
            for key, sample in self._state["group_samples"].items():
                item = dict(sample)
                item["values"] = dict(sample["values"])
                item["raw_values"] = dict(sample["raw_values"])
                timestamp = sample["timestamp"]
                age_s = max(0.0, now - timestamp)
                item["age_s"] = age_s
                item["stale_after_s"] = stale_after_s
                item["freshness"] = (
                    "fresh" if session_active and age_s <= stale_after_s else "stale"
                )
                group_samples[key] = item
                group_freshness[key] = item["freshness"]
                for sensor_key in item["values"]:
                    if timestamp >= value_timestamps.get(sensor_key, float("-inf")):
                        value_freshness[sensor_key] = item["freshness"]
                        value_timestamps[sensor_key] = timestamp
                        value_metadata[sensor_key] = {
                            "group_id": int(key),
                            "timestamp": timestamp,
                            "freshness": item["freshness"],
                        }
                for sensor_key in item["raw_values"]:
                    if timestamp >= raw_value_timestamps.get(sensor_key, float("-inf")):
                        raw_value_freshness[sensor_key] = item["freshness"]
                        raw_value_timestamps[sensor_key] = timestamp
                        raw_value_metadata[sensor_key] = {
                            "group_id": int(key),
                            "timestamp": timestamp,
                            "freshness": item["freshness"],
                        }
            result["group_samples"] = group_samples
            result["group_freshness"] = group_freshness
            result["value_timestamps"] = value_timestamps
            result["value_freshness"] = value_freshness
            result["value_metadata"] = value_metadata
            result["raw_value_timestamps"] = raw_value_timestamps
            result["raw_value_freshness"] = raw_value_freshness
            result["raw_value_metadata"] = raw_value_metadata
            if result["freshness"] == "fresh" and group_freshness:
                result["freshness"] = (
                    "fresh" if "fresh" in group_freshness.values() else "stale"
                )
            return result

    def status(self) -> str:
        return str(self.snapshot()["status"])

    def set_groups(self, groups: Iterable[int]) -> None:
        selected = _validated_groups(groups)
        with self._condition:
            if not self._state["ecu"]:
                raise ValueError("Confirma primeiro a identificação da ECU.")
            if self._state["monitor"]["active"]:
                raise ValueError("Pára a monitorização antes de pedir uma leitura pontual.")
            self._groups = selected
            self._groups_generation += 1
            self._condition.notify_all()

    def start_monitor(
        self,
        groups: Iterable[int],
        min_period_s: float = MONITOR_DEFAULT_PERIOD_S,
    ) -> None:
        selected = validate_monitor_groups(groups)
        period = validate_monitor_period(min_period_s)
        with self._condition:
            if not self._state["ecu"]:
                raise ValueError("Confirma primeiro a identificação da ECU.")
            thread = self._thread
            if (
                not self._state["session_active"]
                or self._state["status"] not in {"identified", "live"}
                or self._stop.is_set()
                or thread is None
                or not thread.is_alive()
            ):
                raise ValueError("A sessão da ECU não está ativa; volta a identificar a ECU.")
            if self._state["monitor"]["active"]:
                raise ValueError("A monitorização contínua já está ativa; pára-a antes de reiniciar.")
            if self._groups:
                raise ValueError("Termina primeiro a leitura pontual pendente.")
            self._monitor_groups = selected
            self._monitor_generation += 1
            self._monitor_cursor = 0
            self._monitor_period_s = period
            for group in selected:
                self._state["group_errors"].pop(str(group), None)
                self._state["groups"][str(group)] = "pending"
            self._state["error"] = None
            self._state["monitor"] = {
                "active": True,
                "status": "running",
                "groups": list(selected),
                "min_period_s": period,
                "expected_cycle_s": len(selected) * period,
                "current_group": None,
                "last_group": None,
                "error": None,
            }
            self._condition.notify_all()

    def stop_monitor(self) -> None:
        with self._condition:
            self._monitor_groups = None
            self._monitor_generation += 1
            self._state["monitor"].update(
                active=False,
                status="stopped",
                current_group=None,
                error=None,
            )
            self._condition.notify_all()

    def _suspend_monitor(self, error: DiagnosticError, generation: int) -> None:
        with self._condition:
            if (
                generation != self._monitor_generation
                or not self._state["monitor"]["active"]
            ):
                return
            self._monitor_groups = None
            self._monitor_generation += 1
            self._state["monitor"].update(
                active=False,
                status="suspended",
                current_group=None,
                error=error.as_dict(),
            )
            self._condition.notify_all()

    def _wait_for_monitor_deadline(
        self,
        deadline: float,
        generation: int,
        group: int,
    ) -> bool:
        with self._condition:
            while not self._stop.is_set():
                if (
                    generation != self._monitor_generation
                    or not self._state["monitor"]["active"]
                    or not self._monitor_groups
                ):
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._state["monitor"]["current_group"] = group
                    return True
                self._condition.wait(timeout=remaining)
            return False

    def _wait_for_command(self, timeout: float) -> None:
        with self._condition:
            if not self._stop.is_set():
                self._condition.wait(timeout=max(0.0, timeout))

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.snapshot()):
                return True
            time.sleep(0.01)
        return predicate(self.snapshot())

    def _run(self) -> None:
        retry_count = 0
        while not self._stop.is_set():
            transport = None
            try:
                transport = self._transport_factory()
                with self._lock:
                    self._state["retry_count"] = retry_count
            except DiagnosticError as exc:
                if not exc.recoverable or retry_count >= self._max_retries:
                    self._set_error(exc)
                    return
                retry_count += 1
                self._mark_retry(retry_count, exc)
                self._stop.wait(self._retry_backoff * retry_count)
                continue
            try:
                self._run_session(transport)
                return
            except DiagnosticError as exc:
                if not exc.recoverable or retry_count >= self._max_retries:
                    self._set_error(exc)
                    return
                retry_count += 1
                self._mark_retry(retry_count, exc)
                self._close_transport(transport)
                self._stop.wait(self._retry_backoff * retry_count)
            except Exception as exc:
                self._set_error(DiagnosticError("source_failed", str(exc)))
                return
            finally:
                self._close_transport(transport)

    def _run_session(self, transport: Any) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._transport = MonitoredTransport(transport, self.trace, phase="kw1281")
            transport = self._transport
            self._state["status"] = "identifying"
            self._state["session_active"] = False
        session = self._session_factory()
        identity = session.identify(transport)
        with self._lock:
            if self._stop.is_set():
                return
            self._state["ecu"] = identity.as_dict()
            self._state["status"] = "identified"
            self._state["session_active"] = True
            self._state["freshness"] = "unavailable"
            self._state["last_update"] = None
        while not self._stop.is_set():
            values: dict[str, float] = {}
            raw_values: dict[str, str] = {}
            group_results: dict[str, str] = {}
            group_errors: dict[str, dict[str, Any]] = {}
            attempts: dict[str, int] = {}
            with self._lock:
                monitoring = bool(self._state["monitor"]["active"] and self._monitor_groups)
                monitor_groups = self._monitor_groups if monitoring else None
                monitor_generation = self._monitor_generation
                if monitor_groups:
                    group = monitor_groups[self._monitor_cursor % len(monitor_groups)]
                    last_completed = self._monitor_last_completed.get(group)
                    deadline = (
                        last_completed + self._monitor_period_s
                        if last_completed is not None else time.monotonic()
                    )
                    groups = (group,)
                    groups_generation = self._groups_generation
                else:
                    group = None
                    deadline = 0.0
                    groups = self._groups
                    groups_generation = self._groups_generation
            if monitoring and group is not None:
                if not self._wait_for_monitor_deadline(deadline, monitor_generation, group):
                    continue
            if not groups and hasattr(session, "keep_alive"):
                session.keep_alive(transport)
                self._wait_for_command(self._sample_interval)
                continue
            for group in groups:
                group_key = str(group)
                for attempt in range(1, self._group_max_attempts + 1):
                    attempts[group_key] = attempt
                    try:
                        if self._stop.is_set():
                            return
                        started_at = time.monotonic()
                        if hasattr(session, "request_group_values"):
                            cells = session.request_group_values(transport, group)
                            raw_values.update({
                                cell.key: cell.raw for cell in cells if cell.raw is not None
                            })
                            cells = [
                                cell for cell in cells
                                if cell.source in (SourceKind.LIVE, SourceKind.REPLAY)
                                and cell.value is not None
                            ]
                            cells = [{"label": cell.key, "value": cell.value} for cell in cells]
                        else:
                            cells = session.request_group(transport, group)
                            raw_values.update({
                                cell.key: cell.raw for cell in cells
                                if getattr(cell, "raw", None) is not None
                            })
                        completed_at = time.monotonic()
                        for cell in cells:
                            label = cell.get("label") if isinstance(cell, dict) else getattr(cell, "key", None)
                            value = cell.get("value") if isinstance(cell, dict) else getattr(cell, "value", None)
                            if isinstance(label, str) and isinstance(value, (int, float)):
                                values[label] = float(value)
                        sample_timestamp = time.time()
                        latency = max(0.0, completed_at - started_at)
                        with self._condition:
                            previous_sample = self._state["group_samples"].get(group_key, {})
                            previous_values = previous_sample.get("values", {})
                            previous_raw = previous_sample.get("raw_values", {})
                            for old_key in previous_values:
                                if (
                                    old_key not in values
                                    and self._state["value_groups"].get(old_key) == group_key
                                ):
                                    self._state["values"].pop(old_key, None)
                                    self._state["value_groups"].pop(old_key, None)
                            for old_key in previous_raw:
                                if (
                                    old_key not in raw_values
                                    and self._state["raw_value_groups"].get(old_key) == group_key
                                ):
                                    self._state["raw_values"].pop(old_key, None)
                                    self._state["raw_value_groups"].pop(old_key, None)
                            for sensor_key in values:
                                self._state["value_groups"][sensor_key] = group_key
                            for sensor_key in raw_values:
                                self._state["raw_value_groups"][sensor_key] = group_key
                            self._state["group_timestamps"][group_key] = sample_timestamp
                            self._state["group_latencies"][group_key] = latency
                            self._state["group_samples"][group_key] = {
                                "values": dict(values),
                                "raw_values": dict(raw_values),
                                "timestamp": sample_timestamp,
                                "latency_s": latency,
                            }
                            if monitoring:
                                self._state["monitor"]["last_group"] = group
                                self._monitor_last_completed[group] = completed_at
                                if monitor_generation == self._monitor_generation:
                                    self._state["monitor"]["current_group"] = None
                        group_results[group_key] = "fresh"
                        group_errors.pop(group_key, None)
                        break
                    except DiagnosticError as exc:
                        if exc.code == "timeout":
                            if monitoring:
                                self._suspend_monitor(exc, monitor_generation)
                            raise DiagnosticError(
                                "session_expired",
                                "KW1281 response timed out; the session must be re-identified.",
                                recoverable=True,
                            ) from exc
                        group_errors[group_key] = exc.as_dict()
                        if self._stop.is_set():
                            return
                        if not exc.recoverable:
                            if monitoring:
                                self._suspend_monitor(exc, monitor_generation)
                            raise
                        if exc.code in {
                            "serial_read_failed",
                            "serial_write_failed",
                            "transport_closed",
                            "transport_disconnected",
                            "usb_disconnected",
                        }:
                            if monitoring:
                                self._suspend_monitor(exc, monitor_generation)
                            raise
                        if attempt == self._group_max_attempts:
                            break
                        if self._stop.wait(self._retry_backoff * attempt):
                            return
                if self._stop.is_set():
                    return
            if monitoring and group is not None:
                with self._condition:
                    if (
                        monitor_generation == self._monitor_generation
                        and self._state["monitor"]["active"]
                        and not group_errors
                    ):
                        self._monitor_cursor = (self._monitor_cursor + 1) % len(monitor_groups)
                        self._state["monitor"]["current_group"] = None
                    elif group_errors:
                        error_data = next(iter(group_errors.values()))
                        if (
                            monitor_generation == self._monitor_generation
                            and self._state["monitor"]["active"]
                        ):
                            self._monitor_groups = None
                            self._monitor_generation += 1
                            self._state["monitor"].update(
                                active=False,
                                status="suspended",
                                current_group=None,
                                error=error_data,
                            )
                    self._condition.notify_all()
            else:
                with self._lock:
                    if self._groups_generation == groups_generation:
                        self._groups = ()
            if values or group_results:
                with self._lock:
                    if monitoring:
                        self._state["values"].update(values)
                        self._state["raw_values"].update(raw_values)
                        self._state["groups"].update(group_results)
                        self._state["group_errors"].update(group_errors)
                        for key in group_results:
                            self._state["group_errors"].pop(key, None)
                        self._state["attempts"].update(attempts)
                    else:
                        self._state["values"] = values
                        self._state["raw_values"] = raw_values
                        self._state["value_groups"] = {
                            key: group_key for key in values
                        }
                        self._state["raw_value_groups"] = {
                            key: group_key for key in raw_values
                        }
                        self._state["groups"] = group_results
                        self._state["group_errors"] = group_errors
                        self._state["attempts"] = attempts
                    self._state["status"] = "live"
                    self._state["last_update"] = time.time()
                    self._state["freshness"] = "fresh"
                    self._state["error"] = next(iter(group_errors.values()), None)
            elif group_errors:
                with self._lock:
                    if monitoring:
                        for key in group_errors:
                            self._state["groups"][key] = "error"
                        self._state["groups"].update(group_results)
                        self._state["group_errors"].update(group_errors)
                        self._state["attempts"].update(attempts)
                        self._state["error"] = next(iter(group_errors.values()))
                        if not self._state["values"]:
                            self._state["status"] = "error"
                            self._state["freshness"] = "unavailable"
                    else:
                        self._state["values"] = {}
                        self._state["raw_values"] = {}
                        self._state["groups"] = group_results
                        self._state["group_errors"] = group_errors
                        self._state["attempts"] = attempts
                        self._state["status"] = "error"
                        self._state["freshness"] = "unavailable"
                        self._state["last_update"] = None
                        self._state["error"] = next(iter(group_errors.values()))
            if not monitoring:
                self._wait_for_command(self._sample_interval)

    def _mark_retry(self, retry_count: int, error: DiagnosticError) -> None:
        with self._lock:
            self._state.update(
                status="reconnecting",
                session_active=False,
                values={},
                raw_values={},
                freshness="unavailable",
                last_update=None,
                error=error.as_dict(),
                retry_count=retry_count,
            )

    def _set_error(self, error: DiagnosticError) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            self._state.update(
                status="error",
                session_active=False,
                values={},
                raw_values={},
                freshness="unavailable",
                last_update=None,
                error=error.as_dict(),
            )

    def _close_transport(self, transport: Any) -> None:
        with self._lock:
            if self._transport is transport or getattr(self._transport, "_transport", None) is transport:
                self._transport = None
        close = getattr(transport, "close", None) if transport is not None else None
        if close:
            close()
