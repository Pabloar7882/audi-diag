"""
Async MySQL Database Logger for Audi A4 B5 Telemetry.
Buffered bulk inserts with automatic reconnection and schema management.
"""

from __future__ import annotations
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import deque
import threading
from queue import Queue, Empty

try:
    import aiomysql
    AIOMYSQL_AVAILABLE = True
except ImportError:
    AIOMYSQL_AVAILABLE = False
    aiomysql = None

from telemetry_worker import (
    TelemetrySnapshot,
    MeasuringBlock003,
    MeasuringBlock007,
    MeasuringBlock011,
    ECUIdentification,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DBConfig:
    """Database connection configuration."""
    host: str = "localhost"
    port: int = 3306
    database: str = "audi_diag"
    user: str = "audi_diag"
    password: str = "change_me"
    pool_size: int = 5
    autocommit: bool = False
    charset: str = "utf8mb4"


@dataclass(slots=True)
class LogStats:
    """Database logging statistics."""
    total_inserts: int = 0
    failed_inserts: int = 0
    bulk_inserts: int = 0
    total_rows: int = 0
    last_insert_time: float = 0
    avg_batch_size: float = 0.0
    connection_errors: int = 0
    last_error: Optional[str] = None


class DatabaseLogger:
    """
    High-performance async MySQL logger with buffered bulk inserts.
    
    Features:
    - Connection pooling for concurrent access
    - Buffered writes with configurable flush interval
    - Automatic reconnection on connection loss
    - Bulk INSERT for high-frequency telemetry
    - Session management (start/end)
    - Schema initialization
    """
    
    def __init__(
        self,
        config: DBConfig,
        bulk_interval: float = 1.0,
        batch_size: int = 100,
        auto_create_schema: bool = True,
    ):
        if not AIOMYSQL_AVAILABLE:
            raise RuntimeError("aiomysql not installed. Run: pip install aiomysql")
        
        self.config = config
        self.bulk_interval = bulk_interval
        self.batch_size = batch_size
        self.auto_create_schema = auto_create_schema
        
        self._pool: Optional[aiomysql.Pool] = None
        self._running = False
        self._flush_task: Optional[asyncio.Task] = None
        
        # Buffers for each table
        self._buffers: Dict[str, deque] = {
            'sessions': deque(),
            'mb003': deque(),
            'mb007': deque(),
            'mb011': deque(),
            'events': deque(),
            'ecu_id': deque(),
        }
        
        # Thread-safe queue for cross-thread logging
        self._queue: Queue = Queue()
        self._queue_processor_task: Optional[asyncio.Task] = None
        
        self._stats = LogStats()
        self._current_session_id: Optional[int] = None
        self._session_start_time: Optional[float] = None
        self._lock = asyncio.Lock()
        
        # Schema SQL (same as schema.sql)
        self._schema_sql = self._get_schema_sql()
    
    async def initialize(self) -> None:
        """Initialize connection pool and schema."""
        self._pool = await aiomysql.create_pool(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            db=self.config.database,
            minsize=1,
            maxsize=self.config.pool_size,
            autocommit=self.config.autocommit,
            charset=self.config.charset,
            echo=False,
        )
        
        if self.auto_create_schema:
            await self._create_schema()
        
        logger.info(f"Database pool created: {self.config.host}:{self.config.port}/{self.config.database}")
    
    async def _create_schema(self) -> None:
        """Create database schema from embedded SQL."""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                statements = [s.strip() for s in self._schema_sql.split(';') if s.strip()]
                for stmt in statements:
                    try:
                        await cursor.execute(stmt)
                    except Exception as e:
                        logger.warning(f"Schema statement failed (may already exist): {e}")
                await conn.commit()
        logger.info("Database schema initialized")
    
    def _get_schema_sql(self) -> str:
        """Return the schema SQL."""
        return """
        CREATE DATABASE IF NOT EXISTS `audi_diag` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
        USE `audi_diag`;
        
        CREATE TABLE IF NOT EXISTS `sessions` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `vin` VARCHAR(17) DEFAULT NULL,
            `ecu_part_number` VARCHAR(20) DEFAULT NULL,
            `ecu_software_version` VARCHAR(20) DEFAULT NULL,
            `engine_code` VARCHAR(10) DEFAULT 'AFN',
            `ecu_type` VARCHAR(20) DEFAULT 'EDC15',
            `adapter_type` VARCHAR(30) DEFAULT 'FTDI_KKL',
            `port` VARCHAR(50) DEFAULT '/dev/ttyUSB0',
            `started_at` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            `ended_at` TIMESTAMP(3) DEFAULT NULL,
            `total_frames` BIGINT UNSIGNED NOT NULL DEFAULT 0,
            `dropped_frames` BIGINT UNSIGNED NOT NULL DEFAULT 0,
            `checksum_errors` BIGINT UNSIGNED NOT NULL DEFAULT 0,
            `notes` TEXT DEFAULT NULL,
            PRIMARY KEY (`id`),
            INDEX `idx_started_at` (`started_at`),
            INDEX `idx_vin` (`vin`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        
        CREATE TABLE IF NOT EXISTS `measuring_block_003` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `session_id` BIGINT UNSIGNED NOT NULL,
            `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            `relative_ms` INT UNSIGNED NOT NULL,
            `rpm` SMALLINT UNSIGNED NOT NULL,
            `maf_actual_mg_stroke` DECIMAL(8,2) NOT NULL,
            `maf_specified_mg_stroke` DECIMAL(8,2) NOT NULL,
            `engine_load_pct` TINYINT UNSIGNED NOT NULL,
            `throttle_position_pct` TINYINT UNSIGNED NOT NULL,
            `iq_actual_mg_stroke` DECIMAL(6,2) DEFAULT NULL,
            `iq_specified_mg_stroke` DECIMAL(6,2) DEFAULT NULL,
            `raw_block_data` VARBINARY(64) NOT NULL,
            `checksum_valid` BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (`id`),
            CONSTRAINT `fk_mb003_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
            INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
            INDEX `idx_relative_ms` (`session_id`, `relative_ms`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        
        CREATE TABLE IF NOT EXISTS `measuring_block_007` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `session_id` BIGINT UNSIGNED NOT NULL,
            `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            `relative_ms` INT UNSIGNED NOT NULL,
            `coolant_temp_c` SMALLINT NOT NULL,
            `intake_air_temp_c` SMALLINT NOT NULL,
            `fuel_temp_c` SMALLINT DEFAULT NULL,
            `oil_temp_c` SMALLINT DEFAULT NULL,
            `ambient_temp_c` SMALLINT DEFAULT NULL,
            `egr_temp_c` SMALLINT DEFAULT NULL,
            `raw_block_data` VARBINARY(64) NOT NULL,
            `checksum_valid` BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (`id`),
            CONSTRAINT `fk_mb007_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
            INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
            INDEX `idx_relative_ms` (`session_id`, `relative_ms`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        
        CREATE TABLE IF NOT EXISTS `measuring_block_011` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `session_id` BIGINT UNSIGNED NOT NULL,
            `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            `relative_ms` INT UNSIGNED NOT NULL,
            `map_actual_mbar` SMALLINT UNSIGNED NOT NULL,
            `map_specified_mbar` SMALLINT UNSIGNED NOT NULL,
            `boost_pressure_mbar` SMALLINT NOT NULL,
            `wastegate_duty_pct` TINYINT UNSIGNED NOT NULL,
            `n75_valve_duty_pct` TINYINT UNSIGNED DEFAULT NULL,
            `egr_duty_pct` TINYINT UNSIGNED DEFAULT NULL,
            `raw_block_data` VARBINARY(64) NOT NULL,
            `checksum_valid` BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (`id`),
            CONSTRAINT `fk_mb011_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
            INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
            INDEX `idx_relative_ms` (`session_id`, `relative_ms`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        
        CREATE TABLE IF NOT EXISTS `diagnostic_events` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `session_id` BIGINT UNSIGNED NOT NULL,
            `timestamp` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            `event_type` ENUM('CONNECT', 'DISCONNECT', 'TIMEOUT', 'CHECKSUM_ERROR', 'BLOCK_ERROR', 'BAUD_SWITCH', 'INIT_FAILED', 'RECONNECT', 'BUFFER_OVERFLOW') NOT NULL,
            `severity` ENUM('INFO', 'WARNING', 'ERROR', 'CRITICAL') NOT NULL DEFAULT 'INFO',
            `block_number` SMALLINT UNSIGNED DEFAULT NULL,
            `expected_bytes` SMALLINT UNSIGNED DEFAULT NULL,
            `received_bytes` SMALLINT UNSIGNED DEFAULT NULL,
            `error_message` TEXT DEFAULT NULL,
            `raw_data` VARBINARY(256) DEFAULT NULL,
            PRIMARY KEY (`id`),
            CONSTRAINT `fk_events_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
            INDEX `idx_session_timestamp` (`session_id`, `timestamp`),
            INDEX `idx_event_type` (`session_id`, `event_type`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        
        CREATE TABLE IF NOT EXISTS `ecu_identification` (
            `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            `session_id` BIGINT UNSIGNED NOT NULL,
            `part_number` VARCHAR(20) DEFAULT NULL,
            `software_version` VARCHAR(20) DEFAULT NULL,
            `engine_code` VARCHAR(10) DEFAULT NULL,
            `vehicle_identification` VARCHAR(40) DEFAULT NULL,
            `date_of_manufacture` DATE DEFAULT NULL,
            `coding` VARCHAR(20) DEFAULT NULL,
            `raw_identification_block` VARBINARY(512) NOT NULL,
            `parsed_at` TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
            PRIMARY KEY (`id`),
            CONSTRAINT `fk_ecu_id_session` FOREIGN KEY (`session_id`) REFERENCES `sessions` (`id`) ON DELETE CASCADE,
            UNIQUE KEY `uk_session` (`session_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    
    async def start_session(
        self,
        vin: Optional[str] = None,
        ecu_part: Optional[str] = None,
        ecu_sw: Optional[str] = None,
        port: str = "/dev/ttyUSB0",
    ) -> int:
        """Start a new logging session and return session ID."""
        async with self._lock:
            if self._current_session_id is not None:
                await self.end_session()
            
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """INSERT INTO sessions 
                           (vin, ecu_part_number, ecu_software_version, port, started_at)
                           VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP(3))""",
                        (vin, ecu_part, ecu_sw, port)
                    )
                    await conn.commit()
                    self._current_session_id = cursor.lastrowid
            
            self._session_start_time = time.monotonic()
            logger.info(f"Started database session: {self._current_session_id}")
            return self._current_session_id
    
    async def end_session(self) -> None:
        """End current session and update statistics."""
        async with self._lock:
            if self._current_session_id is None:
                return
            
            session_id = self._current_session_id
            self._current_session_id = None
            self._session_start_time = None
            
            # Flush any remaining buffers
            await self._flush_all_buffers()
            
            # Update session stats
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """UPDATE sessions SET 
                           ended_at = CURRENT_TIMESTAMP(3),
                           total_frames = (
                               SELECT COUNT(*) FROM measuring_block_003 WHERE session_id = %s
                           ) + (
                               SELECT COUNT(*) FROM measuring_block_007 WHERE session_id = %s
                           ) + (
                               SELECT COUNT(*) FROM measuring_block_011 WHERE session_id = %s
                           ),
                           dropped_frames = (
                               SELECT COUNT(*) FROM diagnostic_events 
                               WHERE session_id = %s AND event_type IN ('TIMEOUT', 'BLOCK_ERROR')
                           ),
                           checksum_errors = (
                               SELECT COUNT(*) FROM diagnostic_events 
                               WHERE session_id = %s AND event_type = 'CHECKSUM_ERROR'
                           )
                           WHERE id = %s""",
                        (session_id, session_id, session_id, session_id, session_id, session_id)
                    )
                    await conn.commit()
            
            logger.info(f"Ended database session: {session_id}")
    
    def log_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        """Queue telemetry snapshot for async logging (thread-safe)."""
        if self._current_session_id is None:
            return
        
        session_time = time.monotonic() - (self._session_start_time or time.monotonic())
        relative_ms = int(session_time * 1000)
        timestamp = datetime.fromtimestamp(snapshot.timestamp)
        
        # Prepare records for each block
        if snapshot.mb003:
            mb = snapshot.mb003
            self._queue.put(('mb003', {
                'session_id': self._current_session_id,
                'timestamp': timestamp,
                'relative_ms': relative_ms,
                'rpm': mb.rpm,
                'maf_actual_mg_stroke': mb.maf_actual_mg_stroke,
                'maf_specified_mg_stroke': mb.maf_specified_mg_stroke,
                'engine_load_pct': mb.engine_load_pct,
                'throttle_position_pct': mb.throttle_position_pct,
                'iq_actual_mg_stroke': mb.iq_actual_mg_stroke,
                'iq_specified_mg_stroke': mb.iq_specified_mg_stroke,
                'raw_block_data': mb.raw_data,
                'checksum_valid': mb.checksum_valid,
            }))
        
        if snapshot.mb007:
            mb = snapshot.mb007
            self._queue.put(('mb007', {
                'session_id': self._current_session_id,
                'timestamp': timestamp,
                'relative_ms': relative_ms,
                'coolant_temp_c': mb.coolant_temp_c,
                'intake_air_temp_c': mb.intake_air_temp_c,
                'fuel_temp_c': mb.fuel_temp_c,
                'oil_temp_c': mb.oil_temp_c,
                'ambient_temp_c': mb.ambient_temp_c,
                'egr_temp_c': mb.egr_temp_c,
                'raw_block_data': mb.raw_data,
                'checksum_valid': mb.checksum_valid,
            }))
        
        if snapshot.mb011:
            mb = snapshot.mb011
            self._queue.put(('mb011', {
                'session_id': self._current_session_id,
                'timestamp': timestamp,
                'relative_ms': relative_ms,
                'map_actual_mbar': mb.map_actual_mbar,
                'map_specified_mbar': mb.map_specified_mbar,
                'boost_pressure_mbar': mb.boost_pressure_mbar,
                'wastegate_duty_pct': mb.wastegate_duty_pct,
                'n75_valve_duty_pct': mb.n75_valve_duty_pct,
                'egr_duty_pct': mb.egr_duty_pct,
                'raw_block_data': mb.raw_data,
                'checksum_valid': mb.checksum_valid,
            }))
    
    def log_event(
        self,
        event_type: str,
        severity: str = "INFO",
        block_number: Optional[int] = None,
        expected_bytes: Optional[int] = None,
        received_bytes: Optional[int] = None,
        error_message: Optional[str] = None,
        raw_data: Optional[bytes] = None,
    ) -> None:
        """Queue diagnostic event for logging."""
        if self._current_session_id is None:
            return
        
        self._queue.put(('events', {
            'session_id': self._current_session_id,
            'timestamp': datetime.now(),
            'event_type': event_type,
            'severity': severity,
            'block_number': block_number,
            'expected_bytes': expected_bytes,
            'received_bytes': received_bytes,
            'error_message': error_message,
            'raw_data': raw_data,
        }))
    
    def log_ecu_identification(self, ecu_id: ECUIdentification) -> None:
        """Queue ECU identification for logging."""
        if self._current_session_id is None:
            return
        
        self._queue.put(('ecu_id', {
            'session_id': self._current_session_id,
            'part_number': ecu_id.part_number,
            'software_version': ecu_id.software_version,
            'engine_code': ecu_id.engine_code,
            'vehicle_identification': ecu_id.vehicle_identification,
            'date_of_manufacture': ecu_id.date_of_manufacture,
            'coding': ecu_id.coding,
            'raw_identification_block': ecu_id.raw_data,
            'parsed_at': datetime.now(),
        }))
    
    async def start(self) -> None:
        """Start the background flush task."""
        if self._running:
            return
        
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._queue_processor_task = asyncio.create_task(self._process_queue())
        logger.info("Database logger started")
    
    async def stop(self) -> None:
        """Stop the logger and flush remaining data."""
        self._running = False
        
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        if self._queue_processor_task:
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass
        
        # Final flush
        await self._flush_all_buffers()
        
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
        
        logger.info("Database logger stopped")
    
    async def _process_queue(self) -> None:
        """Process queued log entries from thread-safe queue."""
        while self._running:
            try:
                # Process up to 100 items per iteration
                for _ in range(100):
                    try:
                        table, data = self._queue.get_nowait()
                        self._buffers[table].append(data)
                    except Empty:
                        break
                
                # Check if any buffer needs flushing
                total_buffered = sum(len(buf) for buf in self._buffers.values())
                if total_buffered >= self.batch_size:
                    await self._flush_all_buffers()
                
                await asyncio.sleep(0.01)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                await asyncio.sleep(0.1)
    
    async def _flush_loop(self) -> None:
        """Periodic flush of buffers."""
        while self._running:
            try:
                await asyncio.sleep(self.bulk_interval)
                if self._running:
                    await self._flush_all_buffers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Flush loop error: {e}")
                await asyncio.sleep(1.0)
    
    async def _flush_all_buffers(self) -> None:
        """Flush all buffers to database."""
        async with self._lock:
            for table, buffer in self._buffers.items():
                if buffer:
                    await self._flush_buffer(table, buffer)
    
    async def _flush_buffer(self, table: str, buffer: deque) -> None:
        """Flush a single buffer using bulk INSERT."""
        if not buffer:
            return
        
        # Take up to batch_size items
        items = []
        for _ in range(min(len(buffer), self.batch_size)):
            items.append(buffer.popleft())
        
        if not items:
            return
        
        try:
            await self._bulk_insert(table, items)
            self._stats.bulk_inserts += 1
            self._stats.total_rows += len(items)
            self._stats.last_insert_time = time.monotonic()
            self._stats.avg_batch_size = (
                (self._stats.avg_batch_size * (self._stats.bulk_inserts - 1) + len(items))
                / self._stats.bulk_inserts
            )
        except Exception as e:
            self._stats.failed_inserts += len(items)
            self._stats.last_error = str(e)
            logger.error(f"Bulk insert failed for {table}: {e}")
            # Re-queue failed items
            for item in reversed(items):
                buffer.appendleft(item)
            raise
    
    async def _bulk_insert(self, table: str, items: List[Dict[str, Any]]) -> None:
        """Execute bulk INSERT for a table."""
        if not items:
            return
        
        columns = list(items[0].keys())
        placeholders = ', '.join(['%s'] * len(columns))
        columns_str = ', '.join(f'`{c}`' for c in columns)
        
        sql = f"INSERT INTO `{table}` ({columns_str}) VALUES ({placeholders})"
        
        values_list = [tuple(item[c] for c in columns) for item in items]
        
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(sql, values_list)
                await conn.commit()
    
    def get_stats(self) -> LogStats:
        """Get current logging statistics."""
        return self._stats
    
    @property
    def current_session_id(self) -> Optional[int]:
        return self._current_session_id
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def buffer_sizes(self) -> Dict[str, int]:
        return {table: len(buf) for table, buf in self._buffers.items()}


class SyncDatabaseLogger:
    """
    Synchronous wrapper for DatabaseLogger for use in non-async contexts.
    Uses a background thread with its own event loop.
    """
    
    def __init__(self, config: DBConfig, **kwargs):
        self._config = config
        self._kwargs = kwargs
        self._logger: Optional[DatabaseLogger] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._initialized = threading.Event()
        self._init_error: Optional[Exception] = None
    
    def start(self) -> None:
        """Start the logger in background thread."""
        if self._thread and self._thread.is_alive():
            return
        
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._initialized.wait(timeout=10.0)
        
        if self._init_error:
            raise self._init_error
    
    def _run_loop(self) -> None:
        """Run the async event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        try:
            self._logger = DatabaseLogger(self._config, **self._kwargs)
            self._loop.run_until_complete(self._logger.initialize())
            self._loop.run_until_complete(self._logger.start())
            self._initialized.set()
            self._loop.run_forever()
        except Exception as e:
            self._init_error = e
            self._initialized.set()
        finally:
            self._loop.close()
    
    def stop(self, timeout: float = 5.0) -> None:
        """Stop the logger."""
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._logger.stop(), self._loop)
            future.result(timeout=timeout)
        
        if self._thread and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=timeout)
    
    def start_session(self, *args, **kwargs) -> int:
        """Start a new session (blocking)."""
        if not self._loop:
            raise RuntimeError("Logger not started")
        future = asyncio.run_coroutine_threadsafe(
            self._logger.start_session(*args, **kwargs), self._loop
        )
        return future.result(timeout=10.0)
    
    def end_session(self) -> None:
        """End current session (blocking)."""
        if not self._loop:
            return
        future = asyncio.run_coroutine_threadsafe(self._logger.end_session(), self._loop)
        future.result(timeout=10.0)
    
    def log_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        """Log telemetry (non-blocking, thread-safe)."""
        if self._logger:
            self._logger.log_telemetry(snapshot)
    
    def log_event(self, *args, **kwargs) -> None:
        """Log event (non-blocking, thread-safe)."""
        if self._logger:
            self._logger.log_event(*args, **kwargs)
    
    def log_ecu_identification(self, ecu_id: ECUIdentification) -> None:
        """Log ECU identification (non-blocking, thread-safe)."""
        if self._logger:
            self._logger.log_ecu_identification(ecu_id)
    
    def get_stats(self) -> LogStats:
        """Get logging statistics."""
        if self._logger:
            return self._logger.get_stats()
        return LogStats()
    
    @property
    def current_session_id(self) -> Optional[int]:
        if self._logger:
            return self._logger.current_session_id
        return None
    
    @property
    def is_running(self) -> bool:
        if self._logger:
            return self._logger.is_running
        return False


# Convenience function for creating logger from config dict
def create_database_logger(config: dict) -> DatabaseLogger:
    """Create DatabaseLogger from configuration dictionary."""
    db_config = DBConfig(
        host=config.get('host', 'localhost'),
        port=config.get('port', 3306),
        database=config.get('database', 'audi_diag'),
        user=config.get('user', 'audi_diag'),
        password=config.get('password', 'change_me'),
        pool_size=config.get('pool_size', 5),
    )
    
    return DatabaseLogger(
        config=db_config,
        bulk_interval=config.get('bulk_insert_interval_s', 1.0),
        batch_size=config.get('bulk_batch_size', 100),
        auto_create_schema=config.get('auto_create_schema', True),
    )