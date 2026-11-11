"""Shared diagnostic and telemetry value types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SourceKind(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    DEMO = "demo"
    CALCULATED = "calculated"
    UNAVAILABLE = "unavailable"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TelemetryValue:
    key: str
    value: float | None
    unit: str
    timestamp: float | None
    source: SourceKind
    freshness: Freshness
    raw: str | None = None

    @classmethod
    def unavailable(cls, key: str, unit: str, source: SourceKind) -> "TelemetryValue":
        return cls(key, None, unit, None, source, Freshness.UNAVAILABLE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp,
            "source": self.source.value,
            "freshness": self.freshness.value,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class EcuIdentity:
    part_number: str | None = None
    component: str | None = None
    software_version: str | None = None
    supplier: str | None = None
    extra_identification: str | None = None
    protocol: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "part_number": self.part_number,
            "component": self.component,
            "software_version": self.software_version,
            "supplier": self.supplier,
            "extra_identification": self.extra_identification,
            "protocol": self.protocol,
        }


class DiagnosticError(Exception):
    def __init__(self, code: str, message: str, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class SessionState:
    status: str
    identity: EcuIdentity | None = None
    error: DiagnosticError | None = None
