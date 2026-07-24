# ⚠️ IN DEVELOPMENT / TESTING PHASE

> This project is actively under development and **still in testing phase**.
> Not all features have been validated on a real car yet, and you may run
> into bugs, unstable connections, or incorrect block readings. Use at your
> own risk — do not rely on this for definitive vehicle diagnostics.

# Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics

A native **Windows** automotive diagnostics application for the 1999 Audi A4 B5 with 1.9 TDI AFN engine and EDC15 ECU, using the proprietary VAG KW1281 protocol over K-Line (ISO 9141) via FTDI USB-KKL adapter.

> **Platform:** this application is built and tested exclusively for **Windows 10/11**. There is no Linux/macOS support.

## Features

- **Real KW1281 Protocol**: byte-by-byte complement handshake (not a simple checksum), correct block titles (0x05/0x06/0x07/0x09/0x29/0xE7/0xF6/0xFC), verified against the public reference and the open-source `kw1281test` project
- **Simple Launcher Menu**: the app opens to a big-button start screen ("Watch the Engine Live", "Check for Problems", "Look at One Sensor") instead of dropping straight into a technical dashboard - no jargon, easy for anyone to use
- **Gauge Pages**: pick a named page (Full Dashboard, Engine Basics, Temperatures, Boost/Turbo) instead of hunting for KW1281 group numbers yourself - only the relevant gauges show up
- **Two dashboard looks**: classic analog dial gauges, or a "big numbers" digital-card view - switch anytime with one button, both stay in sync
- **Fault Codes (DTCs)**: real read/clear commands against the ECU, shown in a simple table
- **Trend Charts**: rolling RPM/Boost/Coolant history graph, no extra dependencies
- **Sensor Explorer**: advanced users can query any measuring group by number and see the decoded raw fields
- **Alerts**: gauges change color near warning/critical thresholds, plus a one-time beep + status message when a value goes critical
- **Port Selector**: dropdown listing every detected COM port, flags recognized FTDI KKL adapters, no manual typing
- **Global Error Handling**: unexpected errors show a dialog and get logged to `logs/audi_diag.log` instead of the app silently closing
- **Real-time Telemetry**: Polls Measuring Blocks 003 (MAF/RPM), 007 (Temperatures), 011 (MAP/Boost) at 10Hz — limited to 3 blocks per cycle to match older ECU capabilities
- **PyQt6 Dashboard**: Native Windows GUI with animated circular gauges (RPM, MAP Actual/Spec, MAF Actual/Spec, Boost, Temps, Wastegate, Engine Load)
- **Async Architecture**: Serial communication runs in a dedicated worker thread with Qt signals for thread-safe GUI updates
- **MySQL/MariaDB Logging**: Buffered bulk INSERT (1s intervals, 100 rows/batch) with automatic reconnection and session management
- **Packaged Executable**: PyInstaller `.exe` for distribution without Python installation

## Hardware Requirements

- 1999 Audi A4 B5 1.9 TDI (AFN engine, EDC15 ECU)
- FTDI-based USB-KKL cable (VAG KKL 409.1 compatible) → `COM3`, `COM4`, etc.
- Windows 10/11 with Python 3.11+ (or use the standalone `.exe`)

## Installation (Development)

```cmd
# Clone repository
git clone https://github.com/Pabloar7882/audi-diag.git
cd audi_diag

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install MySQL client library (for database logging)
# Option 1: MariaDB Connector/C (recommended)
# Download from: https://mariadb.com/downloads/connectors/
# Or use MySQL Connector/C from: https://dev.mysql.com/downloads/connector/c/
```

## Database Setup (MariaDB/MySQL)

```sql
-- Run in MariaDB/MySQL client (HeidiSQL, DBeaver, MySQL Workbench, or CLI)
CREATE DATABASE audi_diag CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'audi_diag'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON audi_diag.* TO 'audi_diag'@'localhost';
FLUSH PRIVILEGES;

-- Schema is auto-created on first run, or manually:
mysql -u audi_diag -p audi_diag < sql\schema.sql
```

> **Tip**: On Windows, [HeidiSQL](https://www.heidisql.com/) or [DBeaver](https://dbeaver.io/) are excellent free GUI tools for database management.

## Configuration

Copy and edit the configuration file:

```cmd
copy config\config.example.yaml config\config.yaml
# Edit config.yaml with your real settings (Notepad, VS Code, etc.)
```

> **Security note:** `config/config.yaml` holds your real MySQL password and is gitignored - never commit it. `config/config.example.yaml` (committed, placeholder values) is the template.

Key settings in `config.local.yaml`:

```yaml
serial:
  port: "COM3"              # Optional default - the app now shows a live dropdown
                             # of every detected COM port at startup, so you can
                             # also just pick it from the "Port" selector in the GUI
  baudrate: 10400           # KW1281 standard
  auto_detect: true         # Auto-find FTDI adapters

database:
  host: "localhost"
  port: 3306
  database: "audi_diag"
  user: "audi_diag"
  password: "your_password"  # CHANGE THIS!

telemetry:
  poll_interval_ms: 100     # 10Hz polling
  blocks: [3, 7, 11]        # MB003, MB007, MB011 - max 3 blocks per cycle (older ECU limit)
```

## Usage

### GUI Mode (Default)
```cmd
python main.py
```
Or run the standalone executable:
```cmd
dist\AudiDiag.exe
```

### Headless Logging Mode (No GUI)
```cmd
# Log to database only
python main.py --headless

# With custom config
python main.py --headless --config config\config.local.yaml
```

### Utility Commands
```cmd
# List detected KKL adapters
python main.py --list-ports

# Create database schema and exit
python main.py --create-schema

# Override serial port
python main.py --port COM4 --baud 10400
```

## Measuring Blocks (EDC15 AFN)

| Block | Name | Key Parameters |
|-------|------|----------------|
| **003** | MAF/RPM | RPM, MAF Actual/Specified (mg/stroke), Engine Load (%), Throttle Position (%), IQ Actual/Specified |
| **007** | Temperatures | Coolant, Intake Air, Fuel, Oil, Ambient, EGR (°C) |
| **011** | MAP/Boost | MAP Actual/Specified (mbar), Boost Pressure (mbar), Wastegate Duty (%), N75 Valve Duty (%), EGR Duty (%) |

## Building Standalone Executable

```cmd
# Install PyInstaller
pip install pyinstaller

# Clean rebuild (recommended - avoids stale cached modules from previous builds)
rmdir /s /q build
rmdir /s /q dist
pyinstaller AudiDiag.spec --clean --noconfirm

# Output: dist\AudiDiag.exe (single file, ~40MB)
# Run on any Windows 10/11 machine without Python
```

> `AudiDiag.spec` adds `src` via `pathex`, so PyInstaller analyzes `main_window.py`, `telemetry_worker.py`, `kw1281_handler.py`, etc. as real code and follows their imports automatically (instead of just copying them as raw data files). If you add a new third-party import inside `src/`, you generally don't need to touch `hiddenimports` — a clean `--clean` rebuild should pick it up.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Main Window (Qt)                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │  RPM    │ │ MAP Act │ │ MAP Spec│ │ MAF Act │ │ MAF Spec│   │
│  │  Gauge  │ │  Gauge  │ │  Gauge  │ │  Gauge  │ │  Gauge  │   │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │
│       │           │           │           │           │        │
│  ┌────┴───────────┴───────────┴───────────┴───────────┴────┐  │
│  │              TelemetryWorker (QThread)                   │  │
│  │  ┌────────────────────────────────────────────────────┐  │  │
│  │  │           asyncio Event Loop                       │  │  │
│  │  │  ┌──────────────────────────────────────────────┐  │  │  │
│  │  │  │           KW1281Handler                      │  │  │  │
│  │  │  │  • 5-baud init (0x33 @ 5bps → 0x55 sync)     │  │  │  │
│  │  │  │  • Keyword exchange (0x01/0xFE, 0x8A/0x75)   │  │  │  │
│  │  │  │  • 10400 baud switch                         │  │  │  │
│  │  │  │  • Block read (0x21) with ACK handling       │  │  │  │
│  │  │  │  • MB 003, 007, 011 parsing                  │  │  │  │
│  │  │  └──────────────────────────────────────────────┘  │  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  │         ▲                    ▲                    ▲       │  │
│  │    telemetry            ecu_id               error        │  │
│  │         │                    │                    │       │  │
│  └─────────┼────────────────────┼────────────────────┼───────┘  │
│            ▼                    ▼                    ▼          │
│       Qt Signals            Qt Signals            Qt Signals    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DatabaseLogger (async)                       │
│  • Buffered bulk INSERT (1s / 100 rows)                        │
│  • Connection pool (5 connections)                             │
│  • Auto-reconnect + session management                         │
└─────────────────────────────────────────────────────────────────┘
```

## KW1281 Protocol Details

> Rewritten to match the real, documented KW1281 protocol (see References) —
> the original checksum-based framing below was replaced early in development
> once it turned out to not match how real VAG ECUs actually talk.

### 5-Baud Initialization Sequence
```
1. Tester sends the module address at 5 baud (0x01 for the engine ECU)
2. Reopen serial at the target baud rate (10400)
3. ECU sends sync byte: 0x55
4. ECU sends keyword: 0x01, 0x8A
5. Tester replies with the complement of the 2nd keyword byte: 0x75
6. ECU immediately starts sending its self-introduction (ASCII blocks)
```

### Block Communication (10400 baud)
- Each block: `[Length][Counter][Title][Data...][0x03 block-end]`
- **No checksum byte** - instead, every byte is individually confirmed: the
  receiver echoes back its one's-complement (0xFF − byte) before the sender
  proceeds to the next byte. The block-end byte (0x03) is the only byte
  that's *not* complemented.
- Block titles: `0x05` clear fault codes, `0x06` end communication, `0x07`
  read fault codes, `0x09` ACK, `0x29` group reading (measuring blocks),
  `0xE7`/`0xF6`/`0xFC` are the corresponding response titles
- The block counter increments by 1 on every block, shared by both directions

## Development

```cmd
# Install dev dependencies
pip install black ruff mypy pytest

# Format code
black src\

# Lint
ruff check src\

# Type checking
mypy src\

# Run tests
pytest tests\
```

## Project Structure

```
audi_diag/
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
├── AudiDiag.spec          # PyInstaller spec
├── README.md              # This file
├── .gitignore              # Excludes build/, dist/, config.yaml (real password), logs/
├── config/
│   ├── config.example.yaml # Template (committed) - copy to config.yaml
│   └── config.yaml         # Your real config incl. DB password (gitignored)
├── sql/
│   └── schema.sql         # MySQL/MariaDB schema (also auto-created by the app on first run)
├── src/
│   ├── __init__.py        # Package exports
│   ├── kw1281_handler.py  # KW1281 protocol (real byte-complement framing, fault codes, group reading, port discovery)
│   ├── telemetry_worker.py # Async worker thread (Qt + asyncio), fault-code/group request queue
│   ├── main_window.py     # PyQt6 UI: launcher menu, dashboard (2 looks), trends, alerts, dialogs
│   ├── config/
│   │   ├── config_loader.py
│   │   └── __init__.py
│   └── db/
│       ├── database_logger.py
│       └── __init__.py
└── tests/
    └── test_kw1281.py
```

## Troubleshooting

### App closes with no error message
As of the latest build, unhandled errors are caught by a global exception
handler: you should now see an error dialog, and full details get written to
`logs\audi_diag.log`. If the app still disappears with no dialog at all:
1. Check `logs\audi_diag.log` for a traceback around the time it happened
2. Try running `python main.py` from a terminal instead of the `.exe` — this
   keeps a console open and shows the crash directly
3. If neither shows anything, it's likely a low-level crash outside Python's
   control (e.g. a broken/virtual COM port driver) — try a different port

### "Access denied" or COM port not found
1. Open **Device Manager** → **Ports (COM & LPT)**
2. Look for **USB Serial Port (COMx)** — that's your KKL adapter
3. Note the COM number (e.g., `COM3`) and update `config.local.yaml`
4. If not visible: install [FTDI VCP Drivers](https://ftdichip.com/drivers/vcp-drivers/)

### No KKL adapter detected
```cmd
# Check USB devices (PowerShell)
Get-PnpDevice -Class USB | Where-Object {$_.FriendlyName -like "*FTDI*"}

# Or use Device Manager → View → Devices by connection
```

### Connection fails at 5-baud init
- Verify cable is connected to **both** laptop and car OBD port
- Ignition **ON** (position 2, dashboard lights on, **not cranking**)
- Try different USB port or cable
- Check Device Manager for FTDI errors (yellow triangle)

### MySQL/MariaDB connection refused
```cmd
# Check service is running
sc query mariadb
# or
sc query mysql

# Start if stopped
net start mariadb

# Test connection
mysql -u audi_diag -p audi_diag
```

### PyInstaller executable fails
```cmd
# Rebuild with clean
pyinstaller --clean AudiDiag.spec

# If antivirus flags it, add exclusion for dist\ folder
```

## License

MIT License — See LICENSE file for details.

## References

- [blafusel.de KW1281 protocol writeup](https://www.blafusel.de/obd/obd2_kw1281.html) — the public reference used to correct the block framing and Kennzahl (value formula) table
- [gmenounos/kw1281test](https://github.com/gmenounos/kw1281test) — open-source C# reference tool, used to verify block titles and command sequences
- ISO 9141-2 / ISO 14230 (KWP2000) — K-Line physical layer
- FTDI FT232R/FT245R datasheet for USB-KKL adapter