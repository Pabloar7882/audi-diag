"""
Configuration loader for Audi A4 B5 Diagnostics.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, get_type_hints
import yaml

from db.database_logger import DBConfig, LogStats

T = TypeVar('T', bound='ConfigBase')


class ConfigBase:
    """Base class for config sections with env var override support."""
    
    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """Create instance from dictionary, handling nested configs."""
        field_types = get_type_hints(cls)
        kwargs = {}
        
        for f in fields(cls):
            if f.name in data:
                value = data[f.name]
                field_type = field_types.get(f.name)
                
                # Handle nested config objects
                if field_type and hasattr(field_type, 'from_dict') and isinstance(value, dict):
                    kwargs[f.name] = field_type.from_dict(value)
                # Handle lists of config objects
                elif field_type and hasattr(field_type, '__origin__') and field_type.__origin__ is list:
                    item_type = field_type.__args__[0] if field_type.__args__ else None
                    if item_type and hasattr(item_type, 'from_dict') and isinstance(value, list):
                        kwargs[f.name] = [item_type.from_dict(v) if isinstance(v, dict) else v for v in value]
                    else:
                        kwargs[f.name] = value
                else:
                    kwargs[f.name] = value
            elif f.default is not field.MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not field.MISSING:
                kwargs[f.name] = f.default_factory()
        
        return cls(**kwargs)
    
    def apply_env_overrides(self, prefix: str = "") -> None:
        """Apply environment variable overrides (e.g., AUDI_DB_HOST=localhost)."""
        for f in fields(self):
            env_name = f"{prefix}{f.name.upper()}"
            if env_name in os.environ:
                value = os.environ[env_name]
                field_type = get_type_hints(self.__class__).get(f.name, str)
                
                # Type conversion
                if field_type == bool:
                    value = value.lower() in ('true', '1', 'yes', 'on')
                elif field_type == int:
                    value = int(value)
                elif field_type == float:
                    value = float(value)
                elif field_type == list:
                    value = [v.strip() for v in value.split(',')]
                
                setattr(self, f.name, value)


@dataclass
class SerialConfig(ConfigBase):
    port: str = "/dev/ttyUSB0"
    baudrate: int = 10400
    timeout: float = 1.0
    write_timeout: float = 1.0
    auto_detect: bool = True


@dataclass
class KW1281TimingConfig(ConfigBase):
    five_baud_bit_ms: int = 200
    wakeup_pulse_ms: int = 25
    init_delay_ms: int = 50
    keyword_delay_ms: int = 5
    baud_switch_delay_ms: int = 300
    block_timeout_ms: int = 500
    inter_block_delay_ms: int = 5


@dataclass
class TelemetryConfig(ConfigBase):
    poll_interval_ms: int = 100
    blocks: List[int] = field(default_factory=lambda: [3, 7, 11])
    max_retries: int = 3
    retry_delay_ms: int = 10


@dataclass
class ReconnectionConfig(ConfigBase):
    initial_delay_s: float = 1.0
    max_delay_s: float = 30.0
    backoff_multiplier: float = 1.5
    max_attempts: int = 0


@dataclass
class DatabaseConfig(ConfigBase):
    host: str = "localhost"
    port: int = 3306
    database: str = "audi_diag"
    user: str = "audi_diag"
    password: str = "change_me"
    pool_size: int = 5
    bulk_insert_interval_s: float = 1.0
    bulk_batch_size: int = 100
    auto_create_schema: bool = True


@dataclass
class LoggingConfig(ConfigBase):
    level: str = "INFO"
    file: str = "logs/audi_diag.log"
    max_size_mb: int = 10
    backup_count: int = 5
    console: bool = True


@dataclass
class UIConfig(ConfigBase):
    theme: str = "dark"
    gauge_style: str = "modern"
    animation_enabled: bool = True
    fullscreen_on_start: bool = False
    window_width: int = 1200
    window_height: int = 800
    show_debug_panel: bool = False


@dataclass
class ECUConfig(ConfigBase):
    engine_code: str = "AFN"
    ecu_type: str = "EDC15"


@dataclass
class ThresholdsConfig(ConfigBase):
    rpm: Dict[str, int] = field(default_factory=lambda: {"warning": 4500, "critical": 5200})
    map_actual: Dict[str, int] = field(default_factory=lambda: {"warning": 2200, "critical": 2400})
    maf_actual: Dict[str, float] = field(default_factory=lambda: {"warning": 1000, "critical": 1150})
    boost: Dict[str, int] = field(default_factory=lambda: {"warning": 1200, "critical": 1400})
    coolant_temp: Dict[str, int] = field(default_factory=lambda: {"warning": 100, "critical": 115})
    intake_temp: Dict[str, int] = field(default_factory=lambda: {"warning": 60, "critical": 70})
    wastegate_duty: Dict[str, int] = field(default_factory=lambda: {"warning": 85, "critical": 95})
    engine_load: Dict[str, int] = field(default_factory=lambda: {"warning": 90, "critical": 100})


@dataclass
class Config(ConfigBase):
    """Main configuration container."""
    serial: SerialConfig = field(default_factory=SerialConfig)
    kw1281_timing: KW1281TimingConfig = field(default_factory=KW1281TimingConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    reconnection: ReconnectionConfig = field(default_factory=ReconnectionConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    ecu: ECUConfig = field(default_factory=ECUConfig)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)


def load_config(config_path: str) -> Config:
    """
    Load configuration from YAML file with environment variable overrides.
    
    Args:
        config_path: Path to config.yaml file
        
    Returns:
        Config object with all settings loaded
    """
    path = Path(config_path)
    
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    
    config = Config.from_dict(data)
    
    # Apply environment variable overrides with AUDI_ prefix
    config.apply_env_overrides("AUDI_")
    
    # Also apply section-specific prefixes
    config.serial.apply_env_overrides("AUDI_SERIAL_")
    config.kw1281_timing.apply_env_overrides("AUDI_KW1281_")
    config.telemetry.apply_env_overrides("AUDI_TELEMETRY_")
    config.reconnection.apply_env_overrides("AUDI_RECONNECT_")
    config.database.apply_env_overrides("AUDI_DB_")
    config.logging.apply_env_overrides("AUDI_LOG_")
    config.ui.apply_env_overrides("AUDI_UI_")
    config.ecu.apply_env_overrides("AUDI_ECU_")
    config.thresholds.apply_env_overrides("AUDI_THRESH_")
    
    return config


def create_default_config() -> Config:
    """Create a default configuration."""
    return Config()


def save_config(config: Config, config_path: str) -> None:
    """Save configuration to YAML file."""
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    def config_to_dict(obj: Any) -> Any:
        if isinstance(obj, ConfigBase):
            result = {}
            for f in fields(obj):
                value = getattr(obj, f.name)
                if isinstance(value, ConfigBase):
                    result[f.name] = config_to_dict(value)
                elif isinstance(value, list):
                    result[f.name] = [config_to_dict(v) if isinstance(v, ConfigBase) else v for v in value]
                else:
                    result[f.name] = value
            return result
        return obj
    
    data = config_to_dict(config)
    
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


# Convenience function for getting DB config
def get_db_config(config: Config) -> DBConfig:
    """Extract DBConfig from main Config."""
    return DBConfig(
        host=config.database.host,
        port=config.database.port,
        database=config.database.database,
        user=config.database.user,
        password=config.database.password,
        pool_size=config.database.pool_size,
    )