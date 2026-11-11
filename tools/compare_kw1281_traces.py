"""Compare offline Windows/Android FIS AFN v1 diagnostic traces."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA = "fis-afn-diagnostic-trace/v1"
RAW_DIRECTIONS = ("tx", "rx")
FRAME_DIRECTIONS = ("tx_frame", "rx_frame")
CONFIG_FIELDS = (
    "device_name", "vendor_id", "product_id", "baudrate", "data_bits",
    "parity", "stop_bits", "local_echo", "ecu_part_number",
)
ACK_ERRORS = {"byte_ack_invalid", "tx_echo_invalid", "ack_invalid", "complement_invalid"}
FRAMING_ERRORS = {"block_length_invalid", "block_counter_invalid", "block_end_invalid", "frame_invalid"}


class TraceInputError(ValueError):
    """The input is not a supported, well-formed v1 diagnostic trace."""


def load_trace(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        return load_trace_text(source.read_text(encoding="utf-8-sig"), source.suffix, str(source))
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise TraceInputError(f"cannot read {source}: {exc}") from exc


def load_trace_text(text: str, extension: str, name: str = "<memory>") -> dict[str, Any]:
    source = Path(name)
    normalized_extension = extension if extension.startswith(".") else f".{extension}"
    if normalized_extension.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TraceInputError(f"{source}: invalid JSON: {exc}") from exc
        return _load_json_payload(source, payload)
    if normalized_extension.lower() == ".csv":
        try:
            return _load_csv_text(source, text)
        except csv.Error as exc:
            raise TraceInputError(f"{source}: invalid CSV: {exc}") from exc
    raise TraceInputError(f"unsupported file extension for {source}; use .json or .csv")


def compare_trace_documents(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left = _prepare_document(left)
    right = _prepare_document(right)
    left_events = left["events"]
    right_events = right["events"]
    left_frames = _semantic_frames(left_events)
    right_frames = _semantic_frames(right_events)
    frames_compared = bool(left_frames and right_frames)
    frame_sequences_equal: bool | None = None
    first_frame_difference = None
    if frames_compared:
        frame_sequences_equal, first_frame_difference = _compare_sequences(left_frames, right_frames, "frame")

    byte_comparison = {
        direction: _compare_bytes(_raw_stream(left_events, direction), _raw_stream(right_events, direction))
        for direction in RAW_DIRECTIONS
    }
    config_mismatches, config_unknown = _compare_configuration(left.get("sessions", []), right.get("sessions", []))
    left_findings = _findings(left_events, left_frames)
    right_findings = _findings(right_events, right_frames)
    return {
        "left": _summary(left, left_findings),
        "right": _summary(right, right_findings),
        "frames_compared": frames_compared,
        "frame_sequences_equal": frame_sequences_equal,
        "first_frame_difference": first_frame_difference,
        "byte_comparison": byte_comparison,
        "config_mismatches": config_mismatches,
        "config_unknown": config_unknown,
        "report": _render_report(
            left, right, frames_compared, frame_sequences_equal, first_frame_difference,
            byte_comparison, config_mismatches, config_unknown,
            _summary(left, left_findings),
            _summary(right, right_findings),
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare FIS AFN Windows/Android KW1281 JSON or CSV traces offline."
    )
    parser.add_argument("reference", help="reference trace (.json or .csv)")
    parser.add_argument("candidate", help="candidate trace (.json or .csv)")
    args = parser.parse_args(argv)
    try:
        report = compare_trace_documents(load_trace(args.reference), load_trace(args.candidate))["report"]
    except TraceInputError as exc:
        print(f"trace input error: {exc}", file=sys.stderr)
        return 2
    print(report)
    return 0


def _load_json_payload(source: Path, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise TraceInputError(f"{source}: expected JSON schema {SCHEMA!r}")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise TraceInputError(f"{source}: 'events' must be a JSON array")
    raw_sessions = payload.get("sessions", [])
    if not isinstance(raw_sessions, list) or any(not isinstance(item, dict) for item in raw_sessions):
        raise TraceInputError(f"{source}: 'sessions' must be an array of objects")
    return {
        "name": str(source),
        "platform": payload.get("platform"),
        "sessions": [dict(item) for item in raw_sessions],
        "events": [_normalize_event(event, index, source) for index, event in enumerate(raw_events)],
    }


def _prepare_document(document: dict[str, Any]) -> dict[str, Any]:
    """Accept either loader output or a v1-shaped in-memory document."""
    source = Path(str(document.get("name", "<memory>")))
    events = document.get("events")
    if not isinstance(events, list):
        raise TraceInputError(f"{source}: 'events' must be an array")
    normalized = []
    for index, event in enumerate(events):
        if isinstance(event, dict) and isinstance(event.get("data"), bytes):
            normalized.append(event)
        else:
            normalized.append(_normalize_event(event, index, source))
    sessions = document.get("sessions", [])
    if not isinstance(sessions, list) or any(not isinstance(item, dict) for item in sessions):
        raise TraceInputError(f"{source}: 'sessions' must be an array of objects")
    return {
        "name": str(document.get("name", "<memory>")),
        "platform": document.get("platform"),
        "sessions": sessions,
        "events": normalized,
    }


def _load_csv(source: Path) -> dict[str, Any]:
    with source.open("r", newline="", encoding="utf-8-sig") as stream:
        return _load_csv_text(source, stream.read())


def _load_csv_text(source: Path, text: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = set(reader.fieldnames or ())
    if not {"direction", "data_hex"}.issubset(fields):
        raise TraceInputError(f"{source}: CSV must include direction and data_hex columns")
    raw_events = list(reader)
    events = [_normalize_event(event, index, source) for index, event in enumerate(raw_events)]
    sessions_by_id: dict[str, dict[str, Any]] = {}
    session_fields = {
        "device_name", "vendor_id", "product_id", "baudrate", "data_bits", "parity",
        "stop_bits", "local_echo", "ecu_part_number", "ecu_component", "ecu_software_version",
        "driver", "session_id",
    }
    for row in raw_events:
        session_id = _text(row.get("session_id"))
        if not session_id:
            continue
        session = sessions_by_id.setdefault(session_id, {"id": session_id})
        for field in session_fields - {"session_id"}:
            value = row.get(field)
            if value not in (None, ""):
                session[field] = _scalar(field, value)
    return {
        "name": str(source),
        "platform": None,
        "sessions": list(sessions_by_id.values()),
        "events": events,
    }


def _normalize_event(raw: Any, index: int, source: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TraceInputError(f"{source}: event {index} must be an object")
    direction = _text(raw.get("direction"))
    if not direction:
        raise TraceInputError(f"{source}: event {index} has no direction")
    encoded = _text(raw.get("data_hex")) or ""
    compact_hex = "".join(encoded.split())
    try:
        if len(compact_hex) % 2:
            raise ValueError("hex data must contain whole bytes")
        data = bytes.fromhex(compact_hex)
    except ValueError as exc:
        raise TraceInputError(f"{source}: event {index} has invalid data_hex: {exc}") from exc
    sequence = _optional_number(raw.get("sequence"))
    return {
        "sequence": sequence if sequence is not None else index,
        "session_id": _text(raw.get("session_id")) or None,
        "timestamp": _optional_number(raw.get("timestamp")),
        "monotonic": _optional_number(raw.get("monotonic")),
        "direction": direction.lower(),
        "phase": _text(raw.get("phase")) or "",
        "data": data,
        "result": _text(raw.get("result")) or "",
        "error": _text(raw.get("error")) or "",
        "interpretation": _text(raw.get("interpretation")) or "",
        "origin": _text(raw.get("origin")) or "",
    }


def _semantic_frames(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["direction"] in FRAME_DIRECTIONS]


def _findings(events: list[dict[str, Any]], frames: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    explicit_timeouts = 0
    empty_reads = 0
    ack_errors = 0
    framing_errors = 0
    for event in events:
        code = event["error"].lower()
        if code:
            errors.append({"phase": event["phase"], "code": code, "message": event["result"]})
            if code in ACK_ERRORS or "ack" in code or "complement" in code or "echo" in code:
                ack_errors += 1
            if code in FRAMING_ERRORS or any(token in code for token in ("block_length", "block_counter", "block_end", "frame_invalid")):
                framing_errors += 1
            if "timeout" in code:
                explicit_timeouts += 1
        if event["direction"] == "rx" and not event["data"] and "empty_read" in event["result"].lower():
            empty_reads += 1

    identity_frames = 0
    measurement_frames = 0
    invalid_frames: list[dict[str, str]] = []
    previous_counter: int | None = None
    previous_session: str | None = None
    for event in frames:
        if event["session_id"] != previous_session:
            previous_counter = None
            previous_session = event["session_id"]
        raw = event["data"]
        if len(raw) < 4:
            invalid_frames.append({"phase": event["phase"], "error": "frame_length_too_short"})
            framing_errors += 1
            continue
        declared_length = raw[0]
        if declared_length != len(raw) - 1:
            invalid_frames.append({"phase": event["phase"], "error": "block_length_invalid"})
            framing_errors += 1
        if raw[-1] != 0x03:
            invalid_frames.append({"phase": event["phase"], "error": "block_end_invalid"})
            framing_errors += 1
        counter = raw[1]
        title = raw[2]
        if previous_counter is not None and counter != ((previous_counter + 1) & 0xFF):
            invalid_frames.append({"phase": event["phase"], "error": "block_counter_invalid"})
            framing_errors += 1
        previous_counter = counter
        identity_frames += title == 0xF6
        measurement_frames += title == 0xE7

    return {
        "events": len(events),
        "semantic_frames": len(frames),
        "identity_frames": identity_frames,
        "measurement_frames": measurement_frames,
        "timeouts": max(explicit_timeouts, empty_reads),
        "ack_errors": ack_errors,
        "framing_errors": framing_errors,
        "errors": errors[:12],
        "invalid_frames": invalid_frames[:12],
        "duration_s": _duration(events),
    }


def _summary(document: dict[str, Any], findings: dict[str, Any]) -> dict[str, Any]:
    return {
        **findings,
        "name": document["name"],
        "platform": document.get("platform"),
        "sessions": document.get("sessions", []),
    }


def _compare_sequences(left: list[dict[str, Any]], right: list[dict[str, Any]], label: str) -> tuple[bool, dict[str, Any] | None]:
    count = min(len(left), len(right))
    for index in range(count):
        left_key = (left[index]["direction"], left[index]["data"])
        right_key = (right[index]["direction"], right[index]["data"])
        if left_key != right_key:
            return False, {
                "index": index,
                "reference_description": _frame_description(left[index]),
                "candidate_description": _frame_description(right[index]),
                "kind": label,
            }
    if len(left) != len(right):
        return False, {
            "index": count,
            "reference_description": _frame_description(left[count]) if len(left) > count else "<end>",
            "candidate_description": _frame_description(right[count]) if len(right) > count else "<end>",
            "kind": label,
        }
    return True, None


def _frame_description(event: dict[str, Any]) -> str:
    raw = event["data"]
    if len(raw) < 3:
        return f"{event['direction']} frame length={len(raw)}"
    return f"{event['direction']} length={len(raw)} counter={raw[1]} title=0x{raw[2]:02x}"


def _compare_bytes(left: bytes, right: bytes) -> dict[str, Any]:
    limit = min(len(left), len(right))
    offset = next((index for index in range(limit) if left[index] != right[index]), None)
    if offset is None and len(left) != len(right):
        offset = limit
    first_difference = None
    if offset is not None:
        first_difference = {
            "offset": offset,
            "reference": f"0x{left[offset]:02x}" if offset < len(left) else "<end>",
            "candidate": f"0x{right[offset]:02x}" if offset < len(right) else "<end>",
        }
    return {
        "equal": left == right,
        "reference_bytes": len(left),
        "candidate_bytes": len(right),
        "first_difference": first_difference,
    }


def _raw_stream(events: list[dict[str, Any]], direction: str) -> bytes:
    return b"".join(event["data"] for event in events if event["direction"] == direction)


def _compare_configuration(left_sessions: list[dict[str, Any]], right_sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    left = left_sessions[-1] if left_sessions else {}
    right = right_sessions[-1] if right_sessions else {}
    mismatches = []
    unknown = []
    for field in CONFIG_FIELDS:
        left_value = left.get(field)
        right_value = right.get(field)
        if left_value in (None, "") or right_value in (None, ""):
            if left_value != right_value:
                unknown.append(field)
            continue
        if left_value != right_value:
            mismatches.append({"field": field, "reference": left_value, "candidate": right_value})
    return mismatches, unknown


def _render_report(
    left_doc: dict[str, Any], right_doc: dict[str, Any], frames_compared: bool,
    frame_sequences_equal: bool | None, first_frame_difference: dict[str, Any] | None,
    byte_comparison: dict[str, Any], config_mismatches: list[dict[str, Any]], config_unknown: list[str],
    left: dict[str, Any], right: dict[str, Any],
) -> str:
    lines = [
        "KW1281 trace comparison",
        f"Reference: {left['name']} ({left['events']} events; {_duration_text(left['duration_s'])})",
        f"Candidate: {right['name']} ({right['events']} events; {_duration_text(right['duration_s'])})",
    ]
    if config_mismatches:
        lines.append("Configuration differences: " + "; ".join(
            f"{item['field']}={item['reference']!r} vs {item['candidate']!r}" for item in config_mismatches
        ))
    else:
        lines.append("Configuration differences: none among fields present in both traces.")
    if config_unknown:
        lines.append("Configuration unavailable on one or both sides: " + ", ".join(sorted(set(config_unknown))) + ".")

    if frames_compared:
        lines.append(f"Semantic frame sequences: {'equal' if frame_sequences_equal else 'different'} ({left['semantic_frames']} vs {right['semantic_frames']} frames).")
        if first_frame_difference:
            lines.append(
                "First frame difference at index {index}: reference {reference_description}; "
                "candidate {candidate_description}.".format(**first_frame_difference)
            )
    else:
        lines.append("Semantic frame comparison: unavailable on at least one side; using raw directional byte streams.")

    for direction, label in (("tx", "TX"), ("rx", "RX")):
        result = byte_comparison[direction]
        line = f"Raw {label} streams: {'equal' if result['equal'] else 'different'} ({result['reference_bytes']} vs {result['candidate_bytes']} bytes)."
        if result["first_difference"]:
            diff = result["first_difference"]
            line += f" First difference at byte {diff['offset']}: {diff['reference']} vs {diff['candidate']}."
        lines.append(line)

    lines.append(
        "Blocks: reference F6 identity={}; E7 measurements={}; candidate F6 identity={}; E7 measurements={}.".format(
            left["identity_frames"], left["measurement_frames"], right["identity_frames"], right["measurement_frames"]
        )
    )
    for label, summary in (("Reference", left), ("Candidate", right)):
        lines.append(
            f"{label} findings: ACK/complement errors={summary['ack_errors']}; timeouts={summary['timeouts']}; framing/counter/ETX errors={summary['framing_errors']}."
        )
        for error in summary["errors"]:
            detail = f" ({error['message']})" if error["message"] else ""
            lines.append(f"  {error['phase'] or 'unknown phase'}: {error['code']}{detail}")
        for error in summary["invalid_frames"]:
            lines.append(f"  {error['phase'] or 'unknown phase'}: {error['error']}")
    lines.append("KW1281 framing checked here is length/counter/title/data/ETX with byte complements; block checksum not present in this framing format, so no checksum result is calculated.")
    lines.append("Timestamps are summarized as durations only; they are not used to align streams or prove electrical timing.")
    return "\n".join(lines)


def _duration(events: list[dict[str, Any]]) -> float | None:
    values = [event["monotonic"] for event in events if event["monotonic"] is not None]
    return max(values) - min(values) if values else None


def _duration_text(value: float | None) -> str:
    return "duration unavailable" if value is None else f"{value:.3f}s span"


def _optional_number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None or value == "null":
        return None
    text = str(value)
    return text if text else None


def _scalar(field: str, value: Any) -> Any:
    if field in {"vendor_id", "product_id", "baudrate", "data_bits", "stop_bits"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field == "local_echo":
        if str(value).lower() in {"true", "1", "yes"}:
            return True
        if str(value).lower() in {"false", "0", "no"}:
            return False
    return value


if __name__ == "__main__":
    raise SystemExit(main())
