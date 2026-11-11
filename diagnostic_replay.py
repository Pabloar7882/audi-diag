"""Deterministic diagnostic fixtures for development without an ECU."""

from __future__ import annotations

from dataclasses import dataclass

from kline_transport import ReplayTransport
from vag_protocol import encode_frame


@dataclass(frozen=True)
class ReplayScenario:
    name: str
    frames: tuple[bytes, ...]

    def create_transport(self) -> ReplayTransport:
        return ReplayTransport(list(self.frames))


def _healthy_frames() -> tuple[bytes, ...]:
    identity = encode_frame(b"ID|038906018|038906018AF|Bosch")
    group_1 = encode_frame(b"GROUP|001|rpm=820|coolant_c=86|iq_mg=5.2")
    group_3 = encode_frame(b"GROUP|003|maf_actual_mg=410|maf_requested_mg=430")
    group_11 = encode_frame(b"GROUP|011|boost_actual_mbar=1890|boost_requested_mbar=1950|n75_percent=62")
    return identity, group_1, group_3, group_11


def scenarios() -> dict[str, ReplayScenario]:
    healthy = _healthy_frames()
    invalid = bytearray(healthy[1])
    invalid[-1] ^= 0xFF
    return {
        "healthy": ReplayScenario("healthy", healthy),
        "bad_checksum": ReplayScenario("bad_checksum", (healthy[0], bytes(invalid))),
        "timeout": ReplayScenario("timeout", ()),
        "disconnect": ReplayScenario("disconnect", ()),
        "reconnect": ReplayScenario("reconnect", healthy),
    }
