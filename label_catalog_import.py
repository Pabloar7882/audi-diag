"""Explicit local label import and bounded change history.

The history records file hashes and import summaries only; it never copies
label row text or changes the canonical sensor definitions/physical policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Protocol

from label_catalog import MAX_FILES, LabelInventory, scan_labels
from label_catalog_integration import LabelMetadataCatalog


MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_HISTORY_EVENTS = 10
MAX_EVENT_NAME_SAMPLES = 100


class ImportHistoryError(ValueError):
    """Raised when the local change-history file is invalid or unavailable."""


class HistoryStore(Protocol):
    def load(self) -> dict[str, Any]: ...
    def save(self, state: dict[str, Any]) -> None: ...


class JsonImportHistoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "files": {}, "events": []}
        try:
            if self.path.stat().st_size > MAX_HISTORY_BYTES:
                raise ImportHistoryError("O histórico de importação excede o limite local.")
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except ImportHistoryError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ImportHistoryError(f"Não foi possível ler o histórico local: {exc}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ImportHistoryError("O histórico de importação tem um formato desconhecido.")
        if not isinstance(state.get("files"), dict) or not isinstance(state.get("events"), list):
            raise ImportHistoryError("O histórico local está incompleto.")
        if len(state["files"]) > MAX_FILES or len(state["events"]) > MAX_HISTORY_EVENTS:
            raise ImportHistoryError("O histórico local excede os limites permitidos.")
        for name, metadata in state["files"].items():
            if not isinstance(name, str) or not name or len(name) > 512 or not isinstance(metadata, dict):
                raise ImportHistoryError("O histórico local contém um ficheiro inválido.")
            digest = metadata.get("sha256")
            if digest is not None and (
                not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest.casefold())
            ):
                raise ImportHistoryError("O histórico local contém um hash inválido.")
        if any(not isinstance(event, dict) for event in state["events"]):
            raise ImportHistoryError("O histórico local contém um evento inválido.")
        return state

    def save(self, state: dict[str, Any]) -> None:
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_HISTORY_BYTES:
            raise ImportHistoryError("O histórico de importação excede o limite local.")
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(encoded, encoding="utf-8", newline="\n")
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ImportHistoryError(f"Não foi possível guardar o histórico local: {exc}") from exc


class LabelCatalogImporter:
    def __init__(self, root: Path, store: HistoryStore):
        self.root = Path(root)
        self.store = store
        self._lock = threading.Lock()

    def import_now(self) -> tuple[LabelMetadataCatalog, dict[str, Any]]:
        with self._lock:
            inventory = scan_labels(self.root)
            catalog = LabelMetadataCatalog.from_inventory(inventory)
            previous = self.store.load()
            updated, report = build_import_history(inventory, catalog, previous)
            self.store.save(updated)
            return catalog, report


def build_import_history(
    inventory: LabelInventory,
    catalog: LabelMetadataCatalog,
    previous: dict[str, Any] | None = None,
    *,
    imported_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    previous = previous or {"schema_version": 1, "files": {}, "events": []}
    if previous.get("schema_version") != 1 or not isinstance(previous.get("files"), dict) or not isinstance(previous.get("events"), list):
        raise ImportHistoryError("O histórico anterior não satisfaz o esquema local v1.")

    current_files: dict[str, dict[str, Any]] = {}
    for item in inventory.files:
        current_files[item.relative_path] = {
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "loaded": item.loaded,
            "format": item.encoding,
        }
    old_files = previous["files"]
    added = sorted(set(current_files) - set(old_files), key=str.casefold)
    removed = sorted(set(old_files) - set(current_files), key=str.casefold)
    changed = sorted(
        (
            name for name in set(current_files).intersection(old_files)
            if current_files[name] != old_files[name]
        ),
        key=str.casefold,
    )
    unchanged = len(current_files) - len(added) - len(changed)
    timestamp = imported_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    conflicts = catalog.conflicts()
    changed_any = bool(added or changed or removed)
    history_event_recorded = changed_any or not previous["events"]
    event = {
        "imported_at_utc": timestamp,
        "files_scanned": len(inventory.files),
        "files_loaded": sum(1 for item in inventory.files if item.loaded),
        "added": added[:MAX_EVENT_NAME_SAMPLES],
        "changed": changed[:MAX_EVENT_NAME_SAMPLES],
        "removed": removed[:MAX_EVENT_NAME_SAMPLES],
        "file_name_samples_truncated": any(len(items) > MAX_EVENT_NAME_SAMPLES for items in (added, changed, removed)),
        "added_count": len(added),
        "changed_count": len(changed),
        "removed_count": len(removed),
        "unchanged_count": unchanged,
        "metadata_rows": catalog.candidate_count,
        "group_count": catalog.group_count,
        "conflict_count": len(conflicts),
        "conflicts": conflicts[:100],
        "warning_count": len(inventory.warnings),
    }
    events = list(previous["events"])
    if history_event_recorded:
        events.append(event)
    events = events[-MAX_HISTORY_EVENTS:]
    state = {
        "schema_version": 1,
        "last_checked_at_utc": timestamp,
        "files": current_files,
        "events": events,
    }
    report = {key: value for key, value in event.items() if key not in {"added", "changed", "removed", "conflicts"}}
    report["added_sample"] = event["added"][:20]
    report["changed_sample"] = event["changed"][:20]
    report["removed_sample"] = event["removed"][:20]
    report["files_scanned"] = event["files_scanned"]
    report["files_loaded"] = event["files_loaded"]
    report["warnings"] = list(inventory.warnings[:20])
    report["conflicts"] = conflicts[:20]
    report["history_event_recorded"] = history_event_recorded
    return state, report
