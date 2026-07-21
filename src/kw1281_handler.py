"""
KW1281 Protocol Handler for VAG EDC15 ECU (1999 Audi A4 B5 1.9 TDI AFN)
Implements 5-baud init sequence, 10400 baud communication, block reading with ACK handling.
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Callable, Awaitable
from collections import deque

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)


class KWP1281Address(IntEnum):
    """KW1281 Address bytes"""
    ECU = 0x11
    TESTER = 0xF1


class KWP1281Commands(IntEnum):
    """KW1281 Service IDs (per VAG KW1281 spec)"""
    START_COMMUNICATION = 0x81
    STOP_COMMUNICATION = 0x82
    READ_DATA_BY_LOCAL_ID = 0x21
    READ_ECU_IDENTIFICATION = 0x1A
    READ_MEASURING_BLOCKS = 0x1B
    SECURITY_ACCESS = 0x27
    CONTROL_MODULE_SELF_TEST = 0x04


class KWP1281ResponseCodes(IntEnum):
    """Response codes"""
    POSITIVE_RESPONSE = 0x40  # Base for positive responses (command + 0x40)
    NEGATIVE_RESPONSE = 0x7F
    NRC_SUB_FUNCTION_NOT_SUPPORTED = 0x12
    NRC_INCORRECT_MESSAGE_LENGTH = 0x13
    NRC_CONDITIONS_NOT_CORRECT = 0x22
    NRC_REQUEST_SEQUENCE_ERROR = 0x24
    NRC_SECURITY_ACCESS_DENIED = 0x33
    NRC_INVALID_KEY = 0x35
    NRC_EXCEEDED_ATTEMPTS = 0x36


class BlockType(IntEnum):
    """KW1281 Block types"""
    ACK = 0x00
    DATA = 0x01
    END_OF_COMMUNICATION = 0x02
    NEGATIVE_ACK = 0x03


@dataclass(slots=True)
class KW1281Block:
    """Parsed KW1281 block"""
    block_type: BlockType
    block_counter: int
    data: bytes
    checksum_valid: bool
    raw_bytes: bytes


@dataclass(slots=True)
class ECUIdentification:
    """Parsed ECU identification from block 0x1A response"""
    part_number: str = ""
    software_version: str = ""
    engine_code: str = ""
    vehicle_identification: str = ""
    date_of_manufacture: str = ""
    coding: str = ""
    raw_data: bytes = b""


class KW1281Error(Exception):
    """Base exception for KW1281 errors"""
    pass


class KW1281TimeoutError(KW1281Error):
    """Timeout during communication"""
    pass


class KW1281ChecksumError(KW1281Error):
    """Checksum verification failed"""
    pass


class KW1281ProtocolError(KW1281Error):
    """Protocol violation (unexpected block type, counter mismatch, etc.)"""
    pass


class KW1281ConnectionError(KW1281Error):
    """Connection lost or cannot establish"""
    pass


class KW1281Handler:
    """
    KW1281 Protocol Handler for EDC15 ECU over K-Line (FTDI USB-KKL adapter).
    
    Implements:
    - 5-baud wakeup sequence (ISO 9141-2 / KWP1281)
    - Baud rate switch to 10400 baud
    - Block-level communication with ACK/NAK handling
    - Measuring block reading (003, 007, 011)
    - ECU identification reading
    """
    
    # Timing constants (milliseconds)
    T_5BAUD_BIT_MS = 200  # 5 baud = 200ms per bit
    T_WAKEUP_PULSE_MS = 25  # Initial low pulse
    T_INIT_DELAY_MS = 50  # Delay after address byte
    T_KEYWORD_DELAY_MS = 5  # Delay between keyword bytes
    T_10400_SWITCH_DELAY_MS = 300  # Delay after init before switching baud
    T_BLOCK_TIMEOUT_MS = 500  # Timeout waiting for block
    T_INTER_BLOCK_DELAY_MS = 5  # Delay between blocks
    
    # 5-baud init sequence: 0x33 (00110011) at 5 baud
    INIT_ADDRESS_BYTE = 0x33
    KEYWORD_1 = 0x01
    KEYWORD_2 = 0x8A  # 10400 baud keyword
    
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 10400,
        timeout: float = 1.0,
        write_timeout: float = 1.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        
        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._block_counter = 0
        self._expected_counter = 0
        self._last_block_time = 0.0
        self._lock = asyncio.Lock()
        
        # Callbacks for async events
        self.on_block_received: Optional[Callable[[KW1281Block], Awaitable[None]]] = None
        self.on_error: Optional[Callable[[Exception], Awaitable[None]]] = None
        self.on_event: Optional[Callable[[str, dict], Awaitable[None]]] = None
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open
    
    async def connect(self) -> ECUIdentification:
        """
        Full connection sequence: 5-baud init -> 10400 baud -> Start Communication -> Read ECU ID.
        Returns parsed ECU identification.
        """
        async with self._lock:
            if self.is_connected:
                logger.warning("Already connected, disconnecting first")
                await self.disconnect()
            
            # Phase 1: 5-baud initialization
            await self._five_baud_init()
            
            # Phase 2: Switch to 10400 baud and start communication
            await self._start_communication()
            
            # Phase 3: Read ECU identification
            ecu_id = await self.read_ecu_identification()
            
            self._connected = True
            logger.info(f"Connected to ECU: {ecu_id.part_number} SW: {ecu_id.software_version}")
            return ecu_id
    
    async def _five_baud_init(self) -> None:
        """
        Execute 5-baud initialization sequence per ISO 9141-2 / VAG KW1281.
        
        Sequence:
        1. Open serial at 5 baud, 8N1
        2. Send address byte 0x33 (00110011) at 5 baud
        3. Wait for ECU to echo 0x55 (synchronization)
        4. Send Keyword 1 (0x01) and Keyword 2 (0x8A for 10400 baud)
        5. Wait for ECU to acknowledge with inverted keywords
        6. Close 5-baud connection, wait, reopen at 10400 baud
        """
        logger.info(f"Starting 5-baud init on {self.port}")
        
        # Open at 5 baud
        ser = serial.Serial(
            port=self.port,
            baudrate=5,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=2.0,  # Generous timeout for 5 baud
            write_timeout=2.0,
        )
        
        try:
            # Clear buffers
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            # Step 1: Send wakeup pulse (optional, some adapters need it)
            ser.set_break(True)
            await asyncio.sleep(self.T_WAKEUP_PULSE_MS / 1000)
            ser.set_break(False)
            await asyncio.sleep(0.05)
            
            # Step 2: Send address byte 0x33 at 5 baud
            logger.debug("Sending address byte 0x33 at 5 baud")
            ser.write(bytes([self.INIT_ADDRESS_BYTE]))
            await self._drain_at_baud(ser, 5, 1)
            
            # Step 3: Wait for sync byte 0x55 from ECU (with timeout)
            sync_byte = await self._read_byte_with_timeout(ser, timeout=1.0)
            if sync_byte != 0x55:
                raise KW1281ConnectionError(
                    f"Expected sync byte 0x55, got 0x{sync_byte:02X}" if sync_byte is not None 
                    else "Timeout waiting for sync byte 0x55"
                )
            logger.debug("Received sync byte 0x55")
            
            # Step 4: Send Keyword 1 (0x01)
            await asyncio.sleep(self.T_KEYWORD_DELAY_MS / 1000)
            ser.write(bytes([self.KEYWORD_1]))
            await self._drain_at_baud(ser, 5, 1)
            
            # Step 5: Wait for inverted Keyword 1 (0xFE)
            kw1_echo = await self._read_byte_with_timeout(ser, timeout=0.5)
            if kw1_echo != 0xFE:
                raise KW1281ConnectionError(
                    f"Expected inverted KW1 0xFE, got 0x{kw1_echo:02X}" if kw1_echo is not None
                    else "Timeout waiting for inverted KW1"
                )
            logger.debug("Received inverted KW1 (0xFE)")
            
            # Step 6: Send Keyword 2 (0x8A for 10400 baud)
            await asyncio.sleep(self.T_KEYWORD_DELAY_MS / 1000)
            ser.write(bytes([self.KEYWORD_2]))
            await self._drain_at_baud(ser, 5, 1)
            
            # Step 7: Wait for inverted Keyword 2 (0x75)
            kw2_echo = await self._read_byte_with_timeout(ser, timeout=0.5)
            if kw2_echo != 0x75:
                raise KW1281ConnectionError(
                    f"Expected inverted KW2 0x75, got 0x{kw2_echo:02X}" if kw2_echo is not None
                    else "Timeout waiting for inverted KW2"
                )
            logger.debug("Received inverted KW2 (0x75) - 10400 baud confirmed")
            
            # Step 8: Wait for ECU to switch baud rate
            await asyncio.sleep(self.T_10400_SWITCH_DELAY_MS / 1000)
            
        finally:
            ser.close()
        
        logger.info("5-baud initialization complete, switching to 10400 baud")
    
    async def _drain_at_baud(self, ser: serial.Serial, baudrate: int, byte_count: int) -> None:
        """Wait for bytes to be transmitted at given baudrate."""
        # Time for byte_count bytes at baudrate (10 bits per byte: 1 start + 8 data + 1 stop)
        bit_time = 1.0 / baudrate
        byte_time = bit_time * 10 * byte_count
        await asyncio.sleep(byte_time * 1.5)  # 1.5x safety margin
    
    async def _read_byte_with_timeout(self, ser: serial.Serial, timeout: float) -> Optional[int]:
        """Read a single byte with timeout."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if ser.in_waiting > 0:
                data = ser.read(1)
                if data:
                    return data[0]
            await asyncio.sleep(0.001)
        return None
    
    async def _start_communication(self) -> None:
        """Open 10400 baud connection and send Start Communication (0x81)."""
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=self.write_timeout,
        )
        
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        
        # Send Start Communication (0x81) with tester address
        await self._send_block(
            address=KWP1281Address.TESTER,
            command=KWP1281Commands.START_COMMUNICATION,
            data=bytes([0x10, 0x01, 0x00])  # Diagnostic session type
        )
        
        # Wait for positive response (0xC1 = 0x81 + 0x40)
        block = await self._read_block()
        if block.block_type != BlockType.DATA or block.data[0] != 0xC1:
            raise KW1281ProtocolError(f"StartCommunication failed: {block}")
        
        logger.debug("Start Communication successful")
        self._block_counter = 0
        self._expected_counter = 1
    
    async def _send_block(
        self,
        address: int,
        command: int,
        data: bytes = b"",
        block_type: BlockType = BlockType.DATA
    ) -> None:
        """Send a KW1281 block with proper framing and checksum."""
        if not self._serial or not self._serial.is_open:
            raise KW1281ConnectionError("Serial port not open")
        
        # Build block: [Length][Address][Command][Data...][Checksum]
        # Length includes Address + Command + Data + Checksum (but not Length byte itself)
        payload = bytes([address, command]) + data
        length = len(payload) + 1  # +1 for checksum
        checksum = self._calculate_checksum(payload)
        
        block = bytes([length]) + payload + bytes([checksum])
        
        logger.debug(f"TX: {block.hex(' ').upper()}")
        
        # Write with thread safety
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._serial.write, block)
        await loop.run_in_executor(None, self._serial.flush)
        
        # Inter-block delay
        await asyncio.sleep(self.T_INTER_BLOCK_DELAY_MS / 1000)
    
    async def _read_block(self, timeout: Optional[float] = None) -> KW1281Block:
        """Read a complete KW1281 block with timeout."""
        if not self._serial or not self._serial.is_open:
            raise KW1281ConnectionError("Serial port not open")
        
        timeout = timeout or (self.T_BLOCK_TIMEOUT_MS / 1000)
        start_time = time.monotonic()
        
        # Read length byte
        length_byte = await self._read_byte_async(timeout)
        if length_byte is None:
            raise KW1281TimeoutError("Timeout reading block length")
        
        length = length_byte
        if length < 3 or length > 260:  # Min: addr+cmd+checksum, Max: KWP1281 max
            raise KW1281ProtocolError(f"Invalid block length: {length}")
        
        # Read remaining bytes (length - 1 because length byte already read)
        remaining = length
        payload = bytearray()
        
        while len(payload) < remaining:
            elapsed = time.monotonic() - start_time
            if elapsed > timeout:
                raise KW1281TimeoutError(f"Timeout reading block payload ({len(payload)}/{remaining} bytes)")
            
            chunk = await self._read_bytes_async(remaining - len(payload), timeout - elapsed)
            if not chunk:
                raise KW1281TimeoutError("Connection closed during block read")
            payload.extend(chunk)
        
        # Parse block
        return self._parse_block(bytes([length_byte]) + payload)
    
    async def _read_byte_async(self, timeout: float) -> Optional[int]:
        """Read a single byte asynchronously."""
        loop = asyncio.get_event_loop()
        start = time.monotonic()
        
        while time.monotonic() - start < timeout:
            if self._serial.in_waiting > 0:
                data = await loop.run_in_executor(None, self._serial.read, 1)
                if data:
                    return data[0]
            await asyncio.sleep(0.001)
        return None
    
    async def _read_bytes_async(self, count: int, timeout: float) -> bytes:
        """Read multiple bytes asynchronously."""
        loop = asyncio.get_event_loop()
        start = time.monotonic()
        result = bytearray()
        
        while len(result) < count and time.monotonic() - start < timeout:
            available = self._serial.in_waiting
            if available > 0:
                to_read = min(available, count - len(result))
                data = await loop.run_in_executor(None, self._serial.read, to_read)
                if data:
                    result.extend(data)
            else:
                await asyncio.sleep(0.001)
        
        return bytes(result)
    
    def _calculate_checksum(self, data: bytes) -> int:
        """Calculate KW1281 checksum (8-bit sum of all bytes, inverted + 1)."""
        # Checksum = 0x100 - (sum of bytes & 0xFF)
        return (0x100 - (sum(data) & 0xFF)) & 0xFF
    
    def _verify_checksum(self, block: bytes) -> bool:
        """Verify block checksum."""
        if len(block) < 3:
            return False
        # Checksum is last byte, data is everything except length and checksum
        data = block[1:-1]
        expected = self._calculate_checksum(data)
        return block[-1] == expected
    
    def _parse_block(self, raw: bytes) -> KW1281Block:
        """Parse raw block into KW1281Block."""
        if len(raw) < 4:
            raise KW1281ProtocolError(f"Block too short: {len(raw)} bytes")
        
        length = raw[0]
        address = raw[1]
        command_or_type = raw[2]
        data = raw[3:-1] if len(raw) > 4 else b""
        checksum_valid = self._verify_checksum(raw)
        
        # Determine block type from address/command
        if address == KWP1281Address.ECU:
            if command_or_type == 0x00:
                block_type = BlockType.ACK
            elif command_or_type == 0x03:
                block_type = BlockType.NEGATIVE_ACK
            else:
                block_type = BlockType.DATA
        else:
            block_type = BlockType.DATA
        
        # Extract block counter from data if present (usually first byte of data for data blocks)
        block_counter = 0
        if block_type == BlockType.DATA and len(data) > 0:
            block_counter = data[0]
        
        return KW1281Block(
            block_type=block_type,
            block_counter=block_counter,
            data=data,
            checksum_valid=checksum_valid,
            raw_bytes=raw
        )
    
    async def read_ecu_identification(self) -> ECUIdentification:
        """Read ECU identification (Service 0x1A)."""
        await self._send_block(
            address=KWP1281Address.TESTER,
            command=KWP1281Commands.READ_ECU_IDENTIFICATION,
            data=b""
        )
        
        # Read identification blocks (multiple blocks possible)
        id_data = bytearray()
        while True:
            block = await self._read_block()
            
            if not block.checksum_valid:
                raise KW1281ChecksumError(f"Checksum error in ID block: {block.raw_bytes.hex()}")
            
            if block.block_type == BlockType.ACK:
                # Send next block request
                await self._send_ack()
                continue
            elif block.block_type == BlockType.DATA:
                id_data.extend(block.data[1:])  # Skip block counter
                await self._send_ack()
            elif block.block_type == BlockType.END_OF_COMMUNICATION:
                break
            else:
                raise KW1281ProtocolError(f"Unexpected block type in ID read: {block.block_type}")
        
        return self._parse_ecu_identification(bytes(id_data))
    
    def _parse_ecu_identification(self, data: bytes) -> ECUIdentification:
        """Parse raw ECU identification data (ASCII/BCD mixed)."""
        ecu = ECUIdentification(raw_data=data)
        
        if not data:
            return ecu
        
        # EDC15 identification format (VAG-specific)
        # Typically: Part Number (10 bytes ASCII), SW Version (6 bytes ASCII), etc.
        try:
            # Try to decode as ASCII where possible
            text = data.decode('ascii', errors='ignore')
            
            # Common patterns in VAG ECU ID strings
            lines = text.split('\x00')
            lines = [l.strip() for l in lines if l.strip()]
            
            for line in lines:
                if len(line) >= 10 and line[:3].isdigit():
                    ecu.part_number = line[:10]
                elif 'SW' in line.upper() or 'VERSION' in line.upper():
                    ecu.software_version = line
                elif line in ('AFN', 'AHU', '1Z', 'AFK', 'ALE', 'ALH'):
                    ecu.engine_code = line
                elif len(line) == 17:  # VIN-like
                    ecu.vehicle_identification = line
                    
        except Exception as e:
            logger.warning(f"Failed to parse ECU ID text: {e}")
        
        # Fallback: extract from known offsets (EDC15 typical layout)
        if len(data) >= 32:
            if not ecu.part_number:
                ecu.part_number = data[0:10].decode('ascii', errors='ignore').strip()
            if not ecu.software_version:
                ecu.software_version = data[10:16].decode('ascii', errors='ignore').strip()
            if not ecu.engine_code:
                ecu.engine_code = data[16:20].decode('ascii', errors='ignore').strip()
        
        return ecu
    
    async def read_measuring_block(self, block_number: int) -> bytes:
        """
        Read a measuring block (Service 0x1B / 0x21).
        Returns raw block data (12 bytes typical for EDC15).
        """
        if not 1 <= block_number <= 255:
            raise ValueError(f"Invalid block number: {block_number}")
        
        # Request measuring block using ReadDataByLocalIdentifier (0x21)
        # Block number is the local identifier
        await self._send_block(
            address=KWP1281Address.TESTER,
            command=KWP1281Commands.READ_DATA_BY_LOCAL_ID,
            data=bytes([block_number])
        )
        
        # Read response blocks
        block_data = bytearray()
        while True:
            block = await self._read_block()
            
            if not block.checksum_valid:
                raise KW1281ChecksumError(f"Checksum error in MB {block_number}: {block.raw_bytes.hex()}")
            
            if block.block_type == BlockType.ACK:
                await self._send_ack()
                continue
            elif block.block_type == BlockType.DATA:
                # First byte of data is block counter, rest is payload
                if len(block.data) > 1:
                    block_data.extend(block.data[1:])
                await self._send_ack()
            elif block.block_type == BlockType.END_OF_COMMUNICATION:
                break
            elif block.block_type == BlockType.NEGATIVE_ACK:
                raise KW1281ProtocolError(f"NAK received for block {block_number}")
            else:
                raise KW1281ProtocolError(f"Unexpected block type: {block.block_type}")
        
        return bytes(block_data)
    
    async def _send_ack(self) -> None:
        """Send ACK block (BlockType.ACK with current counter)."""
        ack_block = bytes([
            0x03,  # Length: addr + type + counter + checksum = 3
            KWP1281Address.TESTER,
            BlockType.ACK,
            self._expected_counter & 0xFF,
            0x00  # Placeholder, will be replaced by _send_block
        ])
        # Calculate proper checksum
        payload = ack_block[1:-1]
        checksum = self._calculate_checksum(payload)
        ack_block = ack_block[:-1] + bytes([checksum])
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._serial.write, ack_block)
        await loop.run_in_executor(None, self._serial.flush)
        
        self._expected_counter = (self._expected_counter + 1) & 0xFF
    
    async def stop_communication(self) -> None:
        """Send Stop Communication (0x82) and close connection."""
        if self._serial and self._serial.is_open:
            try:
                await self._send_block(
                    address=KWP1281Address.TESTER,
                    command=KWP1281Commands.STOP_COMMUNICATION,
                    data=b""
                )
                # Read final ACK
                await self._read_block(timeout=0.5)
            except Exception as e:
                logger.debug(f"Error during stop communication: {e}")
        
        await self.disconnect()
    
    async def disconnect(self) -> None:
        """Close serial connection."""
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        self._connected = False
        logger.info("Disconnected from ECU")
    
    async def __aenter__(self) -> KW1281Handler:
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()


# Convenience function for scanning ports
def find_kkl_adapters() -> list[dict]:
    """Find FTDI-based KKL adapters."""
    adapters = []
    for port in serial.tools.list_ports.comports():
        if port.vid == 0x0403 and port.pid in (0x6001, 0x6010, 0x6011, 0x6014, 0x6015):
            adapters.append({
                'device': port.device,
                'description': port.description,
                'vid': port.vid,
                'pid': port.pid,
                'serial': port.serial_number,
            })
    return adapters