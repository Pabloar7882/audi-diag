"""
Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics Package
"""

__version__ = "1.0.0"
__author__ = "AudiDiag"
__description__ = "KW1281 diagnostics for 1999 Audi A4 B5 1.9 TDI EDC15"

from kw1281_handler import (
    KW1281Handler,
    KW1281Error,
    KW1281TimeoutError,
    KW1281ChecksumError,
    KW1281ProtocolError,
    KW1281ConnectionError,
    ECUIdentification,
    KW1281BlockTitle,
    KW1281Address,
    KW1281Block,
    MeasuringValue,
    FaultCode,
    find_kkl_adapters,
    list_serial_ports,
)

# Common VAG/EDC15 ECU addresses for kw1281test compatibility
ENGINE_ADDRESS = 0x01
CLUSTER_ADDRESS = 0x17
CCM_ADDRESS = 0x46
RADIO_ADDRESS = 0x56
ABS_ADDRESS = 0x03

from telemetry_worker import (
    TelemetryWorker,
    TelemetryThread,
    AsyncTelemetryWorker,
    TelemetrySnapshot,
    MeasuringBlock003,
    MeasuringBlock007,
    MeasuringBlock011,
    WorkerState,
)

from db.database_logger import (
    DatabaseLogger,
    SyncDatabaseLogger,
    DBConfig,
    LogStats,
    create_database_logger,
)

__all__ = [
    # KW1281 Protocol
    "KW1281Handler",
    "KW1281Error",
    "KW1281TimeoutError",
    "KW1281ChecksumError",
    "KW1281ProtocolError",
    "KW1281ConnectionError",
    "ECUIdentification",
    "KW1281BlockTitle",
    "KW1281Address",
    "KW1281Block",
    "MeasuringValue",
    "FaultCode",
    "find_kkl_adapters",
    "list_serial_ports",
    
    # Common VAG addresses (kw1281test compatibility)
    "ENGINE_ADDRESS",
    "CLUSTER_ADDRESS",
    "CCM_ADDRESS",
    "RADIO_ADDRESS",
    "ABS_ADDRESS",
    
    # Telemetry
    "TelemetryWorker",
    "TelemetryThread",
    "AsyncTelemetryWorker",
    "TelemetrySnapshot",
    "MeasuringBlock003",
    "MeasuringBlock007",
    "MeasuringBlock011",
    "WorkerState",
    
    # Database
    "DatabaseLogger",
    "SyncDatabaseLogger",
    "DBConfig",
    "LogStats",
    "create_database_logger",
    
    # Version
    "__version__",
    "__author__",
    "__description__",
]