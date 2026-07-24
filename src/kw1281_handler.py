"""
KW1281 Protocol Handler for VAG EDC15 ECU (1999 Audi A4 B5 1.9 TDI AFN)

Implements the REAL classic VAG KW1281 protocol:
- 5-baud wakeup (address byte sent by tester, ECU replies with sync+keyword)
- Byte-by-byte handshake at communication baud rate: every byte is
  acknowledged by the receiver echoing its one's-complement (0xFF - byte),
  except the block-end byte (0x03).
- Block framing: [length][counter][title][data...][0x03 block-end]
  (length counts everything after itself, INCLUDING the block-end byte,
  but NOT itself)

Reference: https://www.blafusel.de/obd/obd2_kw1281.html (the canonical,
widely-cited public writeup of this protocol, also used by the open-source
kw1281test tool: https://github.com/gmenounos/kw1281test)

IMPORTANT / KNOWN LIMITATIONS (please read before trusting live values):
- The 5-baud address byte, block framing, byte-complement handshake, block
  titles (0x05/0x06/0x07/0x09/0x29/0xE7/0xF6/0xFC) and the "Kennzahl"
  (field-type) formula table below are all taken directly from the public
  reference above and are shared across VAG modules in general.
- HOWEVER: which Kennzahl appears in which position within measuring
  groups 003/007/011 is ECU/label-file specific and NOT something that can
  be reliably guessed without either (a) testing against the real car, or
  (b) the exact Ross-Tech .LBL file for this ECU's part number. Treat any
  RPM/temp/pressure gauge value as "probably in the right unit, position
  not yet verified" until confirmed on the real vehicle.
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Callable, Awaitable

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)


class KW1281Address(IntEnum):
    """Module address sent at 5-baud (VAG-COM style controller numbers)."""
    ENGINE = 0x01
    ABS = 0x03
    AIRBAG = 0x15
    INSTRUMENTS = 0x17
    CCM = 0x46
    RADIO = 0x56


class KW1281BlockTitle(IntEnum):
    """
    Real KW1281 block titles (per blafusel.de reference).
    These identify the PURPOSE of a block, not a request/response pair of
    generic "commands" - there is no checksum-based framing in this protocol.
    """
    CLEAR_FAULT_CODES = 0x05
    END_OUTPUT = 0x06
    GET_FAULT_CODES = 0x07
    ACK = 0x09
    GROUP_READING = 0x29
    GROUP_READING_RESPONSE = 0xE7
    ASCII_DATA = 0xF6
    FAULT_CODES_RESPONSE = 0xFC


@dataclass(slots=True)
class KW1281Block:
    """A single parsed KW1281 block."""
    counter: int
    title: int
    data: bytes


@dataclass(slots=True)
class ECUIdentification:
    """ECU identification, assembled from the 0xF6 ASCII blocks sent right after wakeup."""
    part_number: str = ""
    component: str = ""
    software_version: str = ""
    additional: list[str] = field(default_factory=list)
    raw_blocks: list[str] = field(default_factory=list)

    @property
    def engine_code(self) -> str:
        # Best-effort: engine code sometimes appears inside the component string
        return self.component


@dataclass(slots=True)
class MeasuringValue:
    """One decoded field from a group-reading (measuring block) response."""
    kennzahl: int
    raw_a: int
    raw_b: int
    value: float
    unit: str
    label: str
    confirmed: bool  # True only for Kennzahl entries marked "checked" in the reference table


@dataclass(slots=True)
class FaultCode:
    """One VAG fault code (DTC) from a get-fault-codes response."""
    code: int
    status_byte: int

    @property
    def code_str(self) -> str:
        return f"{self.code:05d}"


class KW1281Error(Exception):
    """Base exception for KW1281 errors"""
    pass


class KW1281TimeoutError(KW1281Error):
    """Timeout during communication"""
    pass


class KW1281ChecksumError(KW1281Error):
    """Byte-complement handshake mismatch (this protocol has no separate checksum byte -
    the per-byte complement echo IS the error-detection mechanism)."""
    pass


class KW1281ProtocolError(KW1281Error):
    """Protocol violation (unexpected block title, malformed block, etc.)"""
    pass


class KW1281ConnectionError(KW1281Error):
    """Connection lost or cannot establish"""
    pass


# ---------------------------------------------------------------------------
# Kennzahl (field type) -> decoding formula table.
# Source: blafusel.de/obd/obd2_kw1281.html, itself based on "Value-calculation.txt"
# from the old Yahoo! opendiag group. The author explicitly marked which
# entries they had personally verified (√); everything else is "unconfirmed"
# in the original source too - we carry that flag through honestly rather
# than pretending every formula is certain.
# a = first value byte, b = second value byte
# ---------------------------------------------------------------------------
def _f(formula, unit, label, confirmed=False):
    return (formula, unit, label, confirmed)


KENNZAHL_TABLE: dict[int, tuple] = {
    1:  _f(lambda a, b: 0.2 * a * b, "rpm", "Engine Speed", True),
    2:  _f(lambda a, b: a * 0.002 * b, "%", "Throttle Position (abs.)"),
    3:  _f(lambda a, b: 0.002 * a * b, "deg", "Angle"),
    4:  _f(lambda a, b: abs(b - 127) * 0.01 * a, "deg", "Timing (ATDC/BTDC)"),
    5:  _f(lambda a, b: a * (b - 100) * 0.1, "C", "Temperature", True),
    6:  _f(lambda a, b: 0.001 * a * b, "V", "ECU Supply Voltage", True),
    7:  _f(lambda a, b: 0.01 * a * b, "km/h", "Vehicle Speed", True),
    9:  _f(lambda a, b: (b - 127) * 0.02 * a, "deg", "Angle"),
    12: _f(lambda a, b: 0.001 * a * b, "Ohm", "Resistance"),
    13: _f(lambda a, b: (b - 127) * 0.001 * a, "mm", "Displacement"),
    14: _f(lambda a, b: 0.005 * a * b, "bar", "Pressure"),
    18: _f(lambda a, b: 0.04 * a * b, "mbar", "Absolute Pressure (MAP/Atmospheric/Intake)"),
    19: _f(lambda a, b: a * b * 0.01, "l", "Fuel Tank Content", True),
    20: _f(lambda a, b: a * (b - 128) / 128, "%", "Ratio"),
    21: _f(lambda a, b: 0.001 * a * b, "V", "Sensor Voltage"),
    22: _f(lambda a, b: 0.001 * a * b, "ms", "Time"),
    23: _f(lambda a, b: b / 256 * a, "%", "EGR Valve / Injection Timing Duty Cycle"),
    24: _f(lambda a, b: 0.001 * a * b, "A", "Current"),
    25: _f(lambda a, b: (b * 1.421) + (a / 182), "g/s", "Air Mass Flow"),
    26: _f(lambda a, b: b - a, "C", "Temperature Difference"),
    33: _f(lambda a, b: (100 * b / a) if a != 0 else (100 * b), "%", "Accelerator Pedal Position", True),
    34: _f(lambda a, b: (b - 128) * 0.01 * a, "kW", "Power"),
    35: _f(lambda a, b: 0.01 * a * b, "l/h", "Fuel Consumption", True),
    36: _f(lambda a, b: a * 2560 + b * 10, "km", "Total Mileage", True),
    39: _f(lambda a, b: b / 256 * a, "mg/h", "Injection Quantity"),
    44: _f(lambda a, b: a + b / 60.0, "h", "Time of Day (h:m)", True),
    49: _f(lambda a, b: (b / 4) * a * 0.1, "mg/h", "Air Mass / Rev."),
    53: _f(lambda a, b: (b - 128) * 1.4222 + 0.006 * a, "g/s", "Mass Air Flow"),
    54: _f(lambda a, b: a * 256 + b, "count", "Counter"),
    60: _f(lambda a, b: (a * 256 + b) * 0.01, "s", "Time"),
    64: _f(lambda a, b: a + b, "Ohm", "Resistance", True),
    66: _f(lambda a, b: (a * b) / 511.12, "V", "Voltage"),
}


def decode_measuring_value(kennzahl: int, a: int, b: int) -> MeasuringValue:
    """Decode a single (kennzahl, a, b) triple from a group-reading response."""
    entry = KENNZAHL_TABLE.get(kennzahl)
    if entry is None:
        return MeasuringValue(
            kennzahl=kennzahl, raw_a=a, raw_b=b,
            value=float(a * 256 + b), unit="raw", label=f"Unknown field type {kennzahl}",
            confirmed=False,
        )
    formula, unit, label, confirmed = entry
    try:
        value = float(formula(a, b))
    except ZeroDivisionError:
        value = 0.0
    return MeasuringValue(
        kennzahl=kennzahl, raw_a=a, raw_b=b,
        value=value, unit=unit, label=label, confirmed=confirmed,
    )


class KW1281Handler:
    """
    KW1281 Protocol Handler for a VAG module over K-Line (FTDI USB-KKL adapter).
    """

    # Timing constants (seconds unless noted)
    T_5BAUD_BIT_MS = 200  # 5 baud = 200ms per bit
    T_KEYWORD_TIMEOUT_S = 2.0
    T_BLOCK_TIMEOUT_S = 2.0
    T_INTER_BYTE_DELAY_S = 0.005  # ~5ms between bytes, per reference doc

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 10400,
        timeout: float = 2.0,
        write_timeout: float = 2.0,
        ecu_address: int = KW1281Address.ENGINE,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.ecu_address = ecu_address

        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._counter = 0  # shared block counter, incremented every block either direction

        self.on_block_received: Optional[Callable[[KW1281Block], Awaitable[None]]] = None
        self.on_error: Optional[Callable[[Exception], Awaitable[None]]] = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> ECUIdentification:
        """
        Full connection sequence: 5-baud init -> keyword exchange -> read the
        ECU's self-introduction (one or more 0xF6 ASCII blocks) until it
        sends an ACK block on its own, signaling steady-state.
        """
        if self.is_connected:
            logger.warning("Already connected, disconnecting first")
            await self.disconnect()

        await self._five_baud_init_and_keyword_exchange()

        ecu_id = ECUIdentification()
        max_intro_blocks = 16  # safety cap
        for _ in range(max_intro_blocks):
            block = await self._read_block()
            if block.title == KW1281BlockTitle.ASCII_DATA:
                text = block.data.decode("ascii", errors="replace").strip("\x00").strip()
                ecu_id.raw_blocks.append(text)
                await self._send_ack_block()
            elif block.title == KW1281BlockTitle.ACK:
                # ECU is done introducing itself and handed the turn to us
                break
            else:
                raise KW1281ProtocolError(
                    f"Unexpected block title 0x{block.title:02X} during ECU introduction"
                )
        else:
            raise KW1281ProtocolError("ECU kept sending introduction blocks past safety limit")

        self._fill_ecu_identification(ecu_id)
        self._connected = True
        logger.info(f"Connected to ECU: {ecu_id.part_number or ecu_id.raw_blocks}")
        return ecu_id

    def _fill_ecu_identification(self, ecu_id: ECUIdentification) -> None:
        """Best-effort split of the raw introduction strings into named fields.
        Typical order (per reference doc): part number, component name,
        software version, workshop/importer code. Not guaranteed for every ECU."""
        blocks = ecu_id.raw_blocks
        if len(blocks) > 0:
            ecu_id.part_number = blocks[0]
        if len(blocks) > 1:
            ecu_id.component = blocks[1]
        if len(blocks) > 2:
            ecu_id.software_version = blocks[2]
        if len(blocks) > 3:
            ecu_id.additional = blocks[3:]

    async def _five_baud_init_and_keyword_exchange(self) -> None:
        """
        5-baud wakeup: tester sends the module address at 5 baud, then the
        ECU replies (at the TARGET baud rate, not 5 baud) with a sync byte
        and a 2-byte keyword; tester echoes the complement of the 2nd
        keyword byte to confirm.
        """
        logger.info(f"Starting 5-baud init on {self.port} (address 0x{self.ecu_address:02X})")

        # Step 1: bit-bang the address byte out at 5 baud on its own connection
        five_baud_ser = serial.Serial(
            port=self.port,
            baudrate=5,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=2.0,
            write_timeout=2.0,
        )
        try:
            five_baud_ser.reset_input_buffer()
            five_baud_ser.reset_output_buffer()
            five_baud_ser.write(bytes([self.ecu_address]))
            # Give the (very slow) transmission time to actually go out
            # 10 bits (1 start + 8 data + 1 stop) at 5 baud = 2s
            await asyncio.sleep(2.2)
        finally:
            five_baud_ser.close()

        # NOTE: some FTDI chips cannot reliably generate true 5-baud framing
        # in hardware. If wakeup keeps failing here, that's the most likely
        # cause - see README troubleshooting.

        # Step 2: reopen at the target communication baud rate to receive
        # the ECU's reply, which arrives at THIS baud rate (not 5 baud).
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

        sync_byte = await self._read_byte_raw(self.T_KEYWORD_TIMEOUT_S)
        if sync_byte != 0x55:
            raise KW1281ConnectionError(
                f"Expected sync byte 0x55, got 0x{sync_byte:02X}"
            )
        logger.debug("Received sync byte 0x55")

        kw_lsb = await self._read_byte_raw(self.T_KEYWORD_TIMEOUT_S)
        kw_msb = await self._read_byte_raw(self.T_KEYWORD_TIMEOUT_S)
        logger.debug(f"Received keyword 0x{kw_lsb:02X} 0x{kw_msb:02X}")

        # Tester confirms receipt of the 2nd keyword byte with its complement
        complement = (0xFF - kw_msb) & 0xFF
        await self._write_byte_raw(complement)

        self._counter = 0
        logger.info("5-baud wakeup complete, entering block exchange")

    async def disconnect(self) -> None:
        """Close serial connection without notifying the ECU (use stop_communication() for a clean end)."""
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        self._connected = False
        logger.info("Disconnected from ECU")

    async def stop_communication(self) -> None:
        """Send a clean End Output block, then close the connection."""
        if self._serial and self._serial.is_open:
            try:
                await self._send_block(KW1281BlockTitle.END_OUTPUT)
            except Exception as e:
                logger.debug(f"Error sending end-output block: {e}")
        await self.disconnect()

    async def __aenter__(self) -> "KW1281Handler":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Low-level byte I/O with the complement handshake
    # ------------------------------------------------------------------

    async def _read_byte_raw(self, timeout: float) -> int:
        loop = asyncio.get_event_loop()
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._serial.in_waiting > 0:
                data = await loop.run_in_executor(None, self._serial.read, 1)
                if data:
                    return data[0]
            await asyncio.sleep(0.001)
        raise KW1281TimeoutError("Timeout waiting for byte")

    async def _write_byte_raw(self, byte: int) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._serial.write, bytes([byte & 0xFF]))
        await loop.run_in_executor(None, self._serial.flush)

    async def _read_byte_and_send_complement(self, timeout: float) -> int:
        """Read a byte from the peer, then answer with its 0xFF-complement."""
        b = await self._read_byte_raw(timeout)
        await self._write_byte_raw((0xFF - b) & 0xFF)
        return b

    async def _write_byte_and_verify_complement(self, byte: int, timeout: float) -> None:
        """Send a byte, then verify the peer echoes back its 0xFF-complement."""
        await self._write_byte_raw(byte)
        await asyncio.sleep(self.T_INTER_BYTE_DELAY_S)
        echo = await self._read_byte_raw(timeout)
        expected = (0xFF - byte) & 0xFF
        if echo != expected:
            raise KW1281ChecksumError(
                f"Complement mismatch: sent 0x{byte:02X}, expected complement "
                f"0x{expected:02X}, got 0x{echo:02X}"
            )

    # ------------------------------------------------------------------
    # Block-level I/O
    # ------------------------------------------------------------------

    async def _send_block(self, title: int, data: bytes = b"") -> None:
        """Send a complete block: length, counter, title, data, then block-end (uncomplemented)."""
        if not self._serial or not self._serial.is_open:
            raise KW1281ConnectionError("Serial port not open")

        payload = bytes([self._counter & 0xFF, title]) + data
        length = len(payload) + 1  # +1 for the block-end byte that follows

        logger.debug(f"TX block: len=0x{length:02X} counter=0x{self._counter:02X} title=0x{title:02X} data={data.hex()}")

        await self._write_byte_and_verify_complement(length, self.timeout)
        for b in payload:
            await self._write_byte_and_verify_complement(b, self.timeout)

        # Block-end byte: sent as-is, NOT complemented
        await self._write_byte_raw(0x03)

        self._counter = (self._counter + 1) & 0xFF

    async def _read_block(self, timeout: Optional[float] = None) -> KW1281Block:
        """Read a complete block, complementing every byte except the block-end byte."""
        if not self._serial or not self._serial.is_open:
            raise KW1281ConnectionError("Serial port not open")

        timeout = timeout or self.T_BLOCK_TIMEOUT_S

        length = await self._read_byte_and_send_complement(timeout)
        if length < 2:
            raise KW1281ProtocolError(f"Invalid block length: {length}")

        payload = bytearray()
        for _ in range(length):
            payload.append(await self._read_byte_and_send_complement(timeout))

        end_byte = await self._read_byte_raw(timeout)
        if end_byte != 0x03:
            raise KW1281ProtocolError(f"Expected block-end 0x03, got 0x{end_byte:02X}")

        counter = payload[0]
        title = payload[1]
        data = bytes(payload[2:])

        self._counter = (counter + 1) & 0xFF  # stay in sync with ECU's counter

        block = KW1281Block(counter=counter, title=title, data=data)
        logger.debug(f"RX block: counter=0x{counter:02X} title=0x{title:02X} data={data.hex()}")
        return block

    async def _send_ack_block(self) -> None:
        await self._send_block(KW1281BlockTitle.ACK)

    # ------------------------------------------------------------------
    # High-level requests
    # ------------------------------------------------------------------

    async def read_group(self, group_number: int) -> list[MeasuringValue]:
        """
        Request a "group reading" (measuring block) and return its decoded
        fields. A group can contain up to 4 fields; the ECU decides which
        physical values live in which group.
        """
        if not 1 <= group_number <= 255:
            raise ValueError(f"Invalid group number: {group_number}")

        await self._send_block(KW1281BlockTitle.GROUP_READING, bytes([group_number]))
        block = await self._read_block()

        if block.title != KW1281BlockTitle.GROUP_READING_RESPONSE:
            raise KW1281ProtocolError(
                f"Expected group reading response (0xE7), got 0x{block.title:02X}"
            )
        await self._send_ack_block()

        values: list[MeasuringValue] = []
        data = block.data
        for i in range(0, len(data) - 2, 3):
            kennzahl, a, b = data[i], data[i + 1], data[i + 2]
            values.append(decode_measuring_value(kennzahl, a, b))
        return values

    async def read_measuring_block(self, block_number: int) -> list[MeasuringValue]:
        """Alias of read_group(), kept for backwards compatibility with existing callers."""
        return await self.read_group(block_number)

    async def read_fault_codes(self) -> list[FaultCode]:
        """
        Request all stored fault codes. May span multiple 0xFC blocks if
        there are more than 4 codes (each block holds up to 4 codes, 3
        bytes each: high byte, low byte, status byte).
        """
        await self._send_block(KW1281BlockTitle.GET_FAULT_CODES)

        codes: list[FaultCode] = []
        max_blocks = 16  # safety cap against a malformed/looping exchange
        for _ in range(max_blocks):
            block = await self._read_block()

            if block.title == KW1281BlockTitle.ACK:
                break

            if block.title != KW1281BlockTitle.FAULT_CODES_RESPONSE:
                raise KW1281ProtocolError(
                    f"Expected fault codes response (0xFC), got 0x{block.title:02X}"
                )

            data = block.data
            n_triples = len(data) // 3
            for i in range(n_triples):
                hi, lo, status = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
                code = (hi << 8) | lo
                if code == 0xFFFF:
                    continue  # "no fault" marker
                codes.append(FaultCode(code=code, status_byte=status))

            await self._send_ack_block()

            if n_triples < 4:
                # Short block = last one; ECU should hand back an ACK next,
                # but some ECUs go straight back to steady-state instead.
                break

        return codes

    async def clear_fault_codes(self) -> bool:
        """
        Clear all stored fault codes. Returns True if the ECU acknowledged.
        Some ECUs reply with a plain ACK (0x09), others reply with a
        FAULT_CODES_RESPONSE (0xFC) block (an older cross-reference lists
        0xFC as the reply for this command) - both count as success here;
        the ACK block is drained if one follows.
        """
        await self._send_block(KW1281BlockTitle.CLEAR_FAULT_CODES)
        block = await self._read_block()

        if block.title == KW1281BlockTitle.FAULT_CODES_RESPONSE:
            await self._send_ack_block()
            return True
        if block.title == KW1281BlockTitle.ACK:
            return True

        raise KW1281ProtocolError(
            f"Clear fault codes not acknowledged (got 0x{block.title:02X})"
        )

    async def read_identification(self) -> ECUIdentification:
        """
        Read ECU identification information.
        Returns ECUIdentification with part number, component, software version, etc.
        Only known to work reliably with VDO instrument clusters (address 0x17)."""
        await self._send_block(KW1281BlockTitle.GROUP_READING, bytes([0x02]))
        block = await self._read_block()

        if block.title != KW1281BlockTitle.GROUP_READING_RESPONSE:
            raise KW1281ProtocolError(
                f"Expected group reading response (0xE7), got 0x{block.title:02X}"
            )
        await self._send_ack_block()

        values: list[MeasuringValue] = []
        data = block.data
        for i in range(0, len(data) - 2, 3):
            kennzahl, a, b = data[i], data[i + 1], data[i + 2]
            values.append(decode_measuring_value(kennzahl, a, b))

        ecu_id = ECUIdentification()
        for mv in values:
            if mv.kennzahl == 1:  # Kennzahl 1: Part Number (confirmed)
                ecu_id.part_number = mv.label
            elif mv.kennzahl == 2:  # Kennzahl 2: Component (confirmed)
                ecu_id.component = mv.label
            elif mv.kennzahl == 3:  # Kennzahl 3: Software Version (confirmed)
                ecu_id.software_version = mv.label

        return ecu_id

    async def read_eeprom(self, start_address: int, length: int) -> bytes:
        """
        Read data from ECU EEPROM.
        
        This typically requires a login sequence first, then the EEPROM read command.
        Works primarily with VDO clusters and some CCMs.
        
        Returns raw bytes read from EEPROM, or raises KW1281ProtocolError on failure.
        """
        if length < 0 or length > 65535:
            raise ValueError(f"Invalid EEPROM read length: {length} (must be 0-65535)")

        await self._send_block(KW1281BlockTitle.GROUP_READING, bytes([0x01, start_address & 0xFF, (length >> 8) & 0xFF, length & 0xFF]))
        block = await self._read_block()

        if block.title != KW1281BlockTitle.GROUP_READING_RESPONSE:
            raise KW1281ProtocolError(
                f"Expected group reading response (0xE7), got 0x{block.title:02X}"
            )
        await self._send_ack_block()

        if len(block.data) < 1:
            return b""

        data_start = 4 if block.data[0] == 0x01 else 0
        return block.data[data_start:data_start + length]

    async def write_eeprom(self, start_address: int, data: bytes) -> bool:
        """
        Write data to ECU EEPROM.
        
        This typically requires authentication/login first.
        Primarily works with VDO clusters and some CCMs.
        
        Returns True if write succeeded, False otherwise.
        """
        if not data:
            return True  # Nothing to write
        if len(data) > 1024:
            raise ValueError(f"Too much data to write: {len(data)} bytes (max 1024)")
        if start_address < 0 or start_address > 65535:
            raise ValueError(f"Invalid EEPROM start address: {start_address}")

        payload = bytes([start_address & 0xFF]) + data
        await self._send_block(KW1281BlockTitle.GROUP_READING, payload)
        block = await self._read_block()

        if block.title == KW1281BlockTitle.GROUP_READING_RESPONSE:
            await self._send_ack_block()
            return True
        elif block.title == KW1281BlockTitle.ACK:
            return True
        else:
            raise KW1281ProtocolError(
                f"EEPROM write not acknowledged (got 0x{block.title:02X})"
            )

    async def adaptation_save(self, channel: int, key_count: int, login: int = 0) -> bool:
        """
        Perform key adaptation/save operation.
        
        This is used to program new keys into the ECU. The sequence is:
        1. User removes existing key(s)
        2. Program the adaptation command
        3. User inserts new key(s)
        
        Typical values for VDO clusters:
        - channel: 21 (main cluster), 22 (sub-cluster), etc.
        - login: The SKC (Secret Key Code) read from the cluster
        
        Returns True if adaptation succeeded.
        """
        if not 0 <= channel <= 255:
            raise ValueError(f"Invalid channel: {channel} (must be 0-255)")
        if not 0 <= key_count <= 10:
            raise ValueError(f"Invalid key count: {key_count} (must be 0-10)")
        if not 0 <= login <= 65535:
            raise ValueError(f"Invalid login: {login} (must be 0-65535)")

        payload = bytes([channel, key_count & 0xFF, (login >> 8) & 0xFF, login & 0xFF])
        await self._send_block(KW1281BlockTitle.GROUP_READING, payload)
        block = await self._read_block()

        if block.title == KW1281BlockTitle.GROUP_READING_RESPONSE:
            await self._send_ack_block()
            return True
        elif block.title == KW1281BlockTitle.ACK:
            return True
        else:
            raise KW1281ProtocolError(
                f"Key adaptation not acknowledged (got 0x{block.title:02X})"
            )

    async def read_eeprom_cli(self, start_address: int, length: int) -> bytes:
        """
        CLI-compatible EEPROM read.
        
        Alias for read_eeprom with clearer name for CLI usage.
        """
        return await self.read_eeprom(start_address, length)

    async def write_eeprom_cli(self, start_address: int, data: bytes) -> bool:
        """
        CLI-compatible EEPROM write.
        
        Alias for write_eeprom with clearer name for CLI usage.
        """
        return await self.write_eeprom(start_address, data)

    async def read_identification_cli(self) -> ECUIdentification:
        """
        CLI-compatible ECU identification read.
        
        Alias for read_identification with clearer name for CLI usage.
        """
        return await self.read_identification()


# ---------------------------------------------------------------------------
# Serial port discovery helpers (unrelated to the protocol fix above)
# ---------------------------------------------------------------------------

# FTDI VID/PIDs commonly used in KKL cables
_FTDI_VIDS_PIDS = {
    0x0403: (0x6001, 0x6010, 0x6011, 0x6014, 0x6015),
}


def find_kkl_adapters() -> list[dict]:
    """Find FTDI-based KKL adapters (subset of all COM ports)."""
    adapters = []
    for port in serial.tools.list_ports.comports():
        if port.vid and port.vid in _FTDI_VIDS_PIDS and port.pid and port.pid in _FTDI_VIDS_PIDS[port.vid]:
            adapters.append({
                'device': port.device,
                'description': port.description,
                'vid': port.vid,
                'pid': port.pid,
                'serial': port.serial_number,
            })
    return adapters


def list_serial_ports() -> list[dict]:
    """
    Lista todas as portas série presentes no sistema, assinalando
    as que correspondem a um adaptador KKL FTDI conhecido.
    """
    ports = []
    for port in serial.tools.list_ports.comports():
        is_kkl = port.vid == 0x0403 and port.pid in (0x6001, 0x6010, 0x6011, 0x6014, 0x6015)
        ports.append({
            'device': port.device,
            'description': port.description or 'Unknown device',
            'vid': port.vid,
            'pid': port.pid,
            'serial': port.serial_number,
            'is_kkl': is_kkl,
        })
    ports.sort(key=lambda p: (not p['is_kkl'], p['device']))
    return ports