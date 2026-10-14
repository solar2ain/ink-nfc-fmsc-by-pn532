# Advanced Use

[中文](advanced-use.md) | [English](advanced-use.en.md)

## Connection and Selection

`COMxx` in the examples is a Windows serial-port placeholder. Replace it with the actual port reported by `python -m ink_nfc_fmsc ports`; use the corresponding device path on macOS or Linux.

`probe` reads board identity and access state without authentication, configuration or upload. `prepare` creates a local preview without opening a serial port.

`--screen` selects a complete recipe. Filters such as `--ic SSD1683 --resolution 400x300 --color bwr` are also supported. Multiple matches require an explicit panel name. Changing dimensions alone cannot adapt a different panel.

`--board auto` tries `26`, `52`, `7A`, `7D` and validates board information. `--board huawei` restricts activation to Huawei Nova boards; `--board iphone` remains a compatibility parameter for standard-wake FMSC boards.

## Image Processing

There are 16 algorithms and seven presets; list them with `algorithms`. `--strength` adjusts dithering, `--heavy-ink` emphasizes dark colors, and `--contrast` adjusts contrast.

`--scale` controls scale; `--pan-x` and `--pan-y` adjust crop position; `--mirror-x` and `--mirror-y` mirror the image. Rotation accepts `auto`, `0`, `90`, `180`, `270`; fit defaults to `crop` and also supports `stretch`.

Check the local `prepare` preview before uploading.

## Timing and Transfer

| Option | Default | Purpose |
|---|---|---|
| `--precharge-ms` | 1000 | Charge wait after connection |
| `--packet-delay-ms` | 0 | Gap between image packets |
| `--pre-refresh-ms` | 0 | Wait before the refresh command |
| `--first-de-delay-ms` | 0 | Wait before the first status query |
| `--de-hz` | 9 | Target query frequency, limited by transport latency |
| `--rf-timeout-ms` | 1000 | RF exchange waiting time |
| `--d4-timeout` | 8 | Refresh-command response deadline in seconds, not waveform duration |
| `--de-timeout` | 5 | Per-query DE response deadline in seconds, 2..60, including WTX waits |
| `--timeout` | Panel-specific | Refresh observation limit in seconds |

A DE deadline or transport error stops the operation without replaying D4. WTX requests are logged and show waiting progress; they do not reset the query deadline.

D3 + LZO compression is the default. `--no-compress` is equivalent to `--transport d2`; do not combine them. `--transport auto` uses the recipe's path. `--compression literal` is still D3, not raw D2.

Compression cannot expand storage. Undeclared D3 support stops the operation before writes, without automatic D2 fallback. D2 is limited to 64000 bytes per slot; A0/A1 full frames and half-screen recipes cannot use D2.

## A0 / A1 Segmented Refresh

```sh
python -m ink_nfc_fmsc send picture.png --screen huawei-a1 --board iphone --passes two --port COMxx
```

`--passes auto` follows the capacity budget; `full` requires one full frame; `two` uses two halves; `first` and `second` refresh one half. Each half reuses slot 0 with 52992 decoded bytes. A failed first half stops the operation.

Segmentation does not guarantee lower peak power or retention of the other half. The final stored configuration and image belong to the last half, so `refresh` only repeats that stored half.

Default conservative budgets are 80000 bytes for standard-wake boards and 200000 for Huawei boards. These are not measured physical capacities. Raising `--capacity` does not expand storage. Other controllers have no generic split recipe.

## Configuration and Safety

Omitting `--screen` and a custom profile preserves the stored driver without DB/DA writes. The stored configuration must match the physical panel. Ambiguous image formats stop execution; narrow candidates using controller, resolution and color filters. `--data-format linear` only compares an existing A0 layout; it cannot identify A0/A1. `native` uses the native row mapping. Preserved drivers cannot be combined with segmented configuration.

`--uid` locks the board identity. `--pin` supplies a known Huawei PIN without guessing or repeated attempts. `--profile-file` imports a complete advanced recipe whose panel and wiring must be verified by the user.

`screens --json --details` exports a recipe. `id` is the public name; `recipe_id` is an internal identifier, not a selection name. Re-export recipes with the current version rather than relying on old internal fields.

For six-color panels, verify pin 16 and external circuitry before adding `--confirm-e6-wiring`. Do not reuse E6 wiring or A0/A1 segmentation for unrelated panels.
