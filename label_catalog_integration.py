"""Presentation and query view for imported label metadata candidates.

This module never grants physical group access and never assigns a decoder to
label-derived fields. It keeps each source row as a separate catalog candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from diagnostic_groups import SUPPORTED_GROUP_IDS, default_afn_groups
from label_catalog import LabelInventory, LabelField


MAX_QUERY_CHARS = 128
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class LabelQueryError(ValueError):
    """Raised when catalog pagination or filters exceed their bounds."""


@dataclass(frozen=True)
class LabelSensorCandidate:
    sensor_id: str
    group_id: int
    field_id: int
    name: str
    description: str
    unit: str | None
    range_text: str | None
    raw_fields: tuple[str, ...]
    source_file: str
    source_line: int
    source_sha256: str | None
    ecu_candidates: tuple[str, ...]
    exact_afn_label: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "group_id": self.group_id,
            "field_id": self.field_id,
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "range_text": self.range_text,
            "raw_fields": list(self.raw_fields),
            "status": "METADATA_ONLY",
            "group_status": "ACTIVE_ALLOWED" if self.group_id in SUPPORTED_GROUP_IDS else "METADATA_ONLY",
            "decoder_id": None,
            "live_readable": False,
            "physical_read_allowed": self.group_id in SUPPORTED_GROUP_IDS,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_sha256": self.source_sha256,
            "ecu_candidates": list(self.ecu_candidates),
            "exact_afn_label": self.exact_afn_label,
        }


class LabelMetadataCatalog:
    """Searchable, bounded view of label rows and universal group states."""

    def __init__(self, candidates: tuple[LabelSensorCandidate, ...]):
        self._candidates = candidates
        counts: dict[int, int] = {}
        for item in candidates:
            counts[item.group_id] = counts.get(item.group_id, 0) + 1
        self._counts = counts
        self._group_labels = {
            group.group_id: group.label for group in default_afn_groups()
        }

    @classmethod
    def from_inventory(cls, inventory: LabelInventory) -> "LabelMetadataCatalog":
        candidates: list[LabelSensorCandidate] = []
        for document in inventory.files:
            if not document.loaded:
                continue
            path_key = document.relative_path.replace("\\", "/").casefold()
            path_digest = hashlib.sha256(path_key.encode("utf-8")).hexdigest()[:12]
            stem = document.relative_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
            exact_afn_label = any(
                stem == target.casefold()
                for target in ("028-906-021-AFN", "038-906-012-AFN", "038-906-018-AFN")
            )
            ecu_candidates = _stable_unique((*document.engine_codes, *document.ecu_references))
            for field in document.fields:
                candidates.append(_candidate(document.relative_path, document.sha256, field, ecu_candidates, path_digest, exact_afn_label))
        candidates.sort(key=lambda item: (item.source_file.casefold(), item.source_file, item.source_line))
        return cls(tuple(candidates))

    @property
    def candidate_count(self) -> int:
        return len(self._candidates)

    @property
    def group_count(self) -> int:
        return len(self._counts)

    def conflicts(self) -> list[dict[str, Any]]:
        by_field: dict[tuple[int, int], list[LabelSensorCandidate]] = {}
        for candidate in self._candidates:
            by_field.setdefault((candidate.group_id, candidate.field_id), []).append(candidate)
        conflicts = []
        for (group_id, field_id), candidates in sorted(by_field.items()):
            variants = {
                (item.name.casefold(), (item.unit or "").casefold(), (item.range_text or "").casefold(),
                 tuple(value.casefold() for value in item.ecu_candidates))
                for item in candidates
            }
            if len(variants) > 1:
                conflicts.append({
                    "group_id": group_id,
                    "field_id": field_id,
                    "candidate_count": len(candidates),
                    "variant_count": len(variants),
                    "source_files": sorted({item.source_file for item in candidates}, key=str.casefold),
                })
        return conflicts

    def groups(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        allowed = set(SUPPORTED_GROUP_IDS)
        for group_id in range(256):
            count = self._counts.get(group_id, 0)
            if group_id in allowed:
                status = "ACTIVE_ALLOWED"
            elif count:
                status = "METADATA_ONLY"
            else:
                status = "UNKNOWN"
            result.append({
                "id": group_id,
                "label": self._group_labels.get(group_id, f"Grupo {group_id:03d}"),
                "status": status,
                "metadata_candidate_count": count,
                "physical_read_allowed": group_id in allowed,
                "decoder_available": False,
            })
        return result

    def query(
        self,
        *,
        query: str = "",
        group_id: int | None = None,
        status: str | None = None,
        group_status: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            raise LabelQueryError(f"A pesquisa tem de ter até {MAX_QUERY_CHARS} caracteres.")
        if group_id is not None and (type(group_id) is not int or not 0 <= group_id <= 255):
            raise LabelQueryError("O grupo tem de ser um inteiro entre 000 e 255.")
        if status not in (None, "METADATA_ONLY"):
            raise LabelQueryError("O único estado de linhas importadas é METADATA_ONLY.")
        if group_status not in (None, "ACTIVE_ALLOWED", "METADATA_ONLY", "UNKNOWN"):
            raise LabelQueryError("Estado do grupo inválido.")
        if type(page) is not int or page < 1:
            raise LabelQueryError("A página tem de ser um inteiro positivo.")
        if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise LabelQueryError(f"O tamanho da página tem de estar entre 1 e {MAX_PAGE_SIZE}.")

        folded = query.casefold().strip()
        matched = []
        if group_status == "UNKNOWN":
            return {"total": 0, "page": page, "page_size": page_size, "items": []}
        for candidate in self._candidates:
            if group_id is not None and candidate.group_id != group_id:
                continue
            candidate_group_status = (
                "ACTIVE_ALLOWED" if candidate.group_id in SUPPORTED_GROUP_IDS else "METADATA_ONLY"
            )
            if group_status is not None and candidate_group_status != group_status:
                continue
            haystack = " ".join((
                candidate.name,
                candidate.description,
                candidate.source_file,
                candidate.unit or "",
                candidate.range_text or "",
                *candidate.ecu_candidates,
            )).casefold()
            if folded and folded not in haystack:
                continue
            matched.append(candidate)

        total = len(matched)
        offset = (page - 1) * page_size
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [item.as_dict() for item in matched[offset:offset + page_size]],
        }


def _candidate(
    source_file: str,
    sha256: str | None,
    field: LabelField,
    ecu_candidates: tuple[str, ...],
    path_digest: str,
    exact_afn_label: bool,
) -> LabelSensorCandidate:
    values = field.raw_fields[2:]
    name = values[0].strip() if values and values[0].strip() else f"Campo {field.field_id:03d}"
    description = ", ".join(value.strip() for value in values if value.strip())
    sensor_id = (
        f"label.{path_digest}.g{field.group_id:03d}.f{field.field_id:03d}.l{field.source_line:06d}"
    )
    return LabelSensorCandidate(
        sensor_id=sensor_id,
        group_id=field.group_id,
        field_id=field.field_id,
        name=name,
        description=description,
        unit=field.unit,
        range_text=field.range_text,
        raw_fields=field.raw_fields,
        source_file=source_file,
        source_line=field.source_line,
        source_sha256=sha256,
        ecu_candidates=ecu_candidates,
        exact_afn_label=exact_afn_label,
    )


def _stable_unique(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            result.append(normalized)
    return tuple(result)
