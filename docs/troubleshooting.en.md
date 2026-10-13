# Troubleshooting

[中文](troubleshooting.md) | [English](troubleshooting.en.md)

## Serial Port Unavailable

Run `ports`, verify the USB connection and close other applications using the port. Linux also requires serial access permissions.

## Detection Works but Refresh Fails

Secure the coil, reduce the gap and verify panel model and cable orientation. Panels, metal backplates and nearby antennas can affect coupling. Do not move the device during refresh.

NFC power delivery may not satisfy refresh load, particularly for high-resolution panels. A successful upload or high capacitor voltage alone does not prove sustained supply. Compression only reduces transfer volume; longer precharge and segmentation do not guarantee lower peak load.

`68CA` / `698A` are board-reported low-voltage states. `RF_TIMEOUT` only means no valid response and can involve coupling, communication or reader behavior; it is not equivalent to low voltage.

## Authentication or Configuration Errors

`6985` means conditions are not satisfied. Auto detection of a Huawei board permits one known-PIN attempt; there is no guessing or repeated retry. Verify board mode, UID and any PIN you set yourself.

Configuration errors require checking controller, resolution, colors and wiring. Do not change dimensions alone. Omitting `--screen` preserves the stored driver, which must match the physical panel. Use controller, resolution or color filters when matching is ambiguous. Selecting `--screen` replaces configuration and cannot restore the original driver.

## Idle Response but Incomplete Image

`DE=00` reports board state, not image verification. Inspect the actual panel, logged BUSY observations and possible communication or power interruption.

A failure stops the run without automatic resume or refresh replay. After improving coupling, restart manually and verify that replacing configuration or image is acceptable.

## Reporting a Problem

Include panel model, board type, reader model, actual panel behavior and the entire log directory. Preserve `run.log`, `summary.json` and `details/`, plus the preview when available. Custom PIN values are redacted in normal exported logs.
