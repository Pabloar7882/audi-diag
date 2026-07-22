"""
Async Telemetry Worker for Audi A4 B5 EDC15 Diagnostics.
Runs KW1281 communication loop in background thread, emits Qt signals for GUI updates.
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Awaitable
from collections import deque
import threading

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QTimer, QMutex, QWaitCondition

from kw1281_handler import (
    KW1281Handler,
    KW1281Error,
    KW1281TimeoutError,
    KW1281ChecksumError,
    KW1281ProtocolError,
    KW1281ConnectionError,
    ECUIdentification,
    MeasuringValue,
)

logger = logging.getLogger(__name__)


class WorkerState(Enum):
    """Worker lifecycle states"""
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    READING = "reading"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(slots=True)
class MeasuringBlock003:
    """Parsed Measuring Block 003: MAF & RPM"""
    rpm: int = 0
    maf_actual_mg_stroke: float = 0.0
    maf_specified_mg_stroke: float = 0.0
    engine_load_pct: int = 0
    throttle_position_pct: int = 0
    iq_actual_mg_stroke: float = 0.0
    iq_specified_mg_stroke: float = 0.0
    raw_fields: list = field(default_factory=list)  # list[MeasuringValue], see kw1281_handler
    timestamp: float = 0.0


@dataclass(slots=True)
class MeasuringBlock007:
    """Parsed Measuring Block 007: Temperatures"""
    coolant_temp_c: int = 0
    intake_air_temp_c: int = 0
    fuel_temp_c: int = 0
    oil_temp_c: int = 0
    ambient_temp_c: int = 0
    egr_temp_c: int = 0
    raw_fields: list = field(default_factory=list)
    timestamp: float = 0.0


@dataclass(slots=True)
class MeasuringBlock011:
    """Parsed Measuring Block 011: MAP/Boost"""
    map_actual_mbar: int = 0
    map_specified_mbar: int = 0
    boost_pressure_mbar: int = 0
    wastegate_duty_pct: int = 0
    n75_valve_duty_pct: int = 0
    egr_duty_pct: int = 0
    raw_fields: list = field(default_factory=list)
    timestamp: float = 0.0


@dataclass(slots=True)
class TelemetrySnapshot:
    """Complete telemetry snapshot from all requested blocks"""
    mb003: Optional[MeasuringBlock003] = None
    mb007: Optional[MeasuringBlock007] = None
    mb011: Optional[MeasuringBlock011] = None
    session_time_ms: int = 0
    timestamp: float = field(default_factory=time.time)


class TelemetryWorker(QObject):
    """
    Qt-compatible worker that runs KW1281 communication in a background thread.
    Emits signals for telemetry updates, connection status, and errors.
    """
    
    # Signals for GUI updates (thread-safe via Qt queued connections)
    telemetry_updated = pyqtSignal(object)  # TelemetrySnapshot
    ecu_identified = pyqtSignal(object)     # ECUIdentification
    connection_state_changed = pyqtSignal(str)  # WorkerState.value
    error_occurred = pyqtSignal(str, str)   # error_type, message
    stats_updated = pyqtSignal(dict)        # Statistics dict
    log_message = pyqtSignal(str, str)      # level, message
    
    # Default measuring blocks to poll (EDC15 AFN)
    DEFAULT_BLOCKS = [3, 7, 11]
    
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 10400,
        poll_interval_ms: int = 100,
        blocks: Optional[list[int]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        
        self.port = port
        self.baudrate = baudrate
        self.poll_interval_ms = poll_interval_ms
        self.blocks = blocks or self.DEFAULT_BLOCKS
        
        # Internal state
        self._state = WorkerState.IDLE
        self._running = False
        self._handler: Optional[KW1281Handler] = None
        self._session_start_time: Optional[float] = None
        self._ecu_id: Optional[ECUIdentification] = None
        
        # Statistics
        self._stats = {
            'total_polls': 0,
            'successful_polls': 0,
            'failed_polls': 0,
            'timeouts': 0,
            'checksum_errors': 0,
            'protocol_errors': 0,
            'reconnects': 0,
            'blocks_read': {b: 0 for b in self.blocks},
            'last_poll_ms': 0,
            'avg_poll_ms': 0.0,
        }
        self._poll_times = deque(maxlen=100)
        
        # Async loop management
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        
        # Mutex for thread-safe state access
        self._mutex = QMutex()
    
    @property
    def state(self) -> WorkerState:
        with QMutex(self._mutex):
            return self._state
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def ecu_identification(self) -> Optional[ECUIdentification]:
        return self._ecu_id
    
    def start(self) -> None:
        """Start the worker thread and async event loop."""
        if self._running:
            logger.warning("Worker already running")
            return
        
        self._running = True
        self._stop_event.clear()
        self._set_state(WorkerState.CONNECTING)
        
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        logger.info("Telemetry worker thread started")
    
    def stop(self) -> None:
        """Stop the worker gracefully."""
        if not self._running:
            return
        
        logger.info("Stopping telemetry worker...")
        self._running = False
        self._stop_event.set()
        self._set_state(WorkerState.STOPPING)
        
        if self._loop and self._loop.is_running():
            # Schedule disconnect on the async loop
            asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        
        self._set_state(WorkerState.STOPPED)
        logger.info("Telemetry worker stopped")
    
    def _run_event_loop(self) -> None:
        """Run the asyncio event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            logger.exception(f"Event loop error: {e}")
            self.error_occurred.emit("EventLoopError", str(e))
        finally:
            self._loop.close()
            self._loop = None
    
    async def _shutdown_async(self) -> None:
        """Async cleanup."""
        if self._handler:
            try:
                await self._handler.stop_communication()
            except Exception as e:
                logger.debug(f"Error during handler shutdown: {e}")
            self._handler = None
    
    async def _main_loop(self) -> None:
        """Main async loop: connect -> poll blocks -> handle reconnection."""
        while self._running and not self._stop_event.is_set():
            try:
                await self._connect_and_identify()
                
                # Main polling loop
                while self._running and not self._stop_event.is_set():
                    await self._poll_cycle()
                    await asyncio.sleep(self.poll_interval_ms / 1000)
                    
            except KW1281ConnectionError as e:
                logger.warning(f"Connection lost: {e}")
                self.error_occurred.emit("ConnectionError", str(e))
                await self._handle_reconnect()
            except KW1281TimeoutError as e:
                logger.warning(f"Timeout: {e}")
                self._stats['timeouts'] += 1
                self.error_occurred.emit("Timeout", str(e))
                await self._handle_reconnect()
            except KW1281ChecksumError as e:
                logger.warning(f"Checksum error: {e}")
                self._stats['checksum_errors'] += 1
                self.error_occurred.emit("ChecksumError", str(e))
                # Don't reconnect on checksum error, just continue
            except KW1281ProtocolError as e:
                logger.warning(f"Protocol error: {e}")
                self._stats['protocol_errors'] += 1
                self.error_occurred.emit("ProtocolError", str(e))
                await self._handle_reconnect()
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")
                self.error_occurred.emit("UnexpectedError", str(e))
                await self._handle_reconnect()
    
    async def _connect_and_identify(self) -> None:
        """Establish connection and read ECU identification."""
        self._set_state(WorkerState.CONNECTING)
        self.log_message.emit("INFO", f"Connecting to {self.port} at {self.baudrate} baud...")
        
        self._handler = KW1281Handler(
            port=self.port,
            baudrate=self.baudrate,
            timeout=1.0,
            write_timeout=1.0,
        )
        
        self._ecu_id = await self._handler.connect()
        self._session_start_time = time.monotonic()
        
        self.ecu_identified.emit(self._ecu_id)
        self.log_message.emit("INFO", f"ECU Identified: {self._ecu_id.part_number} SW:{self._ecu_id.software_version}")
        
        self._set_state(WorkerState.CONNECTED)
        self._reconnect_delay = 1.0  # Reset reconnect delay on success
    
    async def _poll_cycle(self) -> None:
        """Single polling cycle for all configured blocks."""
        if not self._handler or not self._handler.is_connected:
            raise KW1281ConnectionError("Handler not connected")
        
        self._set_state(WorkerState.READING)
        cycle_start = time.monotonic()
        
        snapshot = TelemetrySnapshot()
        snapshot.session_time_ms = int((time.monotonic() - self._session_start_time) * 1000)
        
        # Poll each block sequentially
        for block_num in self.blocks:
            if not self._running or self._stop_event.is_set():
                break
            
            try:
                raw_data = await self._handler.read_measuring_block(block_num)
                
                if block_num == 3:
                    snapshot.mb003 = self._parse_block_003(raw_data)
                    self._stats['blocks_read'][3] += 1
                elif block_num == 7:
                    snapshot.mb007 = self._parse_block_007(raw_data)
                    self._stats['blocks_read'][7] += 1
                elif block_num == 11:
                    snapshot.mb011 = self._parse_block_011(raw_data)
                    self._stats['blocks_read'][11] += 1
                    
                self._stats['successful_polls'] += 1
                
            except KW1281TimeoutError:
                self._stats['timeouts'] += 1
                self._stats['failed_polls'] += 1
                raise
            except KW1281ChecksumError:
                self._stats['checksum_errors'] += 1
                self._stats['failed_polls'] += 1
                raise
            except KW1281ProtocolError:
                self._stats['protocol_errors'] += 1
                self._stats['failed_polls'] += 1
                raise
            except Exception as e:
                self._stats['failed_polls'] += 1
                logger.debug(f"Block {block_num} read failed: {e}")
                raise
            
            self._stats['total_polls'] += 1
        
        # Update timing stats
        poll_ms = (time.monotonic() - cycle_start) * 1000
        self._poll_times.append(poll_ms)
        self._stats['last_poll_ms'] = poll_ms
        self._stats['avg_poll_ms'] = sum(self._poll_times) / len(self._poll_times)
        
        # Emit telemetry to GUI thread
        self.telemetry_updated.emit(snapshot)
        self.stats_updated.emit(self._stats.copy())
    
    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        if not self._running or self._stop_event.is_set():
            return
        
        self._set_state(WorkerState.RECONNECTING)
        self._stats['reconnects'] += 1
        
        self.log_message.emit("WARNING", f"Reconnecting in {self._reconnect_delay:.1f}s... (attempt #{self._stats['reconnects']})")
        
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 1.5, self._max_reconnect_delay)
    
    def _parse_block_003(self, values: list[MeasuringValue]) -> MeasuringBlock003:
        """
        Build MeasuringBlock003 from a decoded group-reading response.

        Kennzahl 1 (Engine Speed) is a confirmed field type per the public
        KW1281 reference table, so RPM is mapped confidently wherever it
        appears in the group. The remaining fields are NOT reliably
        identifiable from the kennzahl alone (multiple physical quantities
        can share a kennzahl depending on the ECU) - they're assigned by
        position as a best-effort guess. Cross-check raw_fields against the
        real car before trusting anything except RPM here.
        """
        mb = MeasuringBlock003(raw_fields=values, timestamp=time.time())

        remaining = []
        for mv in values:
            if mv.kennzahl == 1 and mb.rpm == 0:
                mb.rpm = int(round(mv.value))
            else:
                remaining.append(mv)

        if len(remaining) > 0:
            mb.maf_actual_mg_stroke = remaining[0].value
        if len(remaining) > 1:
            mb.maf_specified_mg_stroke = remaining[1].value
        if len(remaining) > 2:
            mb.engine_load_pct = int(round(remaining[2].value))
        if len(remaining) > 3:
            mb.throttle_position_pct = int(round(remaining[3].value))

        return mb
    
    def _parse_block_007(self, values: list[MeasuringValue]) -> MeasuringBlock007:
        """
        Build MeasuringBlock007 from a decoded group-reading response.

        Kennzahl 5 (Temperature) is confirmed as *a* temperature per the
        reference table, but the same kennzahl is documented to mean
        different sensors (coolant vs. oil vs. ambient, etc.) on different
        ECUs/groups - there is no way to tell them apart from the kennzahl
        alone. Fields are assigned by position as a best-effort guess;
        verify against the real car (e.g. coolant should track known engine
        warm-up behavior) before trusting which gauge is which sensor.
        """
        mb = MeasuringBlock007(raw_fields=values, timestamp=time.time())

        temps = [mv for mv in values if mv.kennzahl == 5]
        others = [mv for mv in values if mv.kennzahl != 5]
        ordered = temps + others  # temperature fields first, whatever's left after

        if len(ordered) > 0:
            mb.coolant_temp_c = int(round(ordered[0].value))
        if len(ordered) > 1:
            mb.intake_air_temp_c = int(round(ordered[1].value))
        if len(ordered) > 2:
            mb.fuel_temp_c = int(round(ordered[2].value))
        if len(ordered) > 3:
            mb.oil_temp_c = int(round(ordered[3].value))

        return mb
    
    def _parse_block_011(self, values: list[MeasuringValue]) -> MeasuringBlock011:
        """
        Build MeasuringBlock011 from a decoded group-reading response.

        Kennzahl 18 (Absolute Pressure) is confirmed as *a* pressure per
        the reference table, but which one (MAP actual vs specified vs
        atmospheric) again depends on group position, which is ECU/label
        specific and not reliably guessable. Assigned by position as a
        best-effort guess - verify against the real car.
        """
        mb = MeasuringBlock011(raw_fields=values, timestamp=time.time())

        pressures = [mv for mv in values if mv.kennzahl == 18]
        others = [mv for mv in values if mv.kennzahl != 18]
        ordered = pressures + others

        if len(ordered) > 0:
            mb.map_actual_mbar = int(round(ordered[0].value))
            mb.boost_pressure_mbar = mb.map_actual_mbar - 1000  # vs. ~1000 mbar atmospheric
        if len(ordered) > 1:
            mb.map_specified_mbar = int(round(ordered[1].value))
        if len(ordered) > 2:
            mb.wastegate_duty_pct = int(round(ordered[2].value))
        if len(ordered) > 3:
            mb.egr_duty_pct = int(round(ordered[3].value))
            mb.n75_valve_duty_pct = mb.wastegate_duty_pct

        
        return mb
    
    def _set_state(self, state: WorkerState) -> None:
        """Thread-safe state update with signal emission."""
        with QMutex(self._mutex):
            if self._state != state:
                self._state = state
                # Emit on GUI thread via queued connection
                self.connection_state_changed.emit(state.value)
    
    def get_stats(self) -> dict:
        """Get current statistics (thread-safe)."""
        with QMutex(self._mutex):
            return self._stats.copy()


class TelemetryThread(QThread):
    """
    QThread wrapper for TelemetryWorker.
    Provides simple start/stop interface for Qt applications.
    """
    
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 10400,
        poll_interval_ms: int = 100,
        blocks: Optional[list[int]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.worker = TelemetryWorker(port, baudrate, poll_interval_ms, blocks)
        self.worker.moveToThread(self)
        
        # Forward worker signals
        self.telemetry_updated = self.worker.telemetry_updated
        self.ecu_identified = self.worker.ecu_identified
        self.connection_state_changed = self.worker.connection_state_changed
        self.error_occurred = self.worker.error_occurred
        self.stats_updated = self.worker.stats_updated
        self.log_message = self.worker.log_message
    
    def run(self) -> None:
        """QThread entry point - starts the worker."""
        self.worker.start()
        # Keep thread alive until worker stops
        while self.worker.is_running:
            self.msleep(100)
    
    def stop(self) -> None:
        """Stop the worker and thread."""
        self.worker.stop()
        self.quit()
        self.wait(5000)


# Async-compatible version for non-Qt contexts
class AsyncTelemetryWorker:
    """
    Pure asyncio version of telemetry worker (no Qt dependencies).
    Use for headless logging or testing.
    """
    
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 10400,
        poll_interval_ms: int = 100,
        blocks: Optional[list[int]] = None,
        on_telemetry: Optional[Callable[[TelemetrySnapshot], Awaitable[None]]] = None,
        on_error: Optional[Callable[[Exception], Awaitable[None]]] = None,
        on_ecu_id: Optional[Callable[[ECUIdentification], Awaitable[None]]] = None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.poll_interval_ms = poll_interval_ms
        self.blocks = blocks or [3, 7, 11]
        
        self._callbacks = {
            'telemetry': on_telemetry,
            'error': on_error,
            'ecu_id': on_ecu_id,
        }
        
        self._handler: Optional[KW1281Handler] = None
        self._running = False
        self._session_start: Optional[float] = None
        self._stats = {b: 0 for b in self.blocks}
    
    async def start(self) -> None:
        """Start the telemetry loop."""
        self._running = True
        await self._run_loop()
    
    async def stop(self) -> None:
        """Stop the telemetry loop."""
        self._running = False
        if self._handler:
            await self._handler.stop_communication()
    
    async def _run_loop(self) -> None:
        while self._running:
            try:
                self._handler = KW1281Handler(self.port, self.baudrate)
                ecu_id = await self._handler.connect()
                
                if self._callbacks['ecu_id']:
                    await self._callbacks['ecu_id'](ecu_id)
                
                self._session_start = time.monotonic()
                
                while self._running:
                    snapshot = await self._poll_all_blocks()
                    if self._callbacks['telemetry']:
                        await self._callbacks['telemetry'](snapshot)
                    
                    await asyncio.sleep(self.poll_interval_ms / 1000)
                    
            except Exception as e:
                if self._callbacks['error']:
                    await self._callbacks['error'](e)
                await asyncio.sleep(2)  # Backoff
    
    async def _poll_all_blocks(self) -> TelemetrySnapshot:
        snapshot = TelemetrySnapshot()
        snapshot.session_time_ms = int((time.monotonic() - self._session_start) * 1000)
        
        for block_num in self.blocks:
            raw = await self._handler.read_measuring_block(block_num)
            if block_num == 3:
                snapshot.mb003 = self._parse_block_003(raw)
            elif block_num == 7:
                snapshot.mb007 = self._parse_block_007(raw)
            elif block_num == 11:
                snapshot.mb011 = self._parse_block_011(raw)
        
        return snapshot
    
    def _parse_block_003(self, values: list[MeasuringValue]) -> MeasuringBlock003:
        return TelemetryWorker._parse_block_003(self, values)

    def _parse_block_007(self, values: list[MeasuringValue]) -> MeasuringBlock007:
        return TelemetryWorker._parse_block_007(self, values)

    def _parse_block_011(self, values: list[MeasuringValue]) -> MeasuringBlock011:
        return TelemetryWorker._parse_block_011(self, values)