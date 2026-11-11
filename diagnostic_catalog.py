"""Evidence-aware diagnostic catalog, kept independent from ECU policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Mapping

from diagnostic_groups import SUPPORTED_GROUP_IDS


class CatalogError(ValueError):
    """Raised when a catalog document does not satisfy its contract."""


class CatalogStatus(str, Enum):
    ACTIVE_ALLOWED = "ACTIVE_ALLOWED"
    IMPLEMENTED_UNVERIFIED = "IMPLEMENTED_UNVERIFIED"
    METADATA_ONLY = "METADATA_ONLY"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED_CONFIRMED = "UNSUPPORTED_CONFIRMED"


@dataclass(frozen=True)
class SourceReference:
    file: str
    line: int | None = None
    sha256: str | None = None
    repository: str | None = None
    commit: str | None = None


@dataclass(frozen=True)
class GroupDefinition:
    group_id: int
    name: str
    status: CatalogStatus
    source_refs: tuple[SourceReference, ...]
    description: str | None = None
    metadata_candidate_count: int = 0
    metadata_source_count: int = 0

    @property
    def physical_read_allowed(self) -> bool:
        """Physical access always comes from the independent group allow-list."""
        return self.group_id in SUPPORTED_GROUP_IDS


@dataclass(frozen=True)
class SensorDefinition:
    sensor_id: str
    group_id: int
    field_id: int | None
    name: str
    unit: str
    status: CatalogStatus
    decoder_id: str | None
    source_refs: tuple[SourceReference, ...]
    ecu_candidates: tuple[str, ...] = ()
    range_min: float | None = None
    range_max: float | None = None
    range_text: str | None = None

    @property
    def live_readable(self) -> bool:
        """Whether a decoded sensor value can be produced from an allowed group.

        Physical group request eligibility is independent. A raw-only field can
        accompany a permitted group response without being a decoded live value.
        """
        if self.group_id not in SUPPORTED_GROUP_IDS:
            return False
        return (
            self.status in (CatalogStatus.ACTIVE_ALLOWED, CatalogStatus.IMPLEMENTED_UNVERIFIED)
            and self.decoder_id is not None
        )


@dataclass(frozen=True)
class DecoderDefinition:
    decoder_id: str
    status: CatalogStatus
    evidence: tuple[SourceReference, ...]


@dataclass(frozen=True)
class DiagnosticCatalog:
    schema_version: int
    groups: tuple[GroupDefinition, ...]
    sensors: tuple[SensorDefinition, ...]
    decoders: tuple[DecoderDefinition, ...]

    @property
    def physical_group_ids(self) -> tuple[int, ...]:
        """Return the immutable policy IDs, never a catalog-derived expansion."""
        return tuple(SUPPORTED_GROUP_IDS)

    def status_for_group(self, group_id: int) -> CatalogStatus:
        match = next((group for group in self.groups if group.group_id == group_id), None)
        return match.status if match else CatalogStatus.UNKNOWN

    def sensor(self, sensor_id: str) -> SensorDefinition | None:
        return next((item for item in self.sensors if item.sensor_id == sensor_id), None)

    def sensors_for_group(self, group_id: int) -> tuple[SensorDefinition, ...]:
        return tuple(item for item in self.sensors if item.group_id == group_id)


def load_catalog(source: Path | str | Mapping[str, Any]) -> DiagnosticCatalog:
    """Load validated JSON or an already parsed mapping.

    This function only constructs metadata. It never writes to or configures the
    transport policy. Label imports should be passed through
    :func:`merge_label_candidates`, which forcibly preserves metadata-only status.
    """
    document = _read_document(source)
    if not isinstance(document, Mapping):
        raise CatalogError("catalog root must be an object")
    if document.get("schema_version") != 1:
        raise CatalogError("unsupported schema_version; expected 1")

    groups_data = document.get("groups")
    sensors_data = document.get("sensors")
    decoders_data = document.get("decoders")
    if not isinstance(groups_data, list) or not isinstance(sensors_data, list) or not isinstance(decoders_data, list):
        raise CatalogError("groups, sensors, and decoders must be arrays")

    decoders = tuple(_parse_decoder(row) for row in decoders_data)
    decoder_ids = [item.decoder_id for item in decoders]
    if len(set(decoder_ids)) != len(decoder_ids):
        raise CatalogError("duplicate decoder_id")

    groups = tuple(_parse_group(row) for row in groups_data)
    group_ids = [item.group_id for item in groups]
    if len(set(group_ids)) != len(group_ids):
        raise CatalogError("duplicate group_id")
    allowed = set(SUPPORTED_GROUP_IDS)
    for group in groups:
        if group.status is CatalogStatus.ACTIVE_ALLOWED and group.group_id not in allowed:
            raise CatalogError(f"group {group.group_id:03d} cannot be ACTIVE_ALLOWED by catalog metadata")

    sensors = tuple(_parse_sensor(row, set(decoder_ids)) for row in sensors_data)
    sensor_ids = [item.sensor_id for item in sensors]
    if len(set(sensor_ids)) != len(sensor_ids):
        raise CatalogError("duplicate sensor_id")

    return DiagnosticCatalog(1, groups, sensors, decoders)


def merge_label_candidates(
    catalog: DiagnosticCatalog,
    candidates: list[Mapping[str, Any]],
) -> DiagnosticCatalog:
    """Append label candidates as source-attributed, non-live metadata only."""
    imported: list[SensorDefinition] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise CatalogError(f"label candidate {index} must be an object")
        if candidate.get("status") not in (None, CatalogStatus.METADATA_ONLY.value):
            raise CatalogError(f"label candidate {index} must remain METADATA_ONLY")
        if candidate.get("decoder_id") is not None:
            raise CatalogError(f"label candidate {index} cannot assign a decoder")
        if candidate.get("physical_read_allowed") is True or candidate.get("live_readable") is True:
            raise CatalogError(f"label candidate {index} cannot set physical/live eligibility")

        normalized = dict(candidate)
        normalized["status"] = CatalogStatus.METADATA_ONLY.value
        normalized["decoder_id"] = None
        sensor = _parse_sensor(normalized, {item.decoder_id for item in catalog.decoders})
        imported.append(sensor)

    return DiagnosticCatalog(
        schema_version=catalog.schema_version,
        groups=catalog.groups,
        sensors=catalog.sensors + tuple(imported),
        decoders=catalog.decoders,
    )


def _read_document(source: Path | str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    path = Path(source)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read catalog: {exc}") from exc


def _parse_group(row: Any) -> GroupDefinition:
    if not isinstance(row, Mapping):
        raise CatalogError("group record must be an object")
    group_id = row.get("group_id")
    if type(group_id) is not int or not 0 <= group_id <= 255:
        raise CatalogError("group_id must be an integer from 0 through 255")
    name = _required_text(row, "name")
    status = _status(row.get("status"))
    refs = _parse_sources(row.get("source_refs"), required=status is not CatalogStatus.UNKNOWN)
    description = _optional_text(row.get("description"), f"group {group_id} description")
    candidate_count = _nonnegative_count(row.get("metadata_candidate_count", 0), "metadata_candidate_count", group_id)
    source_count = _nonnegative_count(row.get("metadata_source_count", 0), "metadata_source_count", group_id)
    return GroupDefinition(group_id, name, status, refs, description, candidate_count, source_count)


def _parse_sensor(row: Any, decoder_ids: set[str]) -> SensorDefinition:
    if not isinstance(row, Mapping):
        raise CatalogError("sensor record must be an object")
    sensor_id = _required_text(row, "sensor_id")
    group_id = row.get("group_id")
    field_id = row.get("field_id")
    if type(group_id) is not int or not 0 <= group_id <= 255:
        raise CatalogError(f"sensor {sensor_id} group_id must be an integer from 0 through 255")
    if field_id is not None and (type(field_id) is not int or not 0 <= field_id <= 255):
        raise CatalogError(f"sensor {sensor_id} field_id must be null or an integer from 0 through 255")
    status = _status(row.get("status"))
    decoder_id = row.get("decoder_id")
    if decoder_id is not None:
        if not isinstance(decoder_id, str) or decoder_id not in decoder_ids:
            raise CatalogError(f"sensor {sensor_id} references unknown decoder {decoder_id!r}")
    if status in (CatalogStatus.UNKNOWN, CatalogStatus.METADATA_ONLY, CatalogStatus.UNSUPPORTED_CONFIRMED) and decoder_id:
        raise CatalogError(f"sensor {sensor_id} with status {status.value} must not assign a decoder")
    if status is CatalogStatus.IMPLEMENTED_UNVERIFIED and decoder_id is None:
        raise CatalogError(f"sensor {sensor_id} with status IMPLEMENTED_UNVERIFIED requires a decoder")
    range_min, range_max, range_text = _parse_range(row.get("range"), sensor_id)
    return SensorDefinition(
        sensor_id=sensor_id,
        group_id=group_id,
        field_id=field_id,
        name=_required_text(row, "name"),
        unit=_required_text(row, "unit"),
        status=status,
        decoder_id=decoder_id,
        source_refs=_parse_sources(row.get("source_refs")),
        ecu_candidates=_optional_text_list(row.get("ecu_candidates"), "ecu_candidates"),
        range_min=range_min,
        range_max=range_max,
        range_text=range_text,
    )


def _parse_decoder(row: Any) -> DecoderDefinition:
    if not isinstance(row, Mapping):
        raise CatalogError("decoder record must be an object")
    decoder_id = _required_text(row, "decoder_id")
    status = _status(row.get("status"))
    if status not in (CatalogStatus.IMPLEMENTED_UNVERIFIED, CatalogStatus.ACTIVE_ALLOWED):
        raise CatalogError(f"decoder {decoder_id} must be IMPLEMENTED_UNVERIFIED or ACTIVE_ALLOWED")
    return DecoderDefinition(decoder_id, status, _parse_sources(row.get("evidence"), key="evidence"))


def _status(value: Any) -> CatalogStatus:
    try:
        return CatalogStatus(value)
    except (ValueError, TypeError) as exc:
        allowed = ", ".join(item.value for item in CatalogStatus)
        raise CatalogError(f"invalid status {value!r}; expected one of {allowed}") from exc


def _parse_sources(
    value: Any,
    *,
    key: str = "source_refs",
    required: bool = True,
) -> tuple[SourceReference, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value):
        raise CatalogError(f"{key} must be a non-empty array")
    result: list[SourceReference] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise CatalogError(f"{key}[{index}] must be an object")
        file = _required_text(row, "file", prefix=f"{key}[{index}].")
        line = row.get("line")
        if line is not None and (type(line) is not int or line < 1):
            raise CatalogError(f"{key}[{index}].line must be null or a positive integer")
        sha256 = _optional_text(row.get("sha256"), f"{key}[{index}].sha256")
        if sha256 is not None and (len(sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha256)):
            raise CatalogError(f"{key}[{index}].sha256 must be a 64-character hexadecimal digest")
        result.append(SourceReference(
            file=file,
            line=line,
            sha256=sha256,
            repository=_optional_text(row.get("repository"), f"{key}[{index}].repository"),
            commit=_optional_text(row.get("commit"), f"{key}[{index}].commit"),
        ))
    return tuple(result)


def _nonnegative_count(value: Any, key: str, group_id: int) -> int:
    if type(value) is not int or value < 0:
        raise CatalogError(f"group {group_id} {key} must be a non-negative integer")
    return value


def _required_text(row: Mapping[str, Any], key: str, *, prefix: str = "") -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{prefix}{key} must be a non-empty string")
    return value


def _optional_text(value: Any, key: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise CatalogError(f"{key} must be null or a non-empty string")
    return value


def _optional_text_list(value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CatalogError(f"{key} must be null or an array of strings")
    return tuple(_required_text({key: item}, key) for item in value)


def _parse_range(value: Any, sensor_id: str) -> tuple[float | None, float | None, str | None]:
    if value is None:
        return None, None, None
    if not isinstance(value, Mapping):
        raise CatalogError(f"sensor {sensor_id} range must be an object or null")
    low = _range_number(value.get("min"), "min", sensor_id)
    high = _range_number(value.get("max"), "max", sensor_id)
    label = _optional_text(value.get("text"), f"sensor {sensor_id} range.text")
    if low is not None and high is not None and low > high:
        raise CatalogError(f"sensor {sensor_id} range.min cannot exceed range.max")
    return low, high, label


def _range_number(value: Any, bound: str, sensor_id: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CatalogError(f"sensor {sensor_id} range.{bound} must be a finite number or null")
    return float(value)
