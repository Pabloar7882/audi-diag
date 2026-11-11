"""Allow-list for diagnostic operations permitted by the read-only product."""

from __future__ import annotations

from diagnostic_models import DiagnosticError


READ_ONLY_SERVICES = frozenset({0x01, 0x22})


def is_allowed_service(service_id: int) -> bool:
    return service_id in READ_ONLY_SERVICES


def require_allowed_service(service_id: int) -> None:
    if not is_allowed_service(service_id):
        raise DiagnosticError(
            "service_not_allowed",
            f"Diagnostic service 0x{service_id:02X} is not permitted in read-only mode.",
        )
