"""Configurable AFN measurement-group metadata and conservative decoding."""

from __future__ import annotations

from dataclasses import dataclass
import time

from diagnostic_models import Freshness, SourceKind, TelemetryValue


SUPPORTED_GROUP_IDS = (1, 3, 11)


@dataclass(frozen=True)
class SignalDefinition:
    key: str
    label: str
    unit: str
    factor: float = 1.0
    provisional: bool = True


@dataclass(frozen=True)
class GroupDefinition:
    group_id: int
    label: str
    signals: tuple[SignalDefinition, ...]
    provisional: bool = True


def default_afn_groups() -> tuple[GroupDefinition, ...]:
    known = {
        1: GroupDefinition(1, "Motor", (
            SignalDefinition("rpm", "Rotação", "rpm"),
            SignalDefinition("coolant_c", "Líquido de refrigeração", "°C"),
            SignalDefinition("iq_mg", "Quantidade injetada", "mg/str"),
        )),
        3: GroupDefinition(3, "Massa de ar", (
            SignalDefinition("maf_actual_mg", "MAF real", "mg/str"),
            SignalDefinition("maf_requested_mg", "MAF pedida", "mg/str"),
        )),
        11: GroupDefinition(11, "Turbo", (
            SignalDefinition("boost_actual_mbar", "Pressão real", "mbar"),
            SignalDefinition("boost_requested_mbar", "Pressão pedida", "mbar"),
            SignalDefinition("n75_percent", "Controlo N75", "%"),
        )),
    }
    return tuple(
        known.get(
            group_id,
            GroupDefinition(group_id, f"Grupo {group_id:03d}", ()),
        )
        for group_id in range(256)
    )


def all_group_ids() -> tuple[int, ...]:
    """Return every addressable measurement-group ID without implying support."""
    return tuple(range(256))


def decode_group(group_id: int, payload: bytes) -> list[TelemetryValue]:
    parts = payload.decode("ascii").split("|")
    if len(parts) < 2 or parts[0] != "GROUP" or int(parts[1]) != group_id:
        raise ValueError("measurement group response does not match requested group")
    definitions = {
        signal.key: signal
        for group in default_afn_groups()
        if group.group_id == group_id
        for signal in group.signals
    }
    timestamp = time.time()
    values: list[TelemetryValue] = []
    for raw_field in parts[2:]:
        key, raw_value = raw_field.split("=", 1)
        definition = definitions.get(key)
        if definition is None:
            try:
                unknown_value = float(raw_value)
            except ValueError:
                unknown_value = None
            values.append(TelemetryValue(
                key=key,
                value=unknown_value,
                unit="raw",
                timestamp=timestamp,
                source=SourceKind.REPLAY,
                freshness=Freshness.FRESH,
                raw=raw_field,
            ))
            continue
        value = float(raw_value) * definition.factor
        values.append(TelemetryValue(
            key=key,
            value=value,
            unit=definition.unit,
            timestamp=timestamp,
            source=SourceKind.REPLAY,
            freshness=Freshness.FRESH,
            raw=raw_field,
        ))
    return values
