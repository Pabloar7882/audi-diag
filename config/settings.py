"""
Configuration for Audi A4 B5 1.9 TDI Diagnostics Application.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import os


@dataclass
class SerialConfig:
    """Serial port configuration for FTDI KKL adapter"""
    port: str = "/dev/ttyUSB0"  # Linux default
    # port: str = "COM3"  # Windows default
    baudrate: int = 10400
    bytesize: int = 8
    parity: str = "N"  # None
    stopbits: int = 1
    timeout: float = 1.0
    write_timeout: float = 1.0
    # FTDI specific
    ftdi_vid: int = 0x0403
    ftdi_pids: List[int] = field(default_factory=lambda: [0x6001, 0x6010, 0x6011, 0x6014, 0x6015])


@dataclass
class KW1281Config:
    """KW1281 Protocol timing parameters (milliseconds)"""
    # 5-baud init timing
    five_baud_bit_ms: int = 200       # 5 baud = 200ms per bit
    wakeup_pulse_ms: int = 25         # Initial break pulse
    init_delay_ms: int = 50           # After address byte
    keyword_delay_ms: int = 5         # Between keyword bytes
    baud_switch_delay_ms: int = 300   # After init before 10400 baud
    
    # Communication timing
    block_timeout_ms: int = 500       # Wait for block
    inter_block_delay_ms: int = 5     # Between blocks
    max_retries: int = 3              # Block retries
    
    # ECU Addresses
    ecu_address: int = 0x11
    tester_address: int = 0xF1


@dataclass
class TelemetryConfig:
    """Telemetry polling configuration"""
    poll_interval_ms: int = 100       # 10Hz default
    blocks: List[int] = field(default_factory=lambda: [3, 7, 11])
    # Block 3: MAF/RPM
    # Block 7: Temperatures
    # Block 11: MAP/Boost
    
    # Value parsing (EDC15 AFN specific)
    rpm_scaling: str = "auto"         # auto, div4, direct
    maf_scaling: float = 0.01         # mg/stroke per LSB (100 = 1 mg/stroke)
    map_scaling: float = 1.0          # mbar per LSB
    temp_offset: int = -40            # Temperature offset for signed byte


@dataclass
class DatabaseConfig:
    """MySQL Database configuration"""
    host: str = "localhost"
    port: int = 3306
    user: str = "audi_diag"
    password: str = "secure_password"
    database: str = "audi_diag"
    charset: str = "utf8mb4"
    autocommit: bool = False
    
    # Connection pool
    pool_min: int = 2
    pool_max: int = 5
    
    # Timeouts
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30
    
    # Buffering
    flush_interval_sec: float = 1.0
    max_buffer_size: int = 10000


@dataclass
class UIConfig:
    """UI/Dashboard configuration"""
    theme: str = "dark"               # dark, light, auto
    gauge_style: str = "modern"       # modern, classic, race
    update_interval_ms: int = 50      # GUI update rate (20Hz)
    window_width: int = 1400
    window_height: int = 900
    fullscreen: bool = False
    show_debug_panel: bool = False
    language: str = "en"


@dataclass
class LoggingConfig:
    """Application logging configuration"""
    level: str = "INFO"               # DEBUG, INFO, WARNING, ERROR
    file: str = "audi_diag.log"
    max_size_mb: int = 10
    backup_count: int = 5
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"


@dataclass
class AppConfig:
    """Main application configuration"""
    serial: SerialConfig = field(default_factory=SerialConfig)
    kw1281: KW1281Config = field(default_factory=KW1281Config)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Vehicle info
    vin: str = ""
    engine_code: str = "AFN"
    ecu_type: str = "EDC15"
    
    @classmethod
    def from_env(cls) -> "AppConfig":
        """Create config from environment variables."""
        return cls(
            serial=SerialConfig(
                port=os.getenv("AUDI_DIAG_PORT", "/dev/ttyUSB0"),
                baudrate=int(os.getenv("AUDI_DIAG_BAUD", "10400")),
            ),
            database=DatabaseConfig(
                host=os.getenv("AUDI_DB_HOST", "localhost"),
                port=int(os.getenv("AUDI_DB_PORT", "3306")),
                user=os.getenv("AUDI_DB_USER", "audi_diag"),
                password=os.getenv("AUDI_DB_PASS", "secure_password"),
                database=os.getenv("AUDI_DB_NAME", "audi_diag"),
            ),
            ui=UIConfig(
                theme=os.getenv("AUDI_UI_THEME", "dark"),
                fullscreen=os.getenv("AUDI_FULLSCREEN", "false").lower() == "true",
            ),
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        import dataclasses
        return dataclasses.asdict(self)


# Default configuration instance
DEFAULT_CONFIG = AppConfig()


# EDC15 AFN Measuring Block Definitions
MEASURING_BLOCKS = {
    3: {
        "name": "MAF / RPM",
        "description": "Mass Air Flow, RPM, Engine Load, Throttle, Injection Quantity",
        "fields": [
            ("rpm", "RPM", "RPM", 0, 6000),
            ("maf_actual", "MAF Actual", "mg/stroke", 0, 1200),
            ("maf_specified", "MAF Specified", "mg/stroke", 0, 1200),
            ("engine_load", "Engine Load", "%", 0, 100),
            ("throttle_pos", "Throttle Position", "%", 0, 100),
            ("iq_actual", "IQ Actual", "mg/stroke", 0, 100),
            ("iq_specified", "IQ Specified", "mg/stroke", 0, 100),
        ],
        "raw_size": 12,
        "parse_func": "parse_block_003",
    },
    7: {
        "name": "Temperatures",
        "description": "Coolant, Intake Air, Fuel, Oil, Ambient, EGR temperatures",
        "fields": [
            ("coolant", "Coolant Temp", "°C", -20, 130),
            ("intake_air", "Intake Air Temp", "°C", -20, 80),
            ("fuel", "Fuel Temp", "°C", -20, 80),
            ("oil", "Oil Temp", "°C", -20, 150),
            ("ambient", "Ambient Temp", "°C", -30, 60),
            ("egr", "EGR Temp", "°C", -20, 800),
        ],
        "raw_size": 6,
        "parse_func": "parse_block_007",
    },
    11: {
        "name": "MAP / Boost",
        "description": "Manifold Absolute Pressure, Boost, Wastegate, N75, EGR duty cycles",
        "fields": [
            ("map_actual", "MAP Actual", "mbar", 0, 2500),
            ("map_specified", "MAP Specified", "mbar", 0, 2500),
            ("boost", "Boost Pressure", "mbar", -1000, 1500),
            ("wastegate_duty", "Wastegate Duty", "%", 0, 100),
            ("n75_duty", "N75 Valve Duty", "%", 0, 100),
            ("egr_duty", "EGR Duty", "%", 0, 100),
        ],
        "raw_size": 6,
        "parse_func": "parse_block_011",
    },
}


# EDC15 AFN Specific Constants
EDC15_AFN_CONSTANTS = {
    "rpm_scaling": 0.25,        # RPM = raw * 0.25 (or raw/4)
    "rpm_max_raw": 16000,       # Max raw value before scaling
    "maf_factor": 0.01,         # mg/stroke = raw * 0.01
    "iq_factor": 0.01,          # mg/stroke = raw * 0.01
    "map_factor": 1.0,          # mbar = raw * 1.0
    "temp_offset": -40,         # °C = raw - 40 (for 0-255 range)
    "duty_factor": 100/255,     # % = raw * 100/255
}


# Known ECU Part Numbers for AFN Engine
KNOWN_ECU_PARTS = {
    "028906018": "Bosch EDC15 AFN 90hp",
    "028906019": "Bosch EDC15 AFN 90hp (variant)",
    "028906020": "Bosch EDC15 AHU/1Z 90hp",
    "028906021": "Bosch EDC15 AHU/1Z 90hp (variant)",
    "028906022": "Bosch EDC15 AFN 110hp",
}


def get_config() -> AppConfig:
    """Get application configuration (from env or defaults)."""
    return AppConfig.from_env()


if __name__ == "__main__":
    # Print config for verification
    cfg = get_config()
    import json
    print(json.dumps(cfg.to_dict(), indent=2, default=str))