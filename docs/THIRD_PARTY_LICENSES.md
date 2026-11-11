# Third-Party Notices

## ESP32 KW1281 core

`firmware/esp32/src/fis_kw1281.c` adapts limited framing, initialization, and
measurement-request logic from [muki01/VAG_KW1281 at commit
1860b11a4c7052795dad0280ad54510c17252606](https://github.com/muki01/VAG_KW1281/tree/1860b11a4c7052795dad0280ad54510c17252606).
That upstream project is licensed under GPL-3.0. The corresponding license
text is included in `firmware/esp32/LICENSES/GPL-3.0.txt`. The ESP32 core and
its derivative portions are governed by that license.

## Android USB serial dependency

The Android app depends on
[`usb-serial-for-android`](https://github.com/mik3y/usb-serial-for-android),
version 3.11.0, under the MIT License. It is included as a Gradle dependency;
its source is not vendored here.

## Other references

- [`gmenounos/kw1281test`](https://github.com/gmenounos/kw1281test/tree/9067217adf7925791e7163b08ce5c7be45e527cf), MIT: protocol framing, timing, and generic sensor-value reference. No source code was copied into this project.
- [`mnaberez/vwradio`](https://github.com/mnaberez/vwradio): KW1281 session reference. No source code was copied.
- `muki01/OBD2_K-line_Reader`, `OBD2_CAN_Bus_Reader`, `OBD2_KLine_Library`, and other projects were reviewed as references; their code is not included, except the explicitly identified ESP32 adaptation above.

Proprietary VCDS label archives are not included. The project uses no bundled
Ross-Tech label files.
