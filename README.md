# Audi A4 B5 AFN Diagnostics

> **TESTING PHASE — PUBLIC EXPERIMENTAL PROJECT.** This software has not been
> validated against the author's vehicle. Treat all live-connection behavior
> and sensor readings as unverified.

Read-only diagnostics and telemetry experiments for a 1999 Audi A4 B5 Avant
1.9 TDI AFN. The target ECU part number is **038 906 018 FD**. The Windows
interface is a native desktop executable; Android and ESP32 implementations
are experimental.

## Current status

- Windows desktop app with serial-port selection, ECU identification, explicit
  reads of groups 001, 003 and 011, optional sequential monitoring, and trace
  export.
- Android USB serial client and portable ESP32 KW1281 protocol core.
- Web dashboard for synthetic/demo data and VCDS CSV replay.
- Software tests and protocol simulators. These do not validate the electrical
  interface, ECU compatibility, live sensor scaling, or vehicle behavior.
- The FTDI VAG KKL 409.1 cable has worked with VCDS-Lite, but the new app has
  not yet completed a verified live session with this ECU.
- 9600 baud is only an initial selectable setting. The ECU's working rate and
  BREAK timing have not been confirmed.

Do not use this project while driving. The diagnostic paths are designed for
read-only operation; no coding, adaptation, fault clearing, actuator tests,
EEPROM access, or map writing are provided as supported features.

## Windows desktop

Build a single-file executable on Windows with Python 3 and PyInstaller:

```powershell
py -m pip install pyinstaller pyserial
powershell -ExecutionPolicy Bypass -File tools/build_windows_exe.ps1
```

The output is `dist/FIS AFN Desktop.exe`. Select the cable's COM port and baud
rate, then explicitly click **Identify ECU**. Once identified, request one
allowed group or explicitly start monitoring. The initial baud selection is
not a confirmed setting for this ECU.

## Android

Open the `android` folder in Android Studio or use its Gradle wrapper. Connect
an FTDI KKL interface through USB OTG and grant USB permission. ECU identification
and measurement requests are explicit actions. Hardware validation is pending.

## ESP32 protocol core

The portable C core and native test harness are in `firmware/esp32`. It does
not yet include a vehicle-ready board pin map, transceiver wiring, or validated
physical transport. Follow the firmware README and license notes before use.

## Development tests

```powershell
py -m unittest discover -s tests
```

Some UI tests also require Node.js. The Android unit tests run with
`android/gradlew.bat testDebugUnitTest` on a configured Android build machine.

## Public testing and contributions

This repository is public so the implementation and test progress can be
reviewed. It is an early-stage testing project, not a finished diagnostic tool.
Please report the ECU part number, component string, adapter chipset and
sanitized diagnostic trace when reporting a connection issue. Remove VINs and
other personal identifiers before sharing traces.

See [third-party notices and licenses](docs/THIRD_PARTY_LICENSES.md) before
redistributing any part of the project. Proprietary VCDS label files are not
included.
