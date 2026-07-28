# 🚗 Audi A4 B5 1.9 TDI — KW1281 Diagnostics

> ⚠️ **IN DEVELOPMENT** — This project is under active development. Features may change. Contributions welcome!

Automotive diagnostics tool for **Audi A4 B5 1999 1.9 TDI (AFN engine, EDC15 ECU)** via KKL/VAG-COM USB cable (FTDI chip), built in **Python/PyQt6**.

## Features

- **Full KW1281 Protocol**: 5-baud init (0x33) → 10400 baud with keyword handshake
- **Real-time Telemetry**: Measuring Blocks 003 (MAF/RPM), 007 (Temperatures), 011 (MAP/Boost) at 10 Hz
- **PyQt6 Dashboard**: Native animated gauges (RPM, MAP, MAF, Boost, Temperatures, Engine Load, Wastegate/N75)
- **Async Architecture**: Serial communication in dedicated worker thread + asyncio
- **MySQL/MariaDB Logging**: Buffered bulk INSERT (1s / 100 rows) with auto-reconnect
- **Headless Mode**: Logging without GUI (ideal for track datalogging)
- **FTDI Auto-detection**: Lists all COM ports and identifies KKL adapters

## Hardware Requirements

- **Car**: Audi A4 B5 1999 1.9 TDI (AFN engine, EDC15 ECU)
- **KKL USB Cable** with **FTDI** chip (VID 0x0403, PIDs 0x6001/0x6010/0x6011/0x6014/0x6015)
- **OS**: Windows 10/11 with Python 3.11+ (or use the standalone `.exe`)

## Installation

```cmd
:: Clone repository
git clone https://github.com/Pabloar7882/audi-diag.git
cd audi_diag

:: Create virtual environment
python -m venv venv
venv\Scripts\activate

:: Install dependencies
pip install -r requirements.txt
```

## Database (MySQL/MariaDB)

```sql
-- Create database and user
CREATE DATABASE audi_diag CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'audi_diag'@'localhost' IDENTIFIED BY 'your_strong_password';
GRANT ALL PRIVILEGES ON audi_diag.* TO 'audi_diag'@'localhost';
FLUSH PRIVILEGES;

-- Schema is auto-created on first run, or manually:
mysql -u audi_diag -p audi_diag < sql\schema.sql
```

> **Tip**: Use [HeidiSQL](https://www.heidisql.com/) or [DBeaver](https://dbeaver.io/) as GUI for database management on Windows.

## Configuration

Copy and edit the configuration file:

```cmd
copy config\config.yaml config\config.local.yaml
notepad config\config.local.yaml
```

Key settings:

```yaml
serial:
  port: "COM3"              # Your KKL port (use --list-ports to see)
  baudrate: 10400           # KW1281 standard
  auto_detect: true         # Auto-find FTDI adapter

database:
  host: "localhost"
  port: 3306
  database: "audi_diag"
  user: "audi_diag"
  password: "your_password" # CHANGE THIS!

telemetry:
  poll_interval_ms: 100     # 10 Hz
  blocks: [3, 7, 11]        # MB003, MB007, MB011
```

## Usage

### GUI Dashboard
```cmd
python main.py
```

### Headless Mode (no GUI)
```cmd
python main.py --headless
python main.py --headless --config config\config.local.yaml
```

### List Available COM Ports
```cmd
python main.py --list-ports
```

Example output:
```
============================================================
  DETECTED COM PORTS ON WINDOWS
============================================================
  [COM3] USB Serial Port (FTDI) ← RECOMMENDED (FTDI KKL)
              Serial: ABC12345
              VID:PID=0403:6001
  [COM5] USB-SERIAL CH340

------------------------------------------------------------
  FTDI KKL Adapters Found: 1
  → Use COM3 in configuration
============================================================
```

### Other Commands
```cmd
:: Create database schema and exit
python main.py --create-schema

:: Override port and baudrate
python main.py --port COM4 --baud 10400

:: Verbose log level
python main.py --log-level DEBUG
```

## Measuring Blocks (EDC15 AFN)

| Block | Name | Key Parameters |
|-------|------|----------------|
| **003** | MAF/RPM | RPM, MAF Actual/Spec (mg/stroke), Engine Load (%), Throttle Position (%), IQ Actual/Spec |
| **007** | Temperatures | Coolant, Intake Air, Fuel, Oil, Ambient, EGR (°C) |
| **011** | MAP/Boost | MAP Actual/Spec (mbar), Boost Pressure (mbar), Wastegate Duty (%), N75 Duty (%), EGR Duty (%) |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Main Window (PyQt6 Dashboard)                │
│  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐        │
│  │  RPM  │  │MAP Act│  │MAP Esp│  │MAF Act│  │MAF Esp│        │
│  │ Gauge │  │ Gauge │  │ Gauge │  │ Gauge │  │ Gauge │        │
│  └───┬───┘  └───┬───┘  └───┬───┘  └───┬───┘  └───┬───┘        │
│      └──────────┴──────────┴──────────┴──────────┘             │
│                          │  Qt Signals                          │
│  ┌───────────────────────┴─────────────────────────────────┐   │
│  │              TelemetryWorker (QThread + asyncio)         │   │
│  │  ┌───────────────────────────────────────────────────┐  │   │
│  │  │              KW1281Handler                        │  │   │
│  │  │  5-baud init → 10400 baud → Block read → ACK     │  │   │
│  │  │  MB 003, 007, 011 parsing                        │  │   │
│  │  └───────────────────────────────────────────────────┘  │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         ▼                                       │
│                    DatabaseLogger (async)                       │
│                    Bulk INSERT → MySQL/MariaDB                  │
└─────────────────────────────────────────────────────────────────┘
```

## KW1281 Protocol (Summary)

### 5-Baud Initialization Sequence
```
1. Open serial at 5 baud, 8N1
2. Send break pulse (25ms low)
3. Send address: 0x33
4. Wait for ECU sync: 0x55
5. Send Keyword 1: 0x01 → Inverted echo: 0xFE
6. Send Keyword 2: 0x8A (10400 baud) → Inverted echo: 0x75
7. Wait 300ms for baudrate switch
8. Reopen serial at 10400 baud
9. Send Start Communication (0x81)
10. Positive response: 0xC1
```

### Block Format (10400 baud)
- Each block: `[Length][Address][Command/Type][Data...][Checksum]`
- Checksum: 8-bit sum of all bytes, inverted + 1
- Types: DATA (0x01), ACK (0x00), END (0x02), NAK (0x03)

## Project Structure

```
audi_diag/
├── main.py                  # Entry point (CLI + GUI)
├── requirements.txt         # Dependencies
├── AudiDiag.spec            # PyInstaller spec
├── config/
│   ├── config.yaml          # Default configuration
│   └── config.local.yaml    # Local overrides (gitignored)
├── sql/
│   └── schema.sql           # MySQL schema
├── src/
│   ├── __init__.py
│   ├── kw1281_handler.py    # KW1281 protocol
│   ├── telemetry_worker.py  # Worker thread + Qt signals
│   ├── database_logger.py   # MySQL bulk logging
│   ├── main_window.py       # PyQt6 dashboard
│   ├── config/
│   │   └── config_loader.py
│   └── db/
│       └── database_logger.py
└── dist/
    └── AudiDiag.exe         # Standalone executable
```

## Troubleshooting

### "Access denied" on COM port
- Close other programs using the port (VCDS, PuTTY, Arduino IDE)
- Check **Device Manager** → Ports (COM & LPT)

### No adapter detected
```cmd
python main.py --list-ports
```
- Install FTDI drivers: https://ftdichip.com/drivers/vcp-drivers/
- Cheap cables with CH340/CP2102 chips **will not work** (needs FTDI)

### MySQL Connection Error
- Verify MySQL/MariaDB service is running: `services.msc`
- Test: `mysql -h localhost -u audi_diag -p`

### Gauges Not Updating
- Ignition must be **ON** (dashboard lights on)
- Cable firmly seated in OBD-2 port (pin 7 = K-line)

## License

Educational / personal use. Not affiliated with Volkswagen AG / Audi AG.
