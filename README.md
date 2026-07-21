# Audi A4 B5 1.9 TDI (AFN/EDC15) Diagnostics

A native Linux automotive diagnostics application for the 1999 Audi A4 B5 with 1.9 TDI AFN engine and EDC15 ECU, using the proprietary VAG KW1281 protocol over K-Line (ISO 9141) via FTDI USB-KKL adapter.

## Features

- **KW1281 Protocol Implementation**: Complete 5-baud initialization sequence → 10400 baud communication with block-level ACK/NAK handling
- **Real-time Telemetry**: Polls Measuring Blocks 003 (MAF/RPM), 007 (Temperatures), 011 (MAP/Boost) at 10Hz
- **PyQt6 Dashboard**: Native Wayland-compatible GUI with animated circular gauges (RPM, MAP Actual/Spec, MAF Actual/Spec, Boost, Temps, Wastegate, Engine Load)
- **Async Architecture**: Serial communication runs in dedicated worker thread with Qt signals for thread-safe GUI updates
- **MySQL Logging**: Buffered bulk INSERT (1s intervals, 100 rows/batch) with automatic reconnection and session management
- **Robust Error Handling**: Exponential backoff reconnection, checksum validation, timeout handling, protocol error recovery

## Hardware Requirements

- 1999 Audi A4 B5 1.9 TDI (AFN engine, EDC15 ECU)
- FTDI-based USB-KKL cable (VAG KKL 409.1 compatible) → `/dev/ttyUSB0`
- Linux system with Python 3.11+

## Installation

```bash
# Clone repository
cd audi_diag

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install MySQL client library (Ubuntu/Debian)
sudo apt-get install libmysqlclient-dev

# Or on Fedora/RHEL
sudo dnf install mysql-devel
```

## Database Setup

```sql
-- Create database and user
CREATE DATABASE audi_diag CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'audi_diag'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON audi_diag.* TO 'audi_diag'@'localhost';
FLUSH PRIVILEGES;

-- Schema is auto-created on first run, or manually:
mysql -u audi_diag -p audi_diag < sql/schema.sql
```

## Configuration

Copy and edit the configuration file:

```bash
cp config/config.yaml config/config.local.yaml
# Edit config.local.yaml with your settings
```

Key settings:
```yaml
serial:
  port: "/dev/ttyUSB0"      # Your KKL adapter port
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
```bash
python main.py
```

### Headless Logging Mode
```bash
# Log to database only (no GUI)
python main.py --headless

# With custom config
python main.py --headless --config config/config.local.yaml
```

### Utility Commands
```bash
# List detected KKL adapters
python main.py --list-ports

# Create database schema and exit
python main.py --create-schema

# Override serial port
python main.py --port /dev/ttyUSB1 --baud 10400
```

## Measuring Blocks (EDC15 AFN)

| Block | Name | Key Parameters |
|-------|------|----------------|
| **003** | MAF/RPM | RPM, MAF Actual/Specified (mg/stroke), Engine Load (%), Throttle Position (%), IQ Actual/Specified |
| **007** | Temperatures | Coolant, Intake Air, Fuel, Oil, Ambient, EGR (°C) |
| **011** | MAP/Boost | MAP Actual/Specified (mbar), Boost Pressure (mbar), Wastegate Duty (%), N75 Valve Duty (%), EGR Duty (%) |

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

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Format code
black src/
ruff check src/

# Type checking
mypy src/
```

## Project Structure

```
audi_diag/
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
├── config/
│   ├── config.yaml         # Default configuration
│   └── config.local.yaml   # Local overrides (gitignored)
├── sql/
│   └── schema.sql          # MySQL schema
├── src/
│   ├── __init__.py         # Package exports
│   ├── kw1281_handler.py   # KW1281 protocol implementation
│   ├── telemetry_worker.py # Async worker thread (Qt + asyncio)
│   ├── database_logger.py  # MySQL bulk logging
│   ├── main_window.py      # PyQt6 dashboard UI
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

### "Permission denied" on /dev/ttyUSB0
```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

### No KKL adapter detected
```bash
# Check FTDI device
lsusb | grep -i ftdi
dmesg | grep ttyUSB

# Verify kernel modules
lsmod | grep ftdi
```

### Connection fails at 5-baud init
- Verify cable is connected to both laptop and car OBD port
- Ignition ON (position 2, not cranking)
- Try different USB port or cable
- Check `dmesg` for FTDI errors

### MySQL connection refused
```bash
# Check MySQL is running
systemctl status mysql

# Verify credentials
mysql -u audi_diag -p audi_diag
```

## License

MIT License - See LICENSE file for details.

## References

- VAG KW1281 Protocol Specification (internal VW/Audi documentation)
- ISO 9141-2 / ISO 14230 (KWP2000) - K-Line physical layer
- EDC15 Bosch ECU measuring block definitions
- FTDI FT232R/FT245R datasheet for USB-KKL adapter