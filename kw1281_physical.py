"""Experimental physical-wire KW1281 session primitives.

This module is intentionally separate from the ASCII fixture protocol in
``vag_protocol.py``. It is not evidence of compatibility until a real trace
confirms the configured timings and ECU response.
"""

from __future__ import annotations

import time
from typing import Any

from diagnostic_models import DiagnosticError, EcuIdentity, Freshness, SourceKind, TelemetryValue
from diagnostic_policy import require_allowed_service
from diagnostic_groups import SUPPORTED_GROUP_IDS


class Kw1281PhysicalSession:
    _ACK = 0x09
    _NAK = 0x0A
    _ASCII = 0xF6
    _MEASUREMENTS = 0xE7
    _MAX_RESPONSE_BLOCKS = 32
    _MAX_IDENT_SEGMENTS = 16

    def __init__(
        self,
        *,
        address: int = 0x01,
        bit_time: float = 0.2,
        baudrate: int | None = None,
        timeout: float = 1.0,
        sleeper=time.sleep,
    ):
        self.address = address
        self.bit_time = bit_time
        self.baudrate = baudrate
        self.timeout = timeout
        self._sleep = sleeper
        self._rx = bytearray()
        self._counter: int | None = None
        self._identified = False

    @staticmethod
    def _set_phase(transport: Any, phase: str) -> None:
        set_phase = getattr(transport, "set_phase", None)
        if set_phase:
            set_phase(phase)

    @staticmethod
    def _record_event(
        transport: Any,
        direction: str,
        phase: str,
        data: bytes = b"",
        *,
        result: str | None = None,
        error: str | None = None,
        interpretation: str | None = None,
    ) -> None:
        recorder = getattr(transport, "record_protocol_event", None)
        if recorder:
            recorder(direction, phase, data, result, error, interpretation)

    def _failure(self, transport: Any, phase: str, code: str, message: str) -> DiagnosticError:
        self._record_event(transport, "error", phase, result=message, error=code)
        return DiagnosticError(code, message, recoverable=True)

    def _trace_errors(self, transport: Any, action: Any) -> Any:
        try:
            return action()
        except DiagnosticError as error:
            phase = getattr(transport, "phase", "protocol")
            trace = getattr(transport, "trace", None)
            if trace is not None:
                events = trace.snapshot()
                if events and events[-1].get("direction") == "error" and events[-1].get("error") == error.code:
                    raise
            self._record_event(
                transport,
                "error",
                phase,
                result=str(error),
                error=error.code,
            )
            raise

    def _read_bytes(self, transport: Any, size: int) -> bytes:
        while len(self._rx) < size:
            chunk = transport.read(size - len(self._rx), self.timeout)
            if not chunk:
                raise self._failure(transport, "response_wait", "timeout", "KW1281 response timed out.")
            self._rx.extend(chunk)
        result = bytes(self._rx[:size])
        del self._rx[:size]
        return result

    def _read_block(self, transport: Any) -> bytes:
        self._set_phase(transport, "block_receive")
        length = self._read_bytes(transport, 1)[0]
        if length < 3:
            raise self._failure(transport, "block_receive", "block_length_invalid", "KW1281 block length is invalid.")
        self._write_byte(transport, length ^ 0xFF)

        payload = bytearray()
        for index in range(length):
            value = self._read_bytes(transport, 1)[0]
            payload.append(value)
            if index < length - 1:
                self._write_byte(transport, value ^ 0xFF)

        counter = payload[0]
        if self._counter is None:
            self._counter = counter
        elif counter != self._counter:
            raise self._failure(
                transport,
                "block_receive",
                "block_counter_invalid",
                f"KW1281 block counter is 0x{counter:02X}; expected 0x{self._counter:02X}.",
            )
        self._counter = (counter + 1) & 0xFF

        frame = bytes([length]) + bytes(payload)
        title = payload[1]
        self._record_event(
            transport,
            "rx_frame",
            "block_receive",
            frame,
            result=f"counter={counter}; title=0x{title:02x}; ETX={'valid' if payload[-1] == 0x03 else 'invalid'}",
        )
        if payload[-1] != 0x03:
            raise self._failure(transport, "block_receive", "block_end_invalid", "KW1281 block has no end byte.")
        return bytes(payload)

    def _write_byte(self, transport: Any, value: int) -> None:
        data = bytes([value])
        transport.write(data)
        if getattr(transport, "local_echo", False):
            echo = self._read_bytes(transport, 1)[0]
            if echo != value:
                raise self._failure(
                    transport,
                    "tx_echo",
                    "tx_echo_invalid",
                    f"K-Line transmit echo mismatch: sent 0x{value:02X}, received 0x{echo:02X}.",
                )
            self._record_event(transport, "ack", "tx_echo", bytes([echo]), result="echo=valid")

    def _send_block(self, transport: Any, title: int, data: bytes = b"") -> None:
        if self._counter is None:
            raise DiagnosticError("block_counter_unset", "KW1281 counter is not initialized by the ECU.")
        self._set_phase(transport, "block_transmit")
        counter = self._counter
        payload = bytes([counter, title]) + data + b"\x03"
        block = bytes([len(payload)]) + payload
        self._record_event(
            transport,
            "tx_frame",
            "block_transmit",
            block,
            result=f"counter={counter}; title=0x{title:02x}; ETX=0x03",
            interpretation="KW1281 frame fields are length, counter, title, data and ETX; no separate block checksum field is defined here.",
        )
        for index, value in enumerate(block):
            self._write_byte(transport, value)
            if index < len(block) - 1:
                ack = self._read_bytes(transport, 1)[0]
                if ack != (value ^ 0xFF):
                    self._record_event(
                        transport,
                        "error",
                        "byte_complement",
                        bytes([ack]),
                        result=f"expected=0x{value ^ 0xFF:02x}; received=0x{ack:02x}",
                        error="byte_ack_invalid",
                    )
                    raise DiagnosticError(
                        "byte_ack_invalid",
                        f"KW1281 byte acknowledgement mismatch for 0x{value:02X}.",
                    )
                self._record_event(transport, "ack", "byte_complement", bytes([ack]), result="complement=valid")
        self._counter = (counter + 1) & 0xFF

    def _initialise(self, transport: Any) -> None:
        self._counter = None
        self._identified = False
        self._rx.clear()
        self._set_phase(transport, "slow_init")
        if self.baudrate is not None:
            set_baudrate = getattr(transport, "set_normal_baudrate", None)
            if set_baudrate is None:
                raise DiagnosticError(
                    "normal_baud_unsupported",
                    "The selected transport cannot select the requested post-init baud rate.",
                )
            set_baudrate(self.baudrate)
        init = getattr(transport, "init_slow", None)
        if init is None:
            init = getattr(transport, "init_5baud", None)
        if init is None:
            raise DiagnosticError(
                "five_baud_unsupported",
                "The selected transport cannot perform KW1281 slow initialization.",
            )
        init(self.address, self.bit_time)
        self._set_phase(transport, "keyword_wait")
        keyword = self._read_bytes(transport, 3)
        if keyword[0] != 0x55:
            raise DiagnosticError("sync_invalid", "KW1281 sync byte is invalid.")
        protocol_version = ((keyword[2] & 0x7F) << 7) | (keyword[1] & 0x7F)
        self._record_event(transport, "rx_keyword", "keyword_wait", keyword, result=f"sync=0x55; protocol={protocol_version}")
        self._set_phase(transport, "keyword_ack")
        self._sleep(0.025)
        self._write_byte(transport, keyword[2] ^ 0xFF)
        self._record_event(transport, "ack", "keyword_complement", bytes([keyword[2] ^ 0xFF]), result="complement sent")
        if protocol_version != 1281:
            raise DiagnosticError(
                "protocol_unsupported",
                f"ECU negotiated protocol {protocol_version}; this session only permits KW1281.",
            )

    def _receive_blocks(self, transport: Any) -> tuple[list[bytes], int]:
        blocks: list[bytes] = []
        for _ in range(self._MAX_RESPONSE_BLOCKS):
            payload = self._read_block(transport)
            title = payload[1]
            if title in (self._ACK, self._NAK):
                self._record_event(
                    transport,
                    "ack" if title == self._ACK else "nak",
                    "terminal_response",
                    bytes([len(payload)]) + payload,
                    result="terminal ACK" if title == self._ACK else "terminal NAK",
                )
                return blocks, title
            blocks.append(payload)
            self._send_block(transport, self._ACK)
        raise DiagnosticError("block_sequence_too_long", "KW1281 response exceeded the block limit.")

    @staticmethod
    def _ascii_identity(blocks: list[bytes]) -> tuple[list[str], bool]:
        fields: list[str] = []
        more_available = False
        for payload in blocks:
            if payload[1] != Kw1281PhysicalSession._ASCII:
                continue
            data = payload[2:-1]
            if not data:
                continue
            more_available |= bool(data[0] & 0x80)
            fields.append("".join(chr(value & 0x7F) for value in data).strip("\x00 \r\n"))
        return fields, more_available

    def identify(self, transport: Any) -> EcuIdentity:
        return self._trace_errors(transport, lambda: self._identify(transport))

    def _identify(self, transport: Any) -> EcuIdentity:
        self._initialise(transport)
        identity_blocks: list[str] = []
        extra_blocks: list[str] = []
        blocks, terminal = self._receive_blocks(transport)
        identity_fields, more = self._ascii_identity(blocks)
        identity_blocks.extend(identity_fields)
        if terminal == self._NAK:
            raise DiagnosticError("identity_rejected", "ECU rejected the identification exchange.")
        if not identity_blocks or not identity_blocks[0]:
            raise DiagnosticError("identity_block_invalid", "ECU did not send an ASCII identification block.")

        for _ in range(self._MAX_IDENT_SEGMENTS - 1):
            if not more:
                break
            self._send_block(transport, 0x00)
            blocks, terminal = self._receive_blocks(transport)
            if terminal == self._NAK:
                raise DiagnosticError("identity_rejected", "ECU rejected a follow-up identification request.")
            segment_fields, more = self._ascii_identity(blocks)
            if not segment_fields:
                raise DiagnosticError("identity_block_invalid", "ECU returned an empty identification segment.")
            extra_blocks.extend(segment_fields)
        else:
            raise DiagnosticError("identity_too_long", "ECU identity exceeded the segment limit.")

        part_number = identity_blocks[0].strip()
        if not part_number:
            raise DiagnosticError("identity_invalid", "ECU identity text is empty.")
        self._identified = True
        identity = EcuIdentity(
            part_number=part_number,
            component=identity_blocks[1] if len(identity_blocks) > 1 else None,
            software_version=identity_blocks[2] if len(identity_blocks) > 2 else None,
            extra_identification=" ".join(extra_blocks) or None,
            protocol="KW1281",
        )
        if self._identified:
            update_identity = getattr(transport, "update_identity", None)
            if update_identity is not None:
                update_identity(
                    identity.part_number,
                    identity.component,
                    identity.software_version,
                    identity.extra_identification is not None,
                )
            else:
                self._record_event(
                    transport,
                    "identity",
                    "identity_decode",
                    result=f"part_number={identity.part_number}; extra_identification_present={identity.extra_identification is not None}",
                )
        return identity

    def keep_alive(self, transport: Any) -> None:
        self._trace_errors(transport, lambda: self._keep_alive(transport))

    def _keep_alive(self, transport: Any) -> None:
        if not self._identified:
            raise DiagnosticError("ecu_not_identified", "Identify the ECU before keeping the session alive.")
        self._send_block(transport, self._ACK)
        blocks, terminal = self._receive_blocks(transport)
        if blocks or terminal != self._ACK:
            raise DiagnosticError("keepalive_invalid", "ECU did not acknowledge the KW1281 keep-alive block.", recoverable=True)
        self._record_event(transport, "ack", "keepalive", result="ECU ACK received")

    def request_group(self, transport: Any, group: int) -> list[TelemetryValue]:
        return self._trace_errors(transport, lambda: self._request_group(transport, group))

    def _request_group(self, transport: Any, group: int) -> list[TelemetryValue]:
        require_allowed_service(0x22)
        if not self._identified:
            raise DiagnosticError("ecu_not_identified", "Identify the ECU before reading a group.")
        if type(group) is not int or group not in SUPPORTED_GROUP_IDS:
            raise DiagnosticError("group_not_allowed", "Only measurement groups 001, 003 and 011 are enabled.")
        self._send_block(transport, 0x29, bytes([group]))
        blocks, terminal = self._receive_blocks(transport)
        if terminal == self._NAK:
            raise DiagnosticError("group_rejected", "ECU rejected the measurement-group request.")
        response = next((item for item in blocks if item[1] == self._MEASUREMENTS), None)
        if response is None or len(response) < 4:
            raise DiagnosticError("group_block_invalid", "KW1281 measurement block is invalid.")
        raw = bytes(response[2:-1]).hex()
        return [
            TelemetryValue(
                key=f"group_{group:03d}_raw",
                value=None,
                unit="raw",
                timestamp=time.time(),
                source=SourceKind.LIVE,
                freshness=Freshness.FRESH,
                raw=raw,
            )
        ]

    def send_service(self, transport: Any, service_id: int, payload: bytes) -> bytes:
        return self._trace_errors(transport, lambda: self._send_service(transport, service_id, payload))

    def _send_service(self, transport: Any, service_id: int, payload: bytes) -> bytes:
        require_allowed_service(service_id)
        if service_id == 0x01:
            if payload:
                raise DiagnosticError(
                    "service_payload_invalid",
                    "Identification does not accept a payload.",
                )
            self._send_block(transport, 0x00)
        elif service_id == 0x22:
            if len(payload) != 1:
                raise DiagnosticError(
                    "service_payload_invalid",
                    "Measurement reads require exactly one group byte.",
                )
            if payload[0] not in SUPPORTED_GROUP_IDS:
                raise DiagnosticError(
                    "group_not_allowed",
                    "Only measurement groups 001, 003 and 011 are enabled.",
                )
            if not self._identified:
                raise DiagnosticError("ecu_not_identified", "Identify the ECU before reading a group.")
            self._send_block(transport, 0x29, payload)
        else:
            raise DiagnosticError("service_not_allowed", "Physical KW1281 service is not supported.")
        blocks, terminal = self._receive_blocks(transport)
        if terminal == self._NAK:
            raise DiagnosticError("service_rejected", "ECU rejected the read-only request.")
        if not blocks:
            raise DiagnosticError("response_empty", "ECU returned no response block.")
        return blocks[0]
