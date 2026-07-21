"""
Main entry point for Audi A4 B5 1.9 TDI Diagnostics Application.
"""

import sys
import asyncio
import logging
import signal
import yaml
from pathlib import Path
from typing import Optional

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from main_window import MainDashboard, main as gui_main
from config.config_loader import load_config, Config
from db.database_logger import DatabaseLogger, DBConfig


def setup_logging(config: Config) -> None:
    """Configure application logging."""
    log_config = config.logging
    
    # Create logs directory
    log_file = Path(log_config.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_config.level.upper(), logging.INFO),
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout) if log_config.console else logging.NullHandler(),
        ]
    )
    
    # Set specific logger levels
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('serial').setLevel(logging.WARNING)
    logging.getLogger('aiomysql').setLevel(logging.WARNING)


def load_application_config(config_path: Optional[str] = None) -> Config:
    """Load application configuration from YAML file."""
    if config_path is None:
        # Try default locations
        default_paths = [
            Path(__file__).parent / "config" / "config.yaml",
            Path.cwd() / "config" / "config.yaml",
            Path.home() / ".config" / "audi_diag" / "config.yaml",
        ]
        for path in default_paths:
            if path.exists():
                config_path = str(path)
                break
    
    if config_path and Path(config_path).exists():
        return load_config(config_path)
    else:
        # Return defaults
        return Config()


async def run_headless(config: Config) -> None:
    """Run in headless mode (no GUI) for logging only."""
    from telemetry_worker import AsyncTelemetryWorker
    from kw1281_handler import find_kkl_adapters
    
    # Auto-detect adapter if needed
    port = config.serial.port
    if config.serial.auto_detect:
        adapters = find_kkl_adapters()
        if adapters:
            port = adapters[0]['device']
            print(f"Auto-detected KKL adapter: {port}")
        else:
            print("No KKL adapter found, using configured port")
    
    # Setup database logger
    db_config = DBConfig(
        host=config.database.host,
        port=config.database.port,
        database=config.database.database,
        user=config.database.user,
        password=config.database.password,
        pool_size=config.database.pool_size,
    )
    
    db_logger = DatabaseLogger(
        db_config,
        bulk_interval=config.database.bulk_insert_interval_s,
        batch_size=config.database.bulk_batch_size,
        auto_create_schema=config.database.auto_create_schema,
    )
    
    await db_logger.initialize()
    await db_logger.start()
    
    # Start session
    session_id = await db_logger.start_session(
        vin=None,  # Could be read from ECU or config
        ecu_part=None,
        ecu_sw=None,
        port=port,
    )
    print(f"Started session #{session_id}")
    
    # Telemetry callback
    async def on_telemetry(snapshot):
        db_logger.log_telemetry(snapshot)
    
    async def on_error(error):
        db_logger.log_event(
            event_type="ERROR",
            severity="ERROR",
            error_message=str(error),
        )
        print(f"Error: {error}")
    
    async def on_ecu_id(ecu_id):
        db_logger.log_ecu_identification(ecu_id)
        print(f"ECU: {ecu_id.part_number} SW:{ecu_id.software_version} Engine:{ecu_id.engine_code}")
    
    # Create and start worker
    worker = AsyncTelemetryWorker(
        port=port,
        baudrate=config.serial.baudrate,
        poll_interval_ms=config.telemetry.poll_interval_ms,
        blocks=config.telemetry.blocks,
        on_telemetry=on_telemetry,
        on_error=on_error,
        on_ecu_id=on_ecu_id,
    )
    
    # Handle signals for graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        print("\nShutdown signal received...")
        shutdown_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    # Run worker until shutdown
    worker_task = asyncio.create_task(worker.start())
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    
    done, pending = await asyncio.wait(
        [worker_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    
    # Cancel remaining tasks
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    # Cleanup
    await worker.stop()
    await db_logger.end_session()
    await db_logger.stop()
    
    print("Shutdown complete")


def main() -> int:
    """Main application entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '-c', '--config',
        help='Path to config.yaml',
        default=None,
    )
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Run without GUI (headless logging mode)',
    )
    parser.add_argument(
        '--port',
        help='Serial port override (e.g., /dev/ttyUSB0 or COM3)',
        default=None,
    )
    parser.add_argument(
        '--baud',
        type=int,
        help='Baud rate override',
        default=None,
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level',
        default=None,
    )
    parser.add_argument(
        '--create-schema',
        action='store_true',
        help='Create database schema and exit',
    )
    parser.add_argument(
        '--list-ports',
        action='store_true',
        help='List available KKL adapters and exit',
    )
    parser.add_argument(
        '--version',
        action='version',
        version='Audi A4 B5 Diagnostics v1.0.0',
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_application_config(args.config)
    
    # Apply CLI overrides
    if args.port:
        config.serial.port = args.port
    if args.baud:
        config.serial.baudrate = args.baud
    if args.log_level:
        config.logging.level = args.log_level
    
    # Setup logging
    setup_logging(config)
    logger = logging.getLogger(__name__)
    
    # Handle list-ports
    if args.list_ports:
        from kw1281_handler import find_kkl_adapters
        adapters = find_kkl_adapters()
        if adapters:
            print("Found KKL adapters:")
            for a in adapters:
                print(f"  {a['device']} - {a['description']} (VID:PID={a['vid']:04X}:{a['pid']:04X})")
        else:
            print("No FTDI KKL adapters found")
        return 0
    
    # Handle create-schema
    if args.create_schema:
        async def create_schema():
            db_config = DBConfig(
                host=config.database.host,
                port=config.database.port,
                database=config.database.database,
                user=config.database.user,
                password=config.database.password,
            )
            logger = DatabaseLogger(db_config, auto_create_schema=True)
            await logger.initialize()
            print("Database schema created successfully")
        
        asyncio.run(create_schema())
        return 0
    
    # Run application
    try:
        if args.headless:
            # Headless mode
            asyncio.run(run_headless(config))
        else:
            # GUI mode
            gui_main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())