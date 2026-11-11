"""Bounded, read-only protocol primitives for fixture-backed VAG sessions."""

from __future__ import annotations

from typing import Protocol

from diagnostic_models import DiagnosticError, EcuIdentity
from diagnostic_policy import require_allowed_service
from diagnostic_groups import decode_group
from diagnostic_models import SourceKind


class ProtocolTransport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, size: int, timeout: float) -> bytes: ...


def checksum8(payload: bytes) -> int:
    return sum(payload) & 0xFF


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > 255:
        raise DiagnosticError("frame_too_large", "Payload exceeds one-byte frame length.")
    return bytes([len(payload)]) + payload + bytes([checksum8(payload)])


def decode_frame(frame: bytes, *, max_length: int = 256) -> bytes:
    if not frame:
        raise DiagnosticError("frame_truncated", "Frame has no length byte.")
    length = frame[0]
    if length > max_length:
        raise DiagnosticError("frame_too_large", "Frame exceeds the configured limit.")
    expected_size = length + 2
    if len(frame) != expected_size:
        raise DiagnosticError("frame_truncated", "Frame length does not match its payload.")
    payload = frame[1:-1]
    if checksum8(payload) != frame[-1]:
        raise DiagnosticError("checksum_invalid", "Frame checksum is invalid.")
    return payload


def strip_echo(sent: bytes, received: bytes) -> bytes:
    if received.startswith(sent):
        return received[len(sent):]
    return received


class Kw1281Session:
    def __init__(self, *, timeout: float = 1.0, max_frame_length: int = 256):
        self.timeout = timeout
        self.max_frame_length = max_frame_length

    def send_service(
        self, transport: ProtocolTransport, service_id: int, payload: bytes
    ) -> bytes:
        require_allowed_service(service_id)
        if service_id == 0x01 and payload not in (b"", b"IDENTIFY"):
            raise DiagnosticError(
                "service_payload_invalid",
                "Identification accepts no payload.",
            )
        if service_id == 0x22 and len(payload) != 1:
            raise DiagnosticError(
                "service_payload_invalid",
                "Measurement reads require exactly one group byte.",
            )
        encoded_request = encode_frame(payload)
        transport.write(encoded_request)
        raw = transport.read(len(encoded_request) + self.max_frame_length + 2, self.timeout)
        return decode_frame(strip_echo(encoded_request, raw), max_length=self.max_frame_length)

    def identify(self, transport: ProtocolTransport) -> EcuIdentity:
        payload = self.send_service(transport, 0x01, b"IDENTIFY")
        try:
            kind, part_number, software_version, supplier = payload.decode("ascii").split("|", 3)
        except (UnicodeDecodeError, ValueError) as exc:
            raise DiagnosticError("identity_invalid", "ECU identity response is invalid.") from exc
        if kind != "ID" or not part_number:
            raise DiagnosticError("identity_invalid", "ECU identity response is invalid.")
        return EcuIdentity(
            part_number=part_number,
            software_version=software_version or None,
            supplier=supplier or None,
            protocol="KW1281",
        )

    def request_group(
        self, transport: ProtocolTransport, group: int
    ) -> list[dict[str, object]]:
        if not 0 <= group <= 255:
            raise DiagnosticError("group_invalid", "Measurement group must fit one byte.")
        payload = self.send_service(transport, 0x22, bytes([group]))
        try:
            fields = payload.decode("ascii").split("|")
            if len(fields) < 2 or fields[0] != "GROUP" or int(fields[1]) != group:
                raise ValueError
            cells = []
            for field in fields[2:]:
                label, value = field.split("=", 1)
                cells.append({"label": label, "value": float(value)})
            return cells
        except (UnicodeDecodeError, ValueError) as exc:
            raise DiagnosticError("group_invalid", "Measurement group response is invalid.") from exc

    def request_group_values(self, transport: ProtocolTransport, group: int):
        payload = self.send_service(transport, 0x22, bytes([group]))
        try:
            return decode_group(group, payload)
        except (UnicodeDecodeError, ValueError) as exc:
            raise DiagnosticError("group_invalid", "Measurement group response is invalid.") from exc

    def close(self, transport: ProtocolTransport) -> None:
        close = getattr(transport, "close", None)
        if close:
            close()
