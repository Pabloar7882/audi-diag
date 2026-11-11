# Product Overview

## Audience and purpose

An experimental, read-only diagnostics and telemetry project for a 1999 Audi
A4 B5 Avant 1.9 TDI AFN (ECU 038 906 018 FD). It provides a native Windows
client, an Android USB client, an ESP32 protocol core, and a web dashboard for
replay and demonstration.

## Current limits

- Live connection with the target ECU has not been verified.
- Baud rate, physical timing, sensor scaling, Android OTG, and ESP32 hardware
  remain unvalidated.
- Software tests and simulated ECU responses are not evidence of vehicle
  compatibility.
- Diagnostic features are restricted to ECU identification and explicit
  measurement requests. No coding, adaptation, clearing, actuator tests, or
  memory writing are supported.
- Preserve third-party attribution and licensing notices when redistributing.
