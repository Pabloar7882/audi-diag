# 🚗 Audi A4 B5 1.9 TDI — Diagnóstico KW1281

> ⚠️ **EM DESENVOLVIMENTO** — Este projeto encontra-se em fase ativa de desenvolvimento. Funcionalidades podem mudar. Contribuições são bem-vindas!

Ferramenta de diagnóstico automotivo para **Audi A4 B5 1999 1.9 TDI (motor AFN, ECU EDC15)** via cabo KKL/VAG-COM USB (chip FTDI), feita em **Python/PyQt6**.

## Funcionalidades

- **Protocolo KW1281 completo**: Inicialização 5 baud (0x33) → 10400 baud com handshake de palavras-chave
- **Telemetria tempo real**: Blocos de Medição 003 (MAF/RPM), 007 (Temperaturas), 011 (MAP/Boost) a 10 Hz
- **Dashboard PyQt6**: Gauges animados nativos (RPM, MAP, MAF, Boost, Temperaturas, Carga Motor, Wastegate/N75)
- **Arquitetura assíncrona**: Comunicação serial em thread worker dedicada + asyncio
- **Logging MySQL/MariaDB**: Bulk INSERT bufferizado (1s / 100 linhas) com reconexão automática
- **Modo headless**: Logging sem GUI (ideal para datalogging em pista)
- **Auto-detecção FTDI**: Lista todas as portas COM e identifica adaptadores KKL

## Requisitos de Hardware

- **Carro**: Audi A4 B5 1999 1.9 TDI (motor AFN, ECU EDC15)
- **Cabo KKL USB** com chip **FTDI** (VID 0x0403, PIDs 0x6001/0x6010/0x6011/0x6014/0x6015)
- **Sistema**: Windows 10/11 com Python 3.11+ (ou usar o `.exe` standalone)

## Instalação

```cmd
:: Clone o repositório
git clone https://github.com/Pabloar7882/audi-diag.git
cd audi_diag

:: Crie ambiente virtual
python -m venv venv
venv\Scripts\activate

:: Instale dependências
pip install -r requirements.txt
```

## Banco de Dados (MySQL/MariaDB)

```sql
-- Crie database e usuário
CREATE DATABASE audi_diag CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'audi_diag'@'localhost' IDENTIFIED BY 'sua_senha_forte';
GRANT ALL PRIVILEGES ON audi_diag.* TO 'audi_diag'@'localhost';
FLUSH PRIVILEGES;

-- Schema é auto-criado na primeira execução, ou manualmente:
mysql -u audi_diag -p audi_diag < sql\schema.sql
```

> **Tip**: Use [HeidiSQL](https://www.heidisql.com/) ou [DBeaver](https://dbeaver.io/) como GUI para gerir a base de dados no Windows.

## Configuração

Copie e edite o ficheiro de configuração:

```cmd
copy config\config.yaml config\config.local.yaml
notepad config\config.local.yaml
```

Principais ajustes:

```yaml
serial:
  port: "COM3"              # Sua porta KKL (use --list-ports para ver)
  baudrate: 10400           # Padrão KW1281
  auto_detect: true         # Auto-encontra adaptador FTDI

database:
  host: "localhost"
  port: 3306
  database: "audi_diag"
  user: "audi_diag"
  password: "sua_senha"     # ALTERE ISTO!

telemetry:
  poll_interval_ms: 100     # 10 Hz
  blocks: [3, 7, 11]        # MB003, MB007, MB011
```

## Uso

### Dashboard GUI
```cmd
python main.py
```

### Modo Headless (sem GUI)
```cmd
python main.py --headless
python main.py --headless --config config\config.local.yaml
```

### Listar portas COM disponíveis
```cmd
python main.py --list-ports
```

Exemplo de saída:
```
============================================================
  PORTAS COM DETETADAS NO WINDOWS
============================================================
  [COM3] USB Serial Port (FTDI) ← RECOMENDADO (FTDI KKL)
              Serial: ABC12345
              VID:PID=0403:6001
  [COM5] USB-SERIAL CH340

------------------------------------------------------------
  Adaptadores KKL FTDI encontrados: 1
  → Use COM3 na configuração
============================================================
```

### Outros comandos
```cmd
:: Criar schema da base de dados e sair
python main.py --create-schema

:: Sobrescrever porta e baudrate
python main.py --port COM4 --baud 10400

:: Nível de log verbose
python main.py --log-level DEBUG
```

## Blocos de Medição (EDC15 AFN)

| Bloco | Nome | Parâmetros Principais |
|-------|------|----------------------|
| **003** | MAF/RPM | RPM, MAF Atual/Espec (mg/curso), Carga Motor (%), Posição Acelerador (%), IQ Atual/Espec |
| **007** | Temperaturas | Refrigeração, Admissão, Combustível, Óleo, Ambiente, EGR (°C) |
| **011** | MAP/Boost | MAP Atual/Espec (mbar), Pressão Boost (mbar), Duty Wastegate (%), Duty N75 (%), Duty EGR (%) |

## Arquitetura

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

## Protocolo KW1281 (Resumo)

### Sequência de Inicialização 5 Baud
```
1. Abrir serial a 5 baud, 8N1
2. Enviar pulso break (25ms low)
3. Enviar endereço: 0x33
4. Aguardar sync ECU: 0x55
5. Enviar Keyword 1: 0x01 → Eco invertido: 0xFE
6. Enviar Keyword 2: 0x8A (10400 baud) → Eco invertido: 0x75
7. Aguardar 300ms para troca de baudrate
8. Reabrir serial em 10400 baud
9. Enviar Start Communication (0x81)
10. Resposta positiva: 0xC1
```

### Formato de Blocos (10400 baud)
- Cada bloco: `[Length][Address][Command/Type][Data...][Checksum]`
- Checksum: Soma 8-bit de todos os bytes, invertida + 1
- Tipos: DATA (0x01), ACK (0x00), END (0x02), NAK (0x03)

## Estrutura do Projeto

```
audi_diag/
├── main.py                  # Entry point (CLI + GUI)
├── requirements.txt         # Dependências
├── AudiDiag.spec            # Spec PyInstaller
├── config/
│   ├── config.yaml          # Configuração padrão
│   └── config.local.yaml    # Overrides locais (gitignored)
├── sql/
│   └── schema.sql           # Schema MySQL
├── src/
│   ├── __init__.py
│   ├── kw1281_handler.py    # Protocolo KW1281
│   ├── telemetry_worker.py  # Worker thread + Qt signals
│   ├── database_logger.py   # MySQL bulk logging
│   ├── main_window.py       # Dashboard PyQt6
│   ├── config/
│   │   └── config_loader.py
│   └── db/
│       └── database_logger.py
└── dist/
    └── AudiDiag.exe         # Executável standalone
```

## Solução de Problemas

### "Acesso negado" na porta COM
- Feche outros programas a usar a porta (VCDS, PuTTY, Arduino IDE)
- Verifique no **Gerenciador de Dispositivos** → Portas (COM e LPT)

### Nenhum adaptador detetado
```cmd
python main.py --list-ports
```
- Instale drivers FTDI: https://ftdichip.com/drivers/vcp-drivers/
- Cabos baratos com chip CH340/CP2102 **não funcionam** (precisa FTDI)

### Erro de conexão MySQL
- Verifique se serviço MySQL/MariaDB está a correr: `services.msc`
- Teste: `mysql -h localhost -u audi_diag -p`

### Gauges não atualizam
- Ignição deve estar **ON** (painel aceso)
- Cabo bem encaixado na tomada OBD-2 (pino 7 = K-line)

## Licença

Uso educacional / pessoal. Não afiliado à Volkswagen AG / Audi AG.
