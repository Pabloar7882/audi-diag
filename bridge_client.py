"""Ligação SOMENTE LEITURA ao TCP localhost do KWPBridge (experimental em AFN).

A aplicação nunca envia comandos de escrita, codificação, adaptação ou limpeza de erros.
A ponte de terceiros faz a gestão da porta COM, pelo que não abrir VCDS simultaneamente.
"""
from __future__ import annotations

import json
import socket
from typing import Any
from vcds_parser import Column, suggest_mapping

HOST, PORT = "127.0.0.1", 50266


def decode_state(message: dict[str, Any]) -> tuple[dict[str, float], str] | None:
    kind = message.get("type", "")
    if kind == "state":
        data = message.get("data") or {}
    elif "groups" in message:
        data = message
    else:
        return None
    groups = data.get("groups") or {}
    columns: list[Column] = []
    raw: dict[str, float] = {}
    next_idx = 0
    for group_id, payload in groups.items():
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            continue
        cells = payload.get("cells", []) if isinstance(payload, dict) else []
        for k, cell in enumerate(cells, 1):
            if not isinstance(cell, dict):
                continue
            try:
                value = float(cell["value"])
                index = int(cell.get("index", k))
            except (ValueError, TypeError, KeyError):
                continue
            columns.append(Column(next_idx, gid, index, str(cell.get("label", "")), str(cell.get("unit", ""))))
            raw[str(next_idx)] = value
            next_idx += 1
    mapped = suggest_mapping(columns)
    result = {key: raw[str(idx)] for key, idx in mapped.items() if str(idx) in raw}
    part = (data.get("ecu_id") or {}).get("part_number") or "ECU por identificar"
    status = f"K-Line · {part} · {data.get('protocol', '?')}"
    err = data.get("error")
    if err and not result:
        status = f"Ponte: {err}"
    return result, status


def read_stream(stop_event, on_data, on_status) -> None:
    """Loop de receção; socket localhost apenas. Callbacks chamados na thread."""
    sock = None
    try:
        on_status("A ligar à ponte TCP local…")
        sock = socket.create_connection((HOST, PORT), timeout=3.0)
        sock.settimeout(1.0)
        # Apenas comando de consulta de estado, sem mutações.
        sock.sendall(b'{"cmd":"get_state"}\n')
        buffer = b""
        on_status("Ponte ligada; à espera dos dados da ECU…")
        while not stop_event.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                on_status("A ponte TCP terminou a ligação.")
                break
            buffer += chunk
            if len(buffer) > 2_000_000:
                raise ValueError("Mensagem TCP demasiado grande.")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue
                state = decode_state(message)
                if state:
                    values, status = state
                    on_status(status)
                    if values:
                        on_data(values)
    except (OSError, ValueError) as exc:
        if not stop_event.is_set():
            on_status(f"Ponte indisponível: {exc}")
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass
