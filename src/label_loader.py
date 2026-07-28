from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Tuple


def find_matching_label_file(ecu_token: str, label_dir: Path | str | None = None) -> Optional[Path]:
    """Find the best matching .lbl file for an ECU token, ignoring case and punctuation."""
    if label_dir is None:
        label_dir = Path(__file__).resolve().parents[1] / "#Labels"
    label_dir = Path(label_dir)

    if not label_dir.exists():
        return None

    token = re.sub(r"[^0-9A-Za-z]+", "", ecu_token).lower()
    candidates = []
    for path in sorted(label_dir.glob("*.lbl")):
        name = re.sub(r"[^0-9A-Za-z]+", "", path.stem).lower()
        if token in name:
            candidates.append((len(name), path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def load_label_catalog(label_dir: Path | str | None = None) -> Dict[str, Dict[int, Dict[int, str]]]:
    """Load a simple catalog of group -> field -> label strings from .lbl files."""
    if label_dir is None:
        label_dir = Path(__file__).resolve().parents[1] / "#Labels"
    label_dir = Path(label_dir)

    catalog: Dict[str, Dict[int, Dict[int, str]]] = {}
    if not label_dir.exists():
        return catalog

    for label_file in sorted(label_dir.glob("*.lbl")):
        group_fields: Dict[int, Dict[int, str]] = {}
        for line in label_file.read_text(encoding="latin-1", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue

            match = re.match(r"^(\d{3}),(\d+),(.+)$", line)
            if not match:
                continue

            group = int(match.group(1))
            field_index = int(match.group(2))
            label = match.group(3).strip()
            group_fields.setdefault(group, {})[field_index] = label

        if group_fields:
            catalog[label_file.stem] = group_fields
            catalog[label_file.stem.lower()] = group_fields

    return catalog


def get_group_field_labels(group_number: int, group_catalog: Dict[int, Dict[int, str]]) -> Dict[int, str]:
    """Return the labels for a measuring block group from the parsed label file."""
    return group_catalog.get(group_number, {})
