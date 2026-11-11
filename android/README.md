# Android Client

Experimental native Android client for USB OTG replay and KW1281 diagnostics.
Replay mode works without a cable and labels synthetic data as replay. Physical
mode uses `usb-serial-for-android` for FTDI, BREAK slow initialization, ECU
identification, and explicit measurement-group reads. Raw measurements are
shown without unverified sensor scaling.

## Build

Open this folder in Android Studio or run:

```shell
./gradlew testDebugUnitTest assembleDebug
```

The project uses Java 17 bytecode and Android Gradle Plugin configuration in
`app/build.gradle.kts`. The SDK must be installed and configured on the build
machine. Do not commit local SDK paths or generated APKs.

## Manual hardware checks

1. Connect the FTDI adapter through USB OTG and grant USB permission.
2. Open and close the adapter without starting ECU identification. Confirm the
   port state changes and no KW1281 traffic is sent.
3. With the vehicle parked and ignition on, explicitly identify the ECU.
4. After successful identification, explicitly request one supported group:
   001, 003, or 011.
5. Export and review a diagnostic trace before sharing it. Traces may contain
   vehicle identifiers such as a VIN; redact personal data first.

Physical validation with the target ECU and cable is still pending. Opening the
USB adapter alone does not test the vehicle connection.
