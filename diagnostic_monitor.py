"""Bounded raw transport tracing for diagnostic sessions."""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from collections import deque
from typing import Any


class DiagnosticTrace:
    def __init__(self, *, max_events: int = 2000, max_event_bytes: int = 256):
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_events)))
        self._max_event_bytes = max(1, int(max_event_bytes))
        self._sessions: deque[dict[str, Any]] = deque(maxlen=32)
        self._active_session_id: str | None = None
        self._next_session = 0
        self._next_sequence = 0
        self._lock = threading.RLock()

    def begin_session(
        self,
        device_name: str,
        baudrate: int,
        *,
        local_echo: bool | None = True,
        vendor_id: int | None = None,
        product_id: int | None = None,
    ) -> str:
        with self._lock:
            self._next_session += 1
            session_id = f"s{self._next_session}"
            session = {
                "id": session_id,
                "device_name": str(device_name)[:128],
                "driver": "Windows serial",
                "baudrate": int(baudrate),
                "data_bits": 8,
                "parity": "none",
                "stop_bits": 1,
                "local_echo": local_echo,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "ecu_part_number": None,
                "ecu_component": None,
                "ecu_software_version": None,
                "extra_identification_present": False,
            }
            self._sessions.append(session)
            self._active_session_id = session_id
            self._record_locked(
                "state", "session_start", result=f"device={session['device_name']}; baudrate={baudrate}; local_echo={local_echo}",
                origin="application_state", session_id=session_id,
            )
            return session_id

    def update_identity(
        self,
        part_number: str,
        component: str | None,
        software_version: str | None,
        extra_identification_present: bool,
    ) -> None:
        with self._lock:
            for session in reversed(self._sessions):
                if session["id"] == self._active_session_id:
                    session.update({
                        "ecu_part_number": str(part_number)[:128],
                        "ecu_component": str(component)[:128] if component else None,
                        "ecu_software_version": str(software_version)[:128] if software_version else None,
                        "extra_identification_present": bool(extra_identification_present),
                    })
                    break
        self.record(
            "identity", "identity_decode",
            result=f"part_number={part_number}; extra_identification_present={bool(extra_identification_present)}",
            origin="protocol_decode",
            interpretation="Extra ECU identification text is not copied into structured metadata; raw RX bytes may contain vehicle identifiers.",
        )

    def record(
        self,
        direction: str,
        phase: str,
        data: bytes = b"",
        *,
        result: str | None = None,
        error: str | None = None,
        interpretation: str | None = None,
        origin: str | None = None,
        session_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        with self._lock:
            self._record_locked(
                direction, phase, data, result=result, error=error, interpretation=interpretation,
                origin=origin, session_id=session_id, duration_ms=duration_ms,
            )

    def _record_locked(
        self,
        direction: str,
        phase: str,
        data: bytes = b"",
        *,
        result: str | None = None,
        error: str | None = None,
        interpretation: str | None = None,
        origin: str | None = None,
        session_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        raw = bytes(data)
        retained = raw[: self._max_event_bytes]
        self._next_sequence += 1
        self._events.append({
            "sequence": self._next_sequence,
            "session_id": session_id or self._active_session_id,
            "timestamp": time.time(),
            "monotonic": time.monotonic(),
            "direction": str(direction)[:32],
            "phase": str(phase)[:128],
            "data_hex": retained.hex(),
            "result": str(result)[:512] if result is not None else None,
            "error": str(error)[:128] if error is not None else None,
            "interpretation": str(interpretation)[:512] if interpretation is not None else None,
            "origin": origin or self._default_origin(direction),
            "duration_ms": max(0.0, float(duration_ms)) if duration_ms is not None else None,
            "truncated_bytes": len(raw) - len(retained),
        })

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events]

    def sessions_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(session) for session in self._sessions]

    def export_json(self) -> str:
        return json.dumps(
            {
                "schema": "fis-afn-diagnostic-trace/v1",
                "platform": "windows",
                "sessions": self.sessions_snapshot(),
                "events": self.snapshot(),
                "evidence": {"electrical_waveform_measured": False},
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def export_csv(self) -> str:
        output = io.StringIO()
        fields = (
            "timestamp", "monotonic", "direction", "phase", "data_hex",
            "result", "error", "interpretation", "sequence", "session_id", "origin",
            "duration_ms", "truncated_bytes", "device_name", "vendor_id", "product_id",
            "baudrate", "data_bits", "parity", "stop_bits", "local_echo",
            "ecu_part_number", "ecu_component", "ecu_software_version", "extra_identification_present",
            "electrical_waveform_measured",
        )
        session_by_id = {session["id"]: session for session in self.sessions_snapshot()}
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for event in self.snapshot():
            session = session_by_id.get(event["session_id"], {})
            row = dict(event)
            row.update({key: session.get(key) for key in fields if key not in row})
            row["electrical_waveform_measured"] = False
            writer.writerow(row)
        return output.getvalue()

    @staticmethod
    def _default_origin(direction: str) -> str:
        return {
            "tx": "serial_write_call",
            "rx": "serial_read_result",
            "control": "software_control_request",
            "state": "application_state",
            "identity": "protocol_decode",
            "tx_frame": "protocol_encoder",
            "rx_frame": "protocol_decoder",
            "ack": "protocol_decoder",
            "error": "software_exception",
        }.get(direction, "protocol_state")


class MonitoredTransport:
    def __init__(self, transport: Any, trace: DiagnosticTrace, *, phase: str = "idle"):
        self._transport = transport
        self.trace = trace
        self.phase = phase
        self._last_tx_byte: int | None = None
        register_observer = getattr(self._transport, "set_control_observer", None)
        if register_observer is not None:
            register_observer(self._record_break_control)

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def _record_break_control(self, asserted: bool, phase: str, result: str) -> None:
        self.trace.record(
            "control",
            f"break_{phase}",
            bytes([1 if asserted else 0]),
            result=result,
            interpretation="Serial BREAK software API request/result only; physical K-Line waveform and timing are unmeasured.",
            origin="software_control_request" if result == "requested" else "software_control_result",
        )

    @property
    def local_echo(self) -> bool:
        return bool(getattr(self._transport, "local_echo", False))

    def init_slow(self, address: int, bit_time: float) -> None:
        started = time.perf_counter()
        self.trace.record(
            "control", self.phase, bytes([address]), result="requested",
            interpretation="KW1281 slow-init call via serial BREAK; electrical waveform/timing are unmeasured",
            origin="software_control_request",
        )
        try:
            init = getattr(self._transport, "init_slow", None)
            if init is None:
                init = getattr(self._transport, "init_5baud", None)
            if init is None:
                raise AttributeError("Transport cannot perform KW1281 slow init.")
            init(address, bit_time)
            self.trace.record(
                "control",
                self.phase,
                bytes([address]),
                result=f"transport_call_returned; requested_bit_time={bit_time:g}s",
                interpretation="KW1281 7O1 slow init via serial BREAK; electrical timing unmeasured",
                origin="software_control_result",
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            self.trace.record("error", self.phase, error=getattr(exc, "code", type(exc).__name__), result=str(exc))
            raise

    def init_5baud(self, address: int, bit_time: float) -> None:
        """Compatibility alias for transports expecting the old method name."""
        self.init_slow(address, bit_time)

    def set_normal_baudrate(self, baudrate: int) -> None:
        setter = getattr(self._transport, "set_normal_baudrate", None)
        if setter is None:
            raise AttributeError("Transport cannot select a normal UART baud rate.")
        try:
            setter(baudrate)
            self.trace.record(
                "control", self.phase, result=f"normal_baud_requested={baudrate}"
            )
        except Exception as exc:
            self.trace.record("error", self.phase, error=getattr(exc, "code", type(exc).__name__))
            raise

    def write(self, data: bytes) -> None:
        started = time.perf_counter()
        try:
            result = self._transport.write(data)
            self.trace.record(
                "tx", self.phase, data, result="write_call_returned",
                interpretation=self._interpret("tx", data),
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            self._last_tx_byte = data[0] if len(data) == 1 else None
            return result
        except Exception as exc:
            self.trace.record("error", self.phase, data, error=getattr(exc, "code", type(exc).__name__), result=str(exc))
            raise

    def read(self, size: int, timeout: float) -> bytes:
        started = time.perf_counter()
        try:
            data = self._transport.read(size, timeout)
            self.trace.record(
                "rx", self.phase, data, result="empty_read" if not data else "read_returned",
                interpretation=self._interpret("rx", data),
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            return data
        except Exception as exc:
            self.trace.record("error", self.phase, error=getattr(exc, "code", type(exc).__name__), result=str(exc))
            raise

    def record_protocol_event(
        self,
        direction: str,
        phase: str,
        data: bytes = b"",
        result: str | None = None,
        error: str | None = None,
        interpretation: str | None = None,
    ) -> None:
        self.trace.record(
            direction, phase, data, result=result, error=error, interpretation=interpretation,
            origin=DiagnosticTrace._default_origin(direction),
        )

    def update_identity(
        self,
        part_number: str,
        component: str | None,
        software_version: str | None,
        extra_identification_present: bool,
    ) -> None:
        self.trace.update_identity(part_number, component, software_version, extra_identification_present)

    def close(self) -> None:
        self.trace.record("state", "transport_close", result="requested")
        close = getattr(self._transport, "close", None)
        if close:
            close()
        self.trace.record("state", "transport_close", result="returned")

    def _interpret(self, direction: str, data: bytes) -> str | None:
        if data == b"\x55":
            return "KW1281 sync"
        if len(data) >= 3 and data[0] == 0x55:
            return "KW1281 sync + keyword bytes"
        if (
            direction == "rx"
            and len(data) == 1
            and self._last_tx_byte is not None
            and data[0] == (self._last_tx_byte ^ 0xFF)
        ):
            return "complement ACK candidate"
        if data.endswith(b"\x03"):
            return "KW1281 block ending"
        return f"{direction} raw bytes"
