import json
import unittest

from diagnostic_models import DiagnosticError
from diagnostic_monitor import DiagnosticTrace, MonitoredTransport
from kline_transport import (
    PortInfo,
    ReplayTransport,
    SerialKLineTransport,
    build_kw1281_slow_init_bits,
    discover_ports,
)


class FakePort:
    device = "COM7"
    description = "FT232R USB UART"
    vid = 0x0403
    pid = 0x6001
    manufacturer = "FTDI"


class FakeSerial:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.timeout = None
        self.baudrate = None
        self.kwargs = None

    def write(self, data):
        self.writes.append(data)

    def read(self, size):
        return b"\x01"

    def close(self):
        self.closed = True


class BreakSerial(FakeSerial):
    def __init__(self):
        self.baudrate_assignments = []
        self.break_states = []
        self.events = []
        super().__init__()
        self._break_condition = False

    @property
    def baudrate(self):
        return getattr(self, "_baudrate", None)

    @baudrate.setter
    def baudrate(self, value):
        self.baudrate_assignments.append(value)
        self._baudrate = value

    @property
    def break_condition(self):
        return self._break_condition

    @break_condition.setter
    def break_condition(self, value):
        self._break_condition = bool(value)
        self.break_states.append(bool(value))
        self.events.append(("break", bool(value)))

    def reset_input_buffer(self):
        self.input_reset = True
        self.events.append(("reset_input",))


class FailingSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.timeout = 9.0

    def read(self, size):
        raise OSError("read failed")


class RoundingBaudSerial(FakeSerial):
    @property
    def baudrate(self):
        return getattr(self, "_baudrate", None)

    @baudrate.setter
    def baudrate(self, value):
        self._baudrate = 4 if value == 5 else value


class KlineTransportTest(unittest.TestCase):
    def test_replay_transport_returns_chunks_and_closes(self):
        transport = ReplayTransport([b"abc", b"def"])
        self.assertEqual(transport.read(10, 0.1), b"abc")
        self.assertEqual(transport.read(10, 0.1), b"def")
        with self.assertRaises(DiagnosticError) as raised:
            transport.read(10, 0.1)
        self.assertEqual(raised.exception.code, "timeout")
        transport.close()
        transport.close()

    def test_discovery_reports_ftdi_metadata(self):
        ports = discover_ports([FakePort()])
        self.assertEqual(ports, [
            PortInfo("COM7", "FT232R USB UART", 0x0403, 0x6001, "FTDI")
        ])

    def test_serial_adapter_bounds_reads_and_closes(self):
        serial = FakeSerial()
        transport = SerialKLineTransport(lambda **kwargs: serial)
        transport.open("COM7", baudrate=10400, timeout=0.2)
        transport.write(b"\x01")
        self.assertEqual(transport.read(1, 0.2), b"\x01")
        self.assertEqual(serial.writes, [b"\x01"])
        transport.close()
        self.assertTrue(serial.closed)

    def test_serial_timeout_is_restored_after_read_failure(self):
        serial = FailingSerial()
        transport = SerialKLineTransport(lambda **kwargs: serial)
        transport.open("COM7", baudrate=10400, timeout=0.2)
        with self.assertRaises(DiagnosticError):
            transport.read(1, 0.2)
        self.assertEqual(serial.timeout, 9.0)

    def test_kw1281_address_frame_is_7_data_bits_odd_parity_lsb_first(self):
        self.assertEqual(build_kw1281_slow_init_bits(0x01), (0, 1, 0, 0, 0, 0, 0, 0, 0, 1))
        self.assertEqual(build_kw1281_slow_init_bits(0x00), (0, 0, 0, 0, 0, 0, 0, 0, 1, 1))
        with self.assertRaises(DiagnosticError):
            build_kw1281_slow_init_bits(0x80)

    def test_serial_transport_generates_slow_init_with_break_and_keeps_normal_uart(self):
        serial = BreakSerial()
        sleeps = []
        options = []

        def serial_factory(**kwargs):
            options.append(kwargs)
            serial.baudrate = kwargs["baudrate"]
            return serial

        def sleep(duration):
            sleeps.append(duration)
            serial.events.append(("sleep", duration))

        transport = SerialKLineTransport(serial_factory, sleeper=sleep)
        transport.open("COM7", baudrate=9600, timeout=0.2)
        transport.init_5baud(0x01, 0.2)

        self.assertEqual(serial.writes, [])
        self.assertEqual(serial.baudrate, 9600)
        self.assertNotIn(5, serial.baudrate_assignments)
        self.assertEqual(
            serial.break_states,
            [False, True, False, True, True, True, True, True, True, True, False],
        )
        self.assertEqual(sleeps, [0.3] + [0.2] * 10)
        self.assertTrue(serial.input_reset)
        self.assertEqual(serial.events[:3], [("break", False), ("sleep", 0.3), ("break", True)])
        self.assertEqual(serial.events[-1], ("reset_input",))
        self.assertEqual(options[0]["bytesize"], 8)
        self.assertEqual(options[0]["parity"], "N")
        self.assertEqual(options[0]["stopbits"], 1)
        self.assertFalse(options[0]["rtscts"])
        self.assertFalse(options[0]["dsrdtr"])

    def test_monitored_transport_records_each_break_api_request_without_claiming_waveform(self):
        serial = BreakSerial()
        transport = SerialKLineTransport(lambda **kwargs: serial, sleeper=lambda _duration: None)
        trace = DiagnosticTrace()
        trace.begin_session("COM7", 10400)
        monitored = MonitoredTransport(transport, trace)
        transport.open("COM7", baudrate=10400, timeout=0.2)

        monitored.init_slow(0x01, 0.2)

        events = trace.snapshot()
        break_events = [event for event in events if event["direction"] == "control" and event["phase"].startswith("break_")]
        self.assertEqual(len([event for event in break_events if event["result"] == "requested"]), 11)
        self.assertTrue(all("waveform" in event["interpretation"] for event in break_events))
        self.assertFalse(json.loads(trace.export_json())["evidence"]["electrical_waveform_measured"])

    def test_partial_serial_open_failure_closes_and_forgets_the_handle(self):
        class RtsFailureSerial(FakeSerial):
            @property
            def rts(self):
                return False

            @rts.setter
            def rts(self, _value):
                raise OSError("RTS control failed")

        serial = RtsFailureSerial()
        transport = SerialKLineTransport(lambda **kwargs: serial)

        with self.assertRaises(DiagnosticError) as raised:
            transport.open("COM7", baudrate=10400, timeout=0.2)

        self.assertEqual(raised.exception.code, "serial_open_failed")
        self.assertTrue(serial.closed)
        self.assertIsNone(transport._serial)

    def test_serial_transport_fails_closed_without_break_control(self):
        serial = FakeSerial()
        sleeps = []
        transport = SerialKLineTransport(lambda **kwargs: serial, sleeper=sleeps.append)
        transport.open("COM7", baudrate=10400, timeout=0.2)

        with self.assertRaisesRegex(DiagnosticError, "BREAK"):
            transport.init_5baud(0x01, 0.2)

        self.assertEqual(serial.writes, [])
        self.assertEqual(sleeps, [])
