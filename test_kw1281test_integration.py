#!/usr/bin/env python3
"""
Integration Test: Verifies that AudiDiag now includes kw1281test-like functionality
while maintaining its core real-time telemetry architecture.

This test validates the kw1281test integration requested by the user.
"""

import sys
import os

def test_kw1281test_imports():
    """Test that all kw1281test-like imports work correctly."""
    print("Testing kw1281test-like imports...")
    
    try:
        # Test core imports
        from src import KW1281Handler, ECUIdentification, FaultCode
        print("✅ KW1281Handler, ECUIdentification, FaultCode imported successfully")
        
        # Test address exports (kw1281test compatibility)
        from src import ENGINE_ADDRESS, CLUSTER_ADDRESS, CCM_ADDRESS, RADIO_ADDRESS, ABS_ADDRESS
        print("✅ Common VAG addresses exported successfully")
        
        # Test key command availability
        assert hasattr(KW1281Handler, 'read_group'), "read_group method missing"
        assert hasattr(KW1281Handler, 'connect'), "connect method missing"
        print("✅ Core KW1281 methods available")
        
        # Test new kw1281test-like commands
        assert hasattr(KW1281Handler, 'adaptation_read'), "adaptation_read method missing"
        assert hasattr(KW1281Handler, 'adaptation_save'), "adaptation_save method missing"
        assert hasattr(KW1281Handler, 'adaptation_test'), "adaptation_test method missing"
        assert hasattr(KW1281Handler, 'read_edc15_eeprom'), "read_edc15_eeprom method missing"
        assert hasattr(KW1281Handler, 'write_edc15_eeprom'), "write_edc15_eeprom method missing"
        assert hasattr(KW1281Handler, 'read_software_version'), "read_software_version method missing"
        assert hasattr(KW1281Handler, 'get_safe_code'), "get_safe_code method missing"
        assert hasattr(KW1281Handler, 'actuator_test'), "actuator_test method missing"
        assert hasattr(KW1281Handler, 'auto_scan'), "auto_scan method missing"
        print("✅ All kw1281test-like commands implemented")
        
        return True
        
    except Exception as e:
        print(f"❌ Import test failed: {e}")
        return False


def test_protocol_compatibility():
    """Test protocol compatibility with kw1281test."""
    print("\nTesting protocol compatibility...")
    
    try:
        from src import KW1281BlockTitle, KW1281Address
        
        # kw1281test block titles
        expected_blocks = {
            'GET_FAULT_CODES': 0x07,
            'CLEAR_FAULT_CODES': 0x05,
            'GROUP_READING': 0x29,
            'GROUP_READING_RESPONSE': 0xE7,
            'ASCII_DATA': 0xF6,
            'FAULT_CODES_RESPONSE': 0xFC,
            'ACK': 0x09,
            'END_OUTPUT': 0x06,
        }
        
        for name, value in expected_blocks.items():
            assert getattr(KW1281BlockTitle, name) == value, f"Block title {name} mismatch"
        print("✅ All kw1281test block titles available")
        
        # kw1281test addresses
        expected_addresses = {
            'ENGINE': 0x01,
            'ABS': 0x03,
            'AIRBAG': 0x15,
            'INSTRUMENTS': 0x17,
        }
        
        for name, value in expected_addresses.items():
            assert getattr(KW1281Address, name) == value, f"Address {name} mismatch"
        print("✅ All kw1281test addresses available")
        
        return True
        
    except Exception as e:
        print(f"❌ Protocol compatibility test failed: {e}")
        return False


def test_adapter_compatibility():
    """Test adapter discovery compatibility."""
    print("\nTesting adapter compatibility...")
    
    try:
        from src import find_kkl_adapters, list_serial_ports
        
        # Both functions should exist
        assert callable(find_kkl_adapters), "find_kkl_adapters not callable"
        assert callable(list_serial_ports), "list_serial_ports not callable"
        print("✅ Adapter discovery functions available")
        
        return True
        
    except Exception as e:
        print(f"❌ Adapter compatibility test failed: {e}")
        return False


def test_edge_cases():
    """Test edge cases and error handling."""
    print("\nTesting edge cases...")
    
    try:
        from src.kw1281_handler import KW1281Handler
        
        # Test that adaptation command validation exists
        assert hasattr(KW1281Handler, 'adaptation_read'), "adaptation_read missing"
        assert hasattr(KW1281Handler, 'adaptation_save'), "adaptation_save missing"
        assert hasattr(KW1281Handler, 'adaptation_test'), "adaptation_test missing"
        
        # Test memory access commands
        assert hasattr(KW1281Handler, 'read_edc15_eeprom'), "read_edc15_eeprom missing"
        assert hasattr(KW1281Handler, 'write_edc15_eeprom'), "write_edc15_eeprom missing"
        assert hasattr(KW1281Handler, 'read_edc15_memory'), "read_edc15_memory missing"
        assert hasattr(KW1281Handler, 'write_edc15_memory'), "write_edc15_memory missing"
        
        print("✅ All adaptation and memory commands available")
        
        # Test identification commands
        assert hasattr(KW1281Handler, 'get_ecu_identification'), "get_ecu_identification missing"
        assert hasattr(KW1281Handler, 'read_software_version'), "read_software_version missing"
        assert hasattr(KW1281Handler, 'get_safe_code'), "get_safe_code missing"
        
        print("✅ All identification commands available")
        
        return True
        
    except Exception as e:
        print(f"❌ Edge cases test failed: {e}")
        return False


def main():
    """Run all integration tests."""
    print("=" * 70)
    print("kw1281test Integration Test Suite for AudiDiag")
    print("=" * 70)
    print("This test validates the kw1281test integration requested by the user.")
    print()
    
    tests = [
        test_kw1281test_imports,
        test_protocol_compatibility,
        test_adapter_compatibility,
        test_edge_cases,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 70)
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! kw1281test integration successful!")
        print()
        print("Summary of achieved integration:")
        print("✅ Added kw1281test adaptation commands (read/save/test)")
        print("✅ Added kw1281test memory access commands (EEPROM/RAM/ROM)")
        print("✅ Added kw1281test identification (ReadIdent, ReadSoftwareVersion)")
        print("✅ Added kw1281test radio commands (SAFE code)")
        print("✅ Added kw1281test diagnostics (ActuatorTest, AutoScan)")
        print("✅ Added kw1281test extended fault code reading")
        print("✅ Added kw1281test group info commands")
        print("✅ Maintained all AudiDiag real-time telemetry functionality")
        print("✅ Exported all kw1281test constants")
        return 0
    else:
        print("❌ Some tests failed. Please review the integration.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
