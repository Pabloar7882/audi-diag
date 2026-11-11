"""Serial and fixture transports for the read-only K-Line protocol."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Iterable, Any

from diagnostic_models import DiagnosticError


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    manufacturer: str | None


def build_kw1281_slow_init_bits(address: int) -> tuple[int, ...]:
    """Return start, seven LSB-first data bits, odd parity, and stop."""
    if not isinstance(address, int) or not 0 <= address <= 0x7F:
        raise DiagnosticError(
            "five_baud_invalid",
            "KW1281 slow-init address must fit seven data bits.",
        )
    data_bits = tuple((address >> bit) & 1 for bit in range(7))
    parity_bit = 1 if sum(data_bits) % 2 == 0 else 0
    return (0, *data_bits, parity_bit, 1)


def discover_ports(backend: Iterable[Any] | None = None) -> list[PortInfo]:
    if backend is None:
        try:
            from serial.tools import list_ports
        except ImportError as exc:
            raise DiagnosticError(
                "serial_backend_missing",
                "Install pyserial to discover Windows serial ports.",
            ) from exc
        backend = list_ports.comports()
    return [
        PortInfo(
            device=str(port.device),
            description=str(getattr(port, "description", "")),
            vid=getattr(port, "vid", None),
            pid=getattr(port, "pid", None),
            manufacturer=getattr(port, "manufacturer", None),
        )
        for port in backend
    ]


class ReplayTransport:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise DiagnosticError("transport_closed", "Transport is closed.")
        self.writes.append(data)

    def read(self, size: int, timeout: float) -> bytes:
        if self.closed:
            raise DiagnosticError("transport_closed", "Transport is closed.")
        if not self._chunks:
            raise DiagnosticError("timeout", "Transport read timed out.", recoverable=True)
        return self._chunks.pop(0)[:size]

    def close(self) -> None:
        self.closed = True


class SerialKLineTransport:
    def __init__(
        self,
        serial_factory: Callable[..., Any] | None = None,
        *,
        sleeper: Callable[[float], None] | None = None,
        local_echo: bool = True,
    ):
        self._serial_factory = serial_factory
        self._sleep = sleeper or time.sleep
        self.local_echo = bool(local_echo)
        self._serial = None
        self._baudrate: int | None = None
        self._control_observer = None

    def set_control_observer(self, observer) -> None:
        self._control_observer = observer

    def open(self, device: str, *, baudrate: int, timeout: float) -> None:
        if self._serial is not None:
            raise DiagnosticError("transport_open", "Transport is already open.")
        factory = self._serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as exc:
                raise DiagnosticError(
                    "serial_backend_missing",
                    "Install pyserial to use a Windows K-Line port.",
                ) from exc
            factory = serial.Serial
        try:
            self._serial = factory(
                port=device,
                baudrate=baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self._serial.rts = False
            self._serial.dtr = True
            self._baudrate = baudrate
        except Exception as exc:
            opened = self._serial
            self._serial = None
            self._baudrate = None
            if opened is not None:
                try:
                    opened.close()
                except Exception:
                    pass
            raise DiagnosticError("serial_open_failed", str(exc), recoverable=True) from exc

    def write(self, data: bytes) -> None:
        if self._serial is None:
            raise DiagnosticError("transport_closed", "Transport is not open.")
        try:
            self._serial.write(data)
        except Exception as exc:
            raise DiagnosticError("serial_write_failed", str(exc), recoverable=True) from exc

    def set_normal_baudrate(self, baudrate: int) -> None:
        """Select the normal post-init UART rate; this does not verify wire timing."""
        if self._serial is None:
            raise DiagnosticError("transport_closed", "Transport is not open.")
        if not isinstance(baudrate, int) or baudrate <= 0:
            raise DiagnosticError("normal_baud_invalid", "UART baud rate must be a positive integer.")
        try:
            self._serial.baudrate = baudrate
            self._baudrate = baudrate
        except Exception as exc:
            raise DiagnosticError("normal_baud_failed", str(exc), recoverable=True) from exc

    def init_slow(self, address: int, bit_time: float) -> None:
        """Generate KW1281's slow-init waveform using serial BREAK control.

        The UART remains configured at its normal post-init baud. BREAK holds
        TX low; clearing BREAK releases the line high. The software intervals
        are not a measurement of the electrical waveform at the K-Line.
        """
        if self._serial is None:
            raise DiagnosticError("transport_closed", "Transport is not open.")
        bits = build_kw1281_slow_init_bits(address)
        if not bit_time > 0:
            raise DiagnosticError("five_baud_invalid", "Invalid KW1281 5-baud parameters.")
        if getattr(self._serial, "break_condition", None) is None:
            raise DiagnosticError(
                "five_baud_unsupported",
                "The selected serial driver does not expose BREAK control for KW1281 slow init.",
            )
        try:
            self._set_break(False, "prelude")
            # KW1281 requires the line to remain idle high before the slow init.
            self._sleep(0.3)
            for index, bit in enumerate(bits):
                # Establish the symbol first, then hold it for one full bit time.
                self._set_break(not bool(bit), f"bit_{index}")
                self._sleep(bit_time)
            reset_input = getattr(self._serial, "reset_input_buffer", None)
            if reset_input:
                reset_input()
        except DiagnosticError:
            raise
        except Exception as exc:
            raise DiagnosticError("five_baud_failed", str(exc), recoverable=True) from exc
        finally:
            try:
                if self._serial.break_condition:
                    self._set_break(False, "finally_deassert")
            except Exception:
                pass

    def _set_break(self, asserted: bool, phase: str) -> None:
        self._notify_control(asserted, phase, "requested")
        try:
            self._serial.break_condition = asserted
            self._notify_control(asserted, phase, "transport_call_returned")
        except Exception as exc:
            self._notify_control(asserted, phase, f"failed:{exc}")
            raise

    def _notify_control(self, asserted: bool, phase: str, result: str) -> None:
        observer = self._control_observer
        if observer is not None:
            try:
                observer(asserted, phase, result)
            except Exception:
                # Trace reporting must never alter the transport operation.
                pass

    def init_5baud(self, address: int, bit_time: float) -> None:
        """Compatibility alias; the implementation does not set UART baud to 5."""
        self.init_slow(address, bit_time)

    def read(self, size: int, timeout: float) -> bytes:
        if self._serial is None:
            raise DiagnosticError("transport_closed", "Transport is not open.")
        if size <= 0:
            return b""
        try:
            previous_timeout = getattr(self._serial, "timeout", None)
            self._serial.timeout = timeout
            try:
                return self._serial.read(size)
            finally:
                self._serial.timeout = previous_timeout
        except Exception as exc:
            raise DiagnosticError("serial_read_failed", str(exc), recoverable=True) from exc

    def close(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        finally:
            self._serial = None
