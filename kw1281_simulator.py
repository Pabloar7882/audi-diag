"""Stateful KW1281 ECU simulator for protocol integration tests."""

from __future__ import annotations

from collections import deque

from diagnostic_models import DiagnosticError


class SimulatedKw1281Ecu:
    def __init__(
        self,
        *,
        sync_byte: int = 0x55,
        unsupported_groups: set[int] | None = None,
    ):
        self.sync_byte = sync_byte
        self.unsupported_groups = unsupported_groups or set()
        self.requested_groups: list[int] = []
        self.forbidden_services: list[int] = []

    def response_for(self, title: int, data: bytes) -> tuple[int, bytes] | None:
        if title == 0x00:
            return 0xF6, b"038906018AF"
        if title == 0x09:
            return 0x09, b""
        if title == 0x29 and data:
            group = data[0]
            self.requested_groups.append(group)
            if group in self.unsupported_groups:
                return 0x0A, b""
            return 0xE7, bytes([0x0C, 0x10, 0x20])
        self.forbidden_services.append(title)
        return None


class SimulatedEcuTransport:
    """A byte-acknowledging ECU model with shared block-counter sequencing."""

    local_echo = True

    def __init__(
        self,
        ecu: SimulatedKw1281Ecu,
        *,
        fragment_size: int | None = None,
    ):
        self.ecu = ecu
        self.fragment_size = fragment_size
        self.writes: list[bytes] = []
        self._rx: deque[int] = deque()
        self._request = bytearray()
        self._expected_request_size: int | None = None
        self._response_pending: deque[int] = deque()
        self._response_waiting_ack: int | None = None
        self._keyword_complement_pending = False
        self._startup_pending = False
        self._counter = 0

    def init_5baud(self, address: int, bit_time: float):
        if address != 0x01:
            raise DiagnosticError("address_invalid", "Simulator only exposes engine address 0x01.")
        self._rx.clear()
        self._request.clear()
        self._response_pending.clear()
        self._expected_request_size = None
        self._response_waiting_ack = None
        self._counter = 0
        self._rx.extend((self.ecu.sync_byte, 0x01, 0x8A))
        self._keyword_complement_pending = True
        self._startup_pending = True

    def _queue_block(self, title: int, data: bytes) -> None:
        counter = self._counter
        self._counter = (counter + 1) & 0xFF
        payload = bytes([counter, title]) + data + b"\x03"
        frame = bytes([len(payload)]) + payload
        self._response_pending = deque(frame[1:])
        first = frame[0]
        self._rx.append(first)
        self._response_waiting_ack = first

    def write(self, data: bytes):
        self.writes.append(data)
        if len(data) != 1:
            raise DiagnosticError("simulator_write_invalid", "Simulator expects byte writes.")
        value = data[0]
        if self.local_echo:
            self._rx.append(value)
        if self._keyword_complement_pending:
            self._keyword_complement_pending = False
            if value != (0x8A ^ 0xFF):
                raise DiagnosticError(
                    "keyword_complement_invalid",
                    "Simulator received an invalid KW1281 keyword complement.",
                )
            if self._startup_pending:
                self._startup_pending = False
                self._queue_block(0xF6, b"038906018AF")
            return
        if self._response_waiting_ack is not None:
            if value != (self._response_waiting_ack ^ 0xFF):
                raise DiagnosticError("simulator_response_ack_invalid", "Simulator received an invalid response ACK.")
            self._response_waiting_ack = None
            if self._response_pending:
                next_value = self._response_pending.popleft()
                self._rx.append(next_value)
                if self._response_pending:
                    self._response_waiting_ack = next_value
            return

        if self._expected_request_size is None:
            self._expected_request_size = value + 1
        self._request.append(value)
        if len(self._request) < self._expected_request_size:
            self._rx.append(value ^ 0xFF)
        if len(self._request) != self._expected_request_size:
            return

        frame = bytes(self._request)
        self._request.clear()
        self._expected_request_size = None
        if len(frame) < 4 or frame[-1] != 0x03:
            raise DiagnosticError("simulator_block_invalid", "Tester sent an invalid KW1281 block.")
        if frame[1] != self._counter:
            raise DiagnosticError(
                "block_counter_invalid",
                f"Tester block counter is 0x{frame[1]:02X}; expected 0x{self._counter:02X}.",
            )
        self._counter = (self._counter + 1) & 0xFF
        result = self.ecu.response_for(frame[2], frame[3:-1])
        if result is None:
            return
        title, body = result
        self._queue_block(title, body)

    def read(self, size: int, timeout: float) -> bytes:
        if not self._rx:
            raise DiagnosticError("timeout", "Simulated ECU timed out.", recoverable=True)
        count = min(size, len(self._rx))
        if self.fragment_size is not None:
            count = min(count, self.fragment_size)
        return bytes(self._rx.popleft() for _ in range(count))

    def close(self):
        self._rx.clear()
