# TEST VERSION
# Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics

A native **Windows** automotive diagnostics application for the 1999 Audi A4 B5 with 1.9 TDI AFN engine and EDC15 ECU, using the proprietary VAG KW1281 protocol over K-Line (ISO 9141) via FTDI USB-KKL adapter.

## Features

- **KW1281 Protocol Implementation**: Complete 5-baud initialization sequence → 10400 baud communication with block-level ACK/NAK handling
- **Real-time Telemetry**: Polls Measuring Blocks 003 (MAF/RPM), 007 (Temperatures), 011 (MAP/Boost) at 10Hz
- **PyQt6 Dashboard**: Native Windows-compatible GUI with animated circular gauges (RPM, MAP Actual/Spec, MAF Actual/Spec, Boost, Temps, Wastegate, Engine Load)
- **Async Architecture**: Serial communication runs in dedicated worker thread with Qt signals for thread-safe GUI updates
- **MySQL/MariaDB Logging**: Buffered bulk INSERT (1s intervals, 100 rows/batch) with automatic reconnection and session management
- **Robust Error Handling**: Exponential backoff reconnection, checksum validation, timeout handling, protocol error recovery
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
copy config\config.yaml config\config.local.yaml
# Edit config.local.yaml with your settings (Notepad, VS Code, etc.)
```

Key settings in `config.local.yaml`:

```yaml
serial:
  port: "COM3"              # Your KKL adapter COM port (check Device Manager)
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
  blocks: [3, 7, 11]        # MB003, MB007, MB011
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

# Build (uses AudiDiag.spec)
pyinstaller AudiDiag.spec

# Output: dist\AudiDiag.exe (single file, ~40MB)
# Run on any Windows 10/11 machine without Python
```

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

### 5-Baud Initialization Sequence
```
1. Open serial at 5 baud, 8N1
2. Send break pulse (25ms low)
3. Send address byte: 0x33 (00110011)
4. Wait for ECU sync: 0x55
5. Send Keyword 1: 0x01 → Wait for inverted echo: 0xFE
6. Send Keyword 2: 0x8A (10400 baud) → Wait for inverted echo: 0x75
7. Wait 300ms for ECU baud rate switch
8. Reopen serial at 10400 baud
9. Send Start Communication (0x81)
10. Expect positive response: 0xC1
```

### Block Communication (10400 baud)
- Each block: `[Length][Address][Command/Type][Data...][Checksum]`
- Checksum: 8-bit sum of all bytes (except length), inverted + 1
- Block types: DATA (0x01), ACK (0x00), END (0x02), NAK (0x03)
- Block counter increments per data block, must be ACKed

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
├── config/
│   ├── config.yaml        # Default configuration
│   └── config.local.yaml  # Local overrides (gitignored)
├── sql/
│   └── schema.sql         # MySQL/MariaDB schema
├── src/
│   ├── __init__.py        # Package exports
│   ├── kw1281_handler.py  # KW1281 protocol implementation
│   ├── telemetry_worker.py # Async worker thread (Qt + asyncio)
│   ├── database_logger.py  # MySQL bulk logging
│   ├── main_window.py     # PyQt6 dashboard UI
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

- VAG KW1281 Protocol Specification (internal VW/Audi documentation)
- ISO 9141-2 / ISO 14230 (KWP2000) — K-Line physical layer
- EDC15 Bosch ECU measuring block definitions
- FTDI FT232R/FT245R datasheet for USB-KKL adapter
