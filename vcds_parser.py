"""Leitor de logs VCDS clássicos (Group A/B/C), sem dependências externas.

Importante: diferentes ECUs podem usar outros significados para os grupos.
Mapeamento AFN é uma *hipótese inicial* e nunca substitui validação de unidades.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


FIELDS = (
    "rpm", "boost_actual_mbar", "boost_requested_mbar", "atmospheric_mbar",
    "maf_actual_mg", "maf_requested_mg", "coolant_c", "iq_mg", "n75_percent",
    "fuel_temp_c",
)
FIELD_LABELS = {
    "rpm": "RPM", "boost_actual_mbar": "Turbo real (mbar abs)",
    "boost_requested_mbar": "Turbo pedido (mbar abs)",
    "atmospheric_mbar": "Pressão atmosférica (mbar)",
    "maf_actual_mg": "MAF real (mg/str)", "maf_requested_mg": "MAF pedido (mg/str)",
    "coolant_c": "Temperatura água (°C)", "iq_mg": "Quantidade injetada (mg/str)",
    "n75_percent": "N75 (%)", "fuel_temp_c": "Temperatura combustível (°C)",
}


def number(text: str) -> Optional[float]:
    raw = str(text).strip().replace("\u00a0", "")
    if not raw:
        return None
    try:
        return float(raw.replace(",", "."))
    except (ValueError, TypeError):
        return None


def normalise(s: str) -> str:
    return " ".join(str(s).lower().replace(".", " ").replace("-", " ").split())


@dataclass
class Column:
    index: int
    group: Optional[int]
    field: int
    description: str
    unit: str

    @property
    def display(self) -> str:
        g = f"G{self.group:03}" if self.group is not None else "?"
        return f"{g} · campo {self.field} · {self.description or 'Sem nome'} [{self.unit or '—'}]"


@dataclass
class Sample:
    time: float
    values: dict[str, float]
    raw: list[str]


class VCDSLog:
    def __init__(self, columns: list[Column], samples: list[Sample], source: str = ""):
        self.columns = columns
        self.samples = samples
        self.source = source
        self.mapping = suggest_mapping(columns)

    def mapped(self, sample: Sample, mapping: Optional[dict[str, int]] = None) -> dict[str, float]:
        selected = mapping if mapping is not None else self.mapping
        out: dict[str, float] = {}
        for name, idx in selected.items():
            value = sample.values.get(str(idx))
            if value is not None:
                out[name] = value
        return out


def _parse_rows(rows: list[list[str]], source: str = "") -> VCDSLog:
    groups_row = None
    data_start = None
    for n, row in enumerate(rows):
        if any(re.search(r"(?:group|block)\s+[abc]\s*:", v, flags=re.I) for v in row):
            groups_row = n
            break
    if groups_row is None:
        raise ValueError("Não encontrei cabeçalho VCDS 'Group A/B/C'. Confirma se é um CSV de Measuring Blocks.")

    group_line = rows[groups_row]
    # Índices dos campos TIME dos grupos: 1, 6, 11 normalmente.
    starts: list[tuple[int, Optional[int]]] = []
    for i, value in enumerate(group_line):
        if re.search(r"(?:group|block)\s+[abc]\s*:", value, flags=re.I):
            v = group_line[i + 1].strip() if i + 1 < len(group_line) else ""
            match = re.search(r"\d{1,3}", v)
            starts.append((i, int(match.group()) if match else None))
    if not starts:
        raise ValueError("Cabeçalho de grupos inválido.")

    # Cabeçalhos contêm 1 ou 2 linhas de descrição antes da linha MARKER,STAMP.
    stamp_row = None
    for n in range(groups_row + 1, min(groups_row + 6, len(rows))):
        if len(rows[n]) >= 2 and rows[n][0].strip().lower() in ("marker", "markierung", "marcador") and rows[n][1].strip().upper() == "STAMP":
            stamp_row = n
            break
    if stamp_row is None:
        for n in range(groups_row + 1, min(groups_row + 7, len(rows))):
            if len(rows[n]) >= 2 and rows[n][1].strip().upper() == "STAMP":
                stamp_row = n
                break
    if stamp_row is None:
        raise ValueError("Não encontrei linha STAMP no CSV VCDS.")
    data_start = stamp_row + 1

    names_rows = rows[groups_row + 1:stamp_row]
    unit_row = rows[stamp_row]
    columns: list[Column] = []
    for pos, (start, group) in enumerate(starts):
        if group is None:
            continue
        boundary = starts[pos + 1][0] if pos + 1 < len(starts) else min(len(group_line), start + 5)
        for idx in range(start + 1, min(start + 5, boundary)):
            # Campo 1 começa em start+1. O marcador do grupo está na coluna TIME.
            fragments = [row[idx].strip() for row in names_rows if idx < len(row) and row[idx].strip()]
            desc = " ".join(fragments).strip()
            unit = unit_row[idx].strip() if idx < len(unit_row) else ""
            columns.append(Column(idx, group, idx - start, desc, unit))

    samples: list[Sample] = []
    for row in rows[data_start:]:
        if not row:
            continue
        # Ignorar cabeçalhos repetidos ao anexar outra sessão no mesmo ficheiro.
        if any("Group A:" in x or "Group B:" in x for x in row):
            continue
        timestamps = [number(row[start]) for start, g in starts if start < len(row) and g is not None]
        timestamps = [v for v in timestamps if v is not None]
        if not timestamps:
            continue
        vals: dict[str, float] = {}
        for c in columns:
            if c.index < len(row):
                v = number(row[c.index])
                if v is not None:
                    vals[str(c.index)] = v
        if vals:
            samples.append(Sample(time=timestamps[0], values=vals, raw=row))
    return VCDSLog(columns, samples, source)


def parse_text(text: str, source: str = "") -> VCDSLog:
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text), delimiter=",")
    return _parse_rows(list(reader), source)


def load_log(path: str | Path) -> VCDSLog:
    path = Path(path)
    content = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return parse_text(content.decode(encoding), str(path))
        except UnicodeDecodeError:
            continue
    raise ValueError("Codificação do CSV não reconhecida.")


def suggest_mapping(columns: list[Column]) -> dict[str, int]:
    """Regras preferenciais AFN/EDC15, identificadas pelo grupo e unidade.

    O utilizador pode corrigir cada associação na interface. Nunca confundir
    pressão absoluta em mbar com pressão relativa de turbo.
    """
    mapping: dict[str, int] = {}
    by = {(c.group, c.field): c for c in columns}
    # Grupos comuns para AFN; os títulos/unidades devem ser confirmados.
    preferred = {
        "rpm": [(11, 1), (3, 1), (1, 1), (4, 1)],
        "boost_requested_mbar": [(11, 2)],
        "boost_actual_mbar": [(11, 3)],
        "n75_percent": [(11, 4)],
        "maf_requested_mg": [(3, 2)],
        "maf_actual_mg": [(3, 3)],
        "coolant_c": [(1, 4), (7, 4)],
        "iq_mg": [(1, 2)],
        "atmospheric_mbar": [(10, 2)],
        "fuel_temp_c": [(7, 1)],
    }
    for key, candidates in preferred.items():
        for choice in candidates:
            c = by.get(choice)
            if c is None:
                continue
            u = normalise(c.unit)
            if "mbar" in key and "mbar" not in u and "hpa" not in u:
                continue
            if "percent" in key and "%" not in c.unit:
                continue
            if "maf" in key and "mg" not in u:
                continue
            if key == "coolant_c" and "c" not in u and "°" not in u:
                continue
            mapping[key] = c.index
            break
    # Remapeamento semântico se há logs de outros grupos e nomes claros.
    for c in columns:
        desc = normalise(c.description)
        unit = normalise(c.unit)
        if "rpm" not in mapping and ("engine speed" in desc or "rpm" in desc) and ("min" in unit or "rpm" in unit):
            mapping["rpm"] = c.index
        if "coolant_c" not in mapping and ("coolant" in desc or "refrigerante" in desc or "agua" in desc) and "c" in unit:
            mapping["coolant_c"] = c.index
        if "atmospheric_mbar" not in mapping and ("atmos" in desc or "ambient pressure" in desc) and ("mbar" in unit or "hpa" in unit):
            mapping["atmospheric_mbar"] = c.index
    return mapping
