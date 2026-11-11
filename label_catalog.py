"""Bounded, read-only inventory of VCDS-style label metadata.

The scanner intentionally reads only ``.LBL`` files. Label rows describe
metadata and are not treated as byte-decoding formulas or physical policy.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 10_000
MAX_ROWS = 100_000
MAX_WARNINGS = 1_000
MAX_HEADER_LINES = 256
MAX_HEADER_LINE_CHARS = 4_096
MAX_REDIRECT_DEPTH = 16

AFN_INDEX_TARGETS = (
    "028-906-021-AFN",
    "038-906-012-AFN",
    "038-906-018-AFN",
)

_DECIMAL = re.compile(r"^[0-9]+$")
_PART_NUMBER = re.compile(
    r"(?i)(?<![A-Z0-9])(\d{3})[- ](\d{3})[- ](\d{3})(?:[- ]([A-Z0-9?xX*]+))?(?![A-Z0-9?*])"
)
_RANGE_LABEL = re.compile(r"(?i)\b(?:range|specification)\b[^:]{0,60}:\s*([^,\r\n]+)")
_INTERVAL = re.compile(
    r"(?i)(?<![A-Z0-9.])([+-]?\d+(?:\.\d+)?\s*(?:\.\.\.|\.\.|--?|\bto\b)\s*[+-]?\d+(?:\.\d+)?)(?![A-Z0-9.])"
)
_UNIT = re.compile(
    r"(?i)(mg\s*/\s*(?:stroke|h)|mbar|bar|rpm|°\s*c|deg\s*c|%|\bv\b|°\s*atdc)"
)
_ENGINE_CODE = re.compile(r"(?i)\b(AFN|AGR|ALH|1Z)\b")
_HEADER_HINT = re.compile(r"(?i)\b(?:component|ecu|p/?n|part\s+(?:no|number))\b")


@dataclass(frozen=True)
class LabelField:
    """One numeric group/field row, retaining its source spelling."""

    group_id: int
    field_id: int
    raw_fields: tuple[str, ...]
    raw_text: str
    source_line: int
    unit: str | None = None
    range_text: str | None = None


@dataclass(frozen=True)
class RedirectReference:
    """A redirect row and the scanner's safe resolution outcome."""

    source_line: int
    target_text: str
    ecu_reference: str | None
    state: str
    resolved_path: str | None = None
    chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class LabelDocument:
    relative_path: str
    size_bytes: int
    sha256: str | None
    encoding: str
    component: str | None
    ecu_references: tuple[str, ...]
    engine_codes: tuple[str, ...]
    fields: tuple[LabelField, ...]
    groups: tuple[int, ...]
    redirects: tuple[RedirectReference, ...]
    loaded: bool


@dataclass(frozen=True)
class AfnTarget:
    part_number: str
    exact_matches: tuple[str, ...]
    related_candidates: tuple[str, ...]


@dataclass(frozen=True)
class LabelInventory:
    root: Path
    files: tuple[LabelDocument, ...]
    warnings: tuple[str, ...]
    afn_targets: tuple[AfnTarget, ...]


@dataclass(frozen=True)
class _PendingRedirect:
    source_line: int
    target_text: str
    ecu_reference: str | None


class _WarningSink:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.suppressed = False

    def add(self, message: str) -> None:
        if len(self.items) < MAX_WARNINGS - 1:
            self.items.append(message)
        elif len(self.items) == MAX_WARNINGS - 1 and not self.suppressed:
            self.items.append("warning limit reached; additional warnings suppressed")
            self.suppressed = True


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _decode(raw: bytes) -> tuple[str, str, bool]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig", False
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252"), "cp1252", False
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace"), "cp1252-replace", True


def _metadata(raw_fields: tuple[str, ...]) -> tuple[str | None, str | None]:
    text = ",".join(raw_fields[2:])
    unit_match = _UNIT.search(text)
    unit = unit_match.group(0).strip() if unit_match else None

    range_text = None
    labeled_range = _RANGE_LABEL.search(text)
    if labeled_range:
        range_match = _INTERVAL.search(labeled_range.group(1))
        if range_match:
            range_text = range_match.group(1).strip()
    if range_text is None:
        for token in raw_fields[3:]:
            range_match = _INTERVAL.fullmatch(token.strip())
            if range_match:
                range_text = range_match.group(1).strip()
                break
    return unit, range_text


def _ecu_metadata(path: Path, header_lines: list[str]) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    component = None
    references: list[str] = []
    engine_codes: list[str] = []
    basename = path.name
    sources = [basename, *header_lines]
    for source in sources:
        for match in _PART_NUMBER.finditer(source):
            value = "-".join(part for part in match.groups() if part)
            if value.casefold() not in {item.casefold() for item in references}:
                references.append(value)
        for match in _ENGINE_CODE.finditer(source):
            value = match.group(1).upper()
            if value not in engine_codes:
                engine_codes.append(value)

        component_match = re.search(r"(?i)\bcomponent\s*:\s*(.*?)\s*$", source)
        if component is None and component_match:
            component = component_match.group(1).strip().rstrip(";")
    return component, tuple(references), tuple(engine_codes)


def _path_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _select_nested_redirect(
    redirects: tuple[_PendingRedirect, ...], ecu_reference: str | None
) -> _PendingRedirect | None:
    if len(redirects) == 1:
        return redirects[0]
    if ecu_reference:
        matches = [
            redirect
            for redirect in redirects
            if redirect.ecu_reference
            and redirect.ecu_reference.casefold() == ecu_reference.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _relative_name(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _candidate_targets(files: tuple[LabelDocument, ...]) -> tuple[AfnTarget, ...]:
    names = [(item.relative_path, Path(item.relative_path).name) for item in files]
    targets: list[AfnTarget] = []
    for target in AFN_INDEX_TARGETS:
        exact: list[str] = []
        related: list[str] = []
        prefix = target.rsplit("-", 1)[0] + "-"
        for relative, basename in names:
            stem = Path(basename).stem
            if stem.casefold() == target.casefold():
                exact.append(relative)
            elif stem.casefold().startswith(prefix.casefold()):
                related.append(relative)
        targets.append(AfnTarget(target, tuple(exact), tuple(related)))
    return tuple(targets)


def _safe_target(source: str, raw_target: str, root: Path) -> tuple[str, str | None, str | None]:
    """Return (state, normalized relative path, extension) without opening it."""

    target = raw_target.strip().strip('"').strip("'")
    if not target:
        return "rejected", None, None
    windows_path = PureWindowsPath(target)
    if (
        Path(target).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or target.startswith(("\\\\", "//"))
        or ":" in target
    ):
        return "rejected", None, None

    normalized = target.replace("\\", "/")
    components = normalized.split("/")
    if any(component in ("", ".", "..") for component in components):
        return "rejected", None, None

    suffix = Path(components[-1]).suffix.casefold()
    if suffix not in (".lbl", ".clb"):
        return "rejected", None, suffix

    candidate = root.joinpath(*components)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return "rejected", None, suffix
    if not _is_within(resolved, root):
        return "rejected", None, suffix
    if suffix == ".lbl" and resolved.suffix.casefold() != ".lbl":
        return "rejected", None, suffix
    relative = resolved.relative_to(root).as_posix()
    return "safe", relative, suffix


def _resolve_redirects(
    source_path: str,
    pending: tuple[_PendingRedirect, ...],
    documents: dict[str, LabelDocument],
    pending_by_key: dict[str, tuple[_PendingRedirect, ...]],
    root: Path,
    warnings: _WarningSink,
) -> tuple[RedirectReference, ...]:
    resolved_records: list[RedirectReference] = []
    for redirect in pending:
        state, target_path, extension = _safe_target(source_path, redirect.target_text, root)
        direct_path: str | None = target_path
        chain = [source_path]
        if state != "safe":
            resolved_records.append(
                RedirectReference(redirect.source_line, redirect.target_text, redirect.ecu_reference, "rejected")
            )
            continue
        if extension == ".clb":
            resolved_records.append(
                RedirectReference(
                    redirect.source_line,
                    redirect.target_text,
                    redirect.ecu_reference,
                    "protected",
                    direct_path,
                    tuple([source_path, direct_path]),
                )
            )
            continue

        current = target_path
        seen = {_path_key(source_path)}
        state = "resolved"
        hops = 0
        if current is None or _path_key(current) not in documents:
            state = "missing"
        else:
            while current is not None:
                current_key = _path_key(current)
                if current_key in seen:
                    state = "cycle"
                    break
                seen.add(current_key)
                # Keep canonical on-disk spelling for the evidence chain.
                current = documents[current_key].relative_path
                chain.append(current)
                nested_options = pending_by_key.get(current_key, ())
                if not nested_options:
                    break
                nested = _select_nested_redirect(nested_options, redirect.ecu_reference)
                if nested is None:
                    state = "ambiguous"
                    warnings.add(
                        f"{current}: ambiguous redirect branch while resolving "
                        f"{source_path}:{redirect.source_line}"
                    )
                    break
                if hops >= MAX_REDIRECT_DEPTH:
                    state = "depth_limit"
                    break
                next_ref = nested
                nested_state, nested_path, nested_ext = _safe_target(current, next_ref.target_text, root)
                if nested_state != "safe":
                    state = "rejected"
                    break
                if nested_ext == ".clb":
                    state = "protected"
                    chain.append(nested_path or next_ref.target_text)
                    break
                if nested_path is None or _path_key(nested_path) not in documents:
                    state = "missing"
                    break
                current = nested_path
                hops += 1

        resolved_records.append(
            RedirectReference(
                redirect.source_line,
                redirect.target_text,
                redirect.ecu_reference,
                state,
                direct_path,
                tuple(chain),
            )
        )
    return tuple(resolved_records)


def scan_labels(root: Path) -> LabelInventory:
    """Inventory confined ``.LBL`` metadata files beneath *root*.

    Results and warnings are deterministic. Individual and cumulative read
    budgets, parsed-row limits, and the warning limit are enforced before
    untrusted label content can consume unbounded resources.
    """

    warnings = _WarningSink()
    try:
        resolved_root = Path(root).resolve(strict=False)
    except (OSError, RuntimeError):
        resolved_root = Path(root).absolute()
    if not resolved_root.is_dir():
        warnings.add(f"label root is unavailable: {resolved_root}")
        return LabelInventory(resolved_root, (), tuple(warnings.items), _candidate_targets(()))

    paths: list[tuple[Path, Path]] = []
    file_limit_reached = False

    def report_walk_error(error: OSError) -> None:
        warnings.add(f"cannot inspect label directory: {error.__class__.__name__}")

    for current, directories, filenames in os.walk(
        resolved_root, followlinks=False, onerror=report_walk_error
    ):
        directories.sort(key=str.casefold)
        for directory in tuple(directories):
            directory_path = Path(current, directory)
            if directory_path.is_symlink():
                try:
                    target = directory_path.resolve(strict=False)
                except (OSError, RuntimeError):
                    target = None
                if target is None or not _is_within(target, resolved_root):
                    warnings.add(f"{_relative_name(directory_path, resolved_root)}: directory symlink escapes root")
                directories.remove(directory)
        for filename in sorted(filenames, key=lambda value: (value.casefold(), value)):
            path = Path(current, filename)
            if path.suffix.casefold() != ".lbl":
                continue
            if len(paths) >= MAX_FILES:
                warnings.add(f"file inventory limit of {MAX_FILES} reached; additional label files were not scanned")
                file_limit_reached = True
                break
            try:
                resolved = path.resolve(strict=False)
            except (OSError, RuntimeError):
                warnings.add(f"{_relative_name(path, resolved_root)}: cannot resolve label path")
                continue
            if not _is_within(resolved, resolved_root):
                warnings.add(f"{_relative_name(path, resolved_root)}: label path escapes root")
                continue
            if resolved.suffix.casefold() != ".lbl":
                warnings.add(f"{_relative_name(path, resolved_root)}: .LBL path resolves to a non-.LBL target; skipped")
                continue
            if not resolved.is_file():
                warnings.add(f"{_relative_name(path, resolved_root)}: label file is unavailable")
                continue
            paths.append((path, resolved))
        if file_limit_reached:
            break

    paths.sort(
        key=lambda pair: (
            _relative_name(pair[0], resolved_root).casefold(),
            _relative_name(pair[0], resolved_root),
        )
    )
    documents: list[LabelDocument] = []
    pending_by_path: dict[str, tuple[_PendingRedirect, ...]] = {}
    total_read = 0
    parsed_rows = 0
    row_limit_warned = False

    for path, safe_path in paths:
        relative = _relative_name(path, resolved_root)
        try:
            size = safe_path.stat().st_size
        except OSError as error:
            warnings.add(f"{relative}: cannot stat label file ({error.__class__.__name__})")
            documents.append(LabelDocument(relative, 0, None, "unreadable", None, (), (), (), (), (), False))
            pending_by_path[_path_key(relative)] = ()
            continue

        if size > MAX_FILE_BYTES:
            warnings.add(f"{relative}: file exceeds the {MAX_FILE_BYTES}-byte per-file size limit")
            documents.append(LabelDocument(relative, size, None, "oversize", None, (), (), (), (), (), False))
            pending_by_path[_path_key(relative)] = ()
            continue
        if total_read + size > MAX_TOTAL_BYTES:
            warnings.add(f"{relative}: skipped because the total label byte limit would be exceeded")
            documents.append(LabelDocument(relative, size, None, "not-read", None, (), (), (), (), (), False))
            pending_by_path[_path_key(relative)] = ()
            continue

        try:
            with safe_path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except OSError as error:
            warnings.add(f"{relative}: cannot read label file ({error.__class__.__name__})")
            documents.append(LabelDocument(relative, size, None, "unreadable", None, (), (), (), (), (), False))
            pending_by_path[_path_key(relative)] = ()
            continue

        if len(raw) > MAX_FILE_BYTES:
            warnings.add(f"{relative}: file exceeded the per-file size limit while being read")
            documents.append(LabelDocument(relative, size, None, "oversize", None, (), (), (), (), (), False))
            pending_by_path[_path_key(relative)] = ()
            continue
        if total_read + len(raw) > MAX_TOTAL_BYTES:
            warnings.add(f"{relative}: skipped because the total label byte limit would be exceeded")
            documents.append(LabelDocument(relative, size, None, "not-read", None, (), (), (), (), (), False))
            pending_by_path[_path_key(relative)] = ()
            continue
        total_read += len(raw)
        digest = hashlib.sha256(raw).hexdigest()
        text, encoding, had_replacement = _decode(raw)
        if had_replacement:
            warnings.add(f"{relative}: cp1252 decoding used replacement characters")

        header_lines: list[str] = []
        fields: list[LabelField] = []
        redirects: list[_PendingRedirect] = []
        seen_fields: set[tuple[int, int]] = set()
        header_limit_warned = False
        header_line_truncation_warned = False

        def remember_header(line: str, line_number: int) -> None:
            nonlocal header_limit_warned, header_line_truncation_warned
            if not (
                _HEADER_HINT.search(line)
                or _PART_NUMBER.search(line)
                or _ENGINE_CODE.search(line)
            ):
                return
            if len(header_lines) >= MAX_HEADER_LINES:
                if not header_limit_warned:
                    warnings.add(f"{relative}:{line_number}: header metadata limit of {MAX_HEADER_LINES} reached")
                    header_limit_warned = True
                return
            header_lines.append(line[:MAX_HEADER_LINE_CHARS])
            if len(line) > MAX_HEADER_LINE_CHARS and not header_line_truncation_warned:
                warnings.add(
                    f"{relative}:{line_number}: header metadata line truncated to {MAX_HEADER_LINE_CHARS} characters"
                )
                header_line_truncation_warned = True

        for line_number, source_line in enumerate(io.StringIO(text, newline=None), 1):
            line = source_line.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith((";", "#", "//")):
                remember_header(line, line_number)
                continue
            raw_fields = tuple(line.split(","))
            first = raw_fields[0].strip()
            if first.casefold() == "redirect":
                if parsed_rows >= MAX_ROWS:
                    if not row_limit_warned:
                        warnings.add(f"{relative}:{line_number}: parsed row limit of {MAX_ROWS} reached")
                        row_limit_warned = True
                    continue
                parsed_rows += 1
                target = raw_fields[1].strip() if len(raw_fields) > 1 else ""
                ecu_reference = raw_fields[2].strip() if len(raw_fields) > 2 else None
                redirects.append(_PendingRedirect(line_number, target, ecu_reference or None))
                if not target:
                    warnings.add(f"{relative}:{line_number}: redirect has no target")
                continue
            if len(raw_fields) < 2 or not _DECIMAL.fullmatch(first) or not _DECIMAL.fullmatch(raw_fields[1].strip()):
                remember_header(line, line_number)
                continue
            if parsed_rows >= MAX_ROWS:
                if not row_limit_warned:
                    warnings.add(f"{relative}:{line_number}: parsed row limit of {MAX_ROWS} reached")
                    row_limit_warned = True
                continue

            parsed_rows += 1
            group_id = int(first, 10)
            field_id = int(raw_fields[1].strip(), 10)
            key = (group_id, field_id)
            if key in seen_fields:
                warnings.add(f"{relative}:{line_number}: duplicate group/field {group_id}/{field_id}")
            seen_fields.add(key)
            unit, range_text = _metadata(raw_fields)
            fields.append(LabelField(group_id, field_id, raw_fields, line, line_number, unit, range_text))

        component, ecu_references, engine_codes = _ecu_metadata(path, header_lines)
        document = LabelDocument(
            relative,
            size,
            digest,
            encoding,
            component,
            ecu_references,
            engine_codes,
            tuple(fields),
            tuple(sorted({field.group_id for field in fields})),
            (),
            True,
        )
        documents.append(document)
        pending_by_path[_path_key(relative)] = tuple(redirects)

    by_path = {_path_key(document.relative_path): document for document in documents}
    resolved_documents: list[LabelDocument] = []
    for document in documents:
        redirects = _resolve_redirects(
            document.relative_path,
            pending_by_path.get(_path_key(document.relative_path), ()),
            by_path,
            pending_by_path,
            resolved_root,
            warnings,
        )
        resolved_documents.append(
            LabelDocument(
                document.relative_path,
                document.size_bytes,
                document.sha256,
                document.encoding,
                document.component,
                document.ecu_references,
                document.engine_codes,
                document.fields,
                document.groups,
                redirects,
                document.loaded,
            )
        )

    final_files = tuple(resolved_documents)
    for target in _candidate_targets(final_files):
        if len(target.exact_matches) > 1:
            warnings.add(f"duplicate exact AFN label candidates for {target.part_number}")
        if len(target.related_candidates) > 1:
            warnings.add(f"multiple related ECU candidates for {target.part_number}")
    return LabelInventory(resolved_root, final_files, tuple(warnings.items), _candidate_targets(final_files))
