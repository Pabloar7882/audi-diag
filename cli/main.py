# CLI Interface for Audi A4 B5 Diagnostics
# Command-line tool matching kw1281test functionality

import argparse
import logging
import sys
import asyncio
from typing import Optional

from src.kw1281_handler import KW1281Handler

# Setup logging similar to kw1281test
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def cmd_read_ident(port: str, baudrate: int, ecu_address: int):
    """kw1281test: kw1281test.exe COM3 10400 17 ReadIdent"""
    handler = KW1281Handler(port=port, baudrate=baudrate, ecu_address=ecu_address)
    
    try:
        await handler.connect()
        ecu_id = await handler.read_identification()
        
        print(f"ECU: {ecu_id.part_number or '—'} {ecu_id.component or '—'} SW:{ecu_id.software_version or '—'}")
        print(f"Engine: {ecu_id.engine_code or 'AFN'}")
        print("Component: Best-effort split of part number, component name, software version")
        return ecu_id
    finally:
        await handler.disconnect()

async def cmd_read_eeprom(port: str, baudrate: int, start_addr: int, length: int, ecu_address: int):
    """kw1281test: kw1281test.exe COM3 10400 17 ReadEeprom 1109"""
    handler = KW1281Handler(port=port, baudrate=baudrate, ecu_address=ecu_address)
    
    try:
        await handler.connect()
        data = await handler.read_eeprom(start_addr, length)
        
        print(f"Read EEPROM: {len(data)} bytes from 0x{start_addr:04X}")
        print(f"Raw hex: {' '.join(f'{b:02X}' for b in data)}")
        return data
    finally:
        await handler.disconnect()

async def cmd_write_eeprom(port: str, baudrate: int, start_addr: int, file_path: str, ecu_address: int):
    """kw1281test: kw1281test.exe COM3 10400 17 WriteEeprom 1109 <file>"""
    with open(file_path, 'rb') as f:
        data = f.read()
    
    handler = KW1281Handler(port=port, baudrate=baudrate, ecu_address=ecu_address)
    
    try:
        await handler.connect()
        success = await handler.write_eeprom(start_addr, data)
        
        print(f"Write EEPROM: {'OK' if success else 'FAILED'} at 0x{start_addr:04X}")
        print(f"Written: {len(data)} bytes")
        return success
    finally:
        await handler.disconnect()

async def cmd_adaptation_save(port: str, baudrate: int, channel: int, key_count: int, login: int, ecu_address: int):
    """kw1281test: kw1281test.exe COM3 10400 17 AdaptationSave 21 2 01111"""
    handler = KW1281Handler(port=port, baudrate=baudrate, ecu_address=ecu_address)
    
    try:
        await handler.connect()
        success = await handler.adaptation_save(channel, key_count, login)
        
        print(f"Key adaptation: {'OK' if success else 'FAILED'} channel {channel}, keys {key_count}")
        print("⚠️  Important: Remove old key(s) first, insert new key now")
        return success
    finally:
        await handler.disconnect()

async def cmd_read_fault_codes(port: str, baudrate: int, ecu_address: int):
    """kw1281test: kw1281test.exe COM3 10400 17 ReadFaultCodes"""
    handler = KW1281Handler(port=port, baudrate=baudrate, ecu_address=ecu_address)
    
    try:
        await handler.connect()
        codes = await handler.read_fault_codes()
        
        print(f"Fault codes: {len(codes)} found")
        for fc in codes:
            print(f"  DTC: {fc.code_str} Status: 0x{fc.status_byte:02X}")
        return codes
    finally:
        await handler.disconnect()

def main():
    parser = argparse.ArgumentParser(description="Audi A4 B5 Diagnostics CLI")
    parser.add_argument('--port', required=True, help='Serial port (COM1, /dev/ttyUSB0, etc.)')
    parser.add_argument('--baud', type=int, default=10400, help='Baud rate (default: 10400)')
    parser.add_argument('--ecu-address', type=int, default=0x17, help='Controller address (default: 0x17 for cluster)')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Read identification
    subparsers.add_parser('read_ident', help='Read ECU identification')
    
    # Read EEPROM
    ep_read = subparsers.add_parser('read_eeprom', help='Read EEPROM data')
    ep_read.add_argument('--addr', required=True, type=lambda x: int(x, 0), help='Start address (hex/dec)')
    ep_read.add_argument('--len', type=int, default=128, help='Number of bytes to read (default: 128)')
    
    # Write EEPROM
    ep_write = subparsers.add_parser('write_eeprom', help='Write EEPROM data')
    ep_write.add_argument('--addr', required=True, type=lambda x: int(x, 0), help='Start address (hex/dec)')
    ep_write.add_argument('--file', required=True, help='File to write to EEPROM')
    
    # Key adaptation
    ep_adapt = subparsers.add_parser('adaptation_save', help='Program new keys')
    ep_adapt.add_argument('--channel', type=int, default=21, help='Cluster channel (default: 21)')
    ep_adapt.add_argument('--keys', type=int, default=1, help='Number of keys to program (default: 1)')
    ep_adapt.add_argument('--login', type=lambda x: int(x, 0), default=0x1111, help='SKC/Key code (default: 0x1111)')
    
    # Read fault codes
    subparsers.add_parser('read_fault_codes', help='Read stored fault codes')
    
    # Parse arguments
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Run async command
    try:
        asyncio.run(main_command(args))
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

async def main_command(args):
    if args.command == 'read_ident':
        await cmd_read_ident(args.port, args.baud, args.ecu_address)
    elif args.command == 'read_eeprom':
        await cmd_read_eeprom(args.port, args.baud, args.addr, args.len, args.ecu_address)
    elif args.command == 'write_eeprom':
        await cmd_write_eeprom(args.port, args.baud, args.addr, args.file, args.ecu_address)
    elif args.command == 'adaptation_save':
        await cmd_adaptation_save(args.port, args.baud, args.channel, args.keys, args.login, args.ecu_address)
    elif args.command == 'read_fault_codes':
        await cmd_read_fault_codes(args.port, args.baud, args.ecu_address)

if __name__ == '__main__':
    main()
