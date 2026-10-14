# Ink NFC FMSC by PN532

[中文](README.md) | [English](README.en.md)

## Overview

This tool supports **Mo You Yin Li Huawei Nova color-display phone-case NFC driver boards**, as well as other **FMSC passive e-paper NFC driver boards using a compatible protocol**, including those found in iPhone e-paper cases. PN532 or PCR532 UART readers can select panel drivers, process images, upload and refresh, clear to white, and refresh stored images.

It supports Windows, macOS and Linux with Python 3.11 or newer.

## Limitations

**NFC power delivery may be insufficient for higher-resolution panels to complete a refresh.** Reader hardware, antenna alignment, coil matching, stored energy and panel load all affect the result. In most cases, the board can be detected and image transfer completed, but this does not mean power delivery can sustain an e-paper refresh. Longer waits, compression and segmented refresh do not guarantee a solution.

**PN532 power delivery is currently insufficient to drive Huawei A0 and A1 panels reliably; starting a refresh can lead to a low-voltage disconnect.** Some panels using other controllers have been driven successfully, but the model, wiring and power conditions still need checking. The availability of A0/A1 driver recipes does not mean PN532 can complete their refresh. An `RF_TIMEOUT` alone is not proof that the chip lost power.

> [!CAUTION]
> **Changing panels and writing the corresponding driver configuration overwrites the board's factory panel settings and refresh sequence. This tool cannot restore them, and the factory information may be unrecoverable.** Selecting `--screen` or a custom profile for upload or clear writes the selected configuration. Proceed only if you accept this risk.

Decoded image storage is also limited. Compression does not increase this capacity. A0/A1 segmented refresh is experimental and does not guarantee that the other half remains unchanged. Panels cannot be identified automatically: verify the controller, resolution, colors and wiring. A catalog entry does not guarantee every reader/board/panel combination.

## Tested Range

Using a **PCR532** reader, the following driver board and tested panel combinations completed refreshes:

| Driver board | Tested working panel range |
|---|---|
| Huawei Nova e-paper phone-case NFC driver board | Tested panels at resolutions up to 400×300 |
| FMSC e-paper NFC driver board (iPhone e-paper case) | Tested panels up to 2.13 inches |

![E-paper case driver boards: Huawei Nova 14 Pro on the left, and two iPhone e-paper case driver boards in the center and on the right](assets/hardware/driver-boards-annotated.png)

From left to right: a Huawei Nova 14 Pro e-paper case driver board, followed by two iPhone e-paper case driver boards.

These results apply to the reader, boards, coils and panels tested, not every model of the same size or resolution. Larger panels have not refreshed reliably. Panel area, colors, waveforms and power conditions also affect the result.

## Installation and Connection

```sh
python -m pip install .
```

A wheel can be installed with `python -m pip install package.whl`. A separate Python virtual environment is recommended. Commands below use `python -m ink_nfc_fmsc`; `ink-nfc-fmsc` and `ink-nfc` are also available after installation.

Connect the reader and panel, then secure the board coil over the reader antenna. Do not move the coil or reconnect the panel during refresh. Only PN532-compatible UART readers are supported, not PC/SC readers or direct SPI/I2C connections.

```sh
python -m ink_nfc_fmsc ports
python -m ink_nfc_fmsc probe --port COMxx
```

Windows serial ports use `COM` followed by a port number, such as `COM3`. `COMxx` in these commands is a placeholder: replace it with the actual port name reported by `ports`. macOS commonly uses `/dev/cu.usbserial-...`; Linux uses `/dev/ttyUSB0` or `/dev/ttyACM0`. Linux users need serial-port permissions.

## Selecting a Panel

```sh
python -m ink_nfc_fmsc screens
python -m ink_nfc_fmsc screens --ic SSD1683
```

Names use **CONTROLLER-WIDTHxHEIGHT-COLOR**, case-insensitively. Only Huawei A0/A1 have variant suffixes. The table includes panel, aliases and status; empty aliases remain blank, and unavailable panels are omitted.

Only `huawei-a0`, `huawei-a1`, `osptek-e6-154`, `osptek-e6-169` and `osptek-e6-370` are retained as aliases. Use the full canonical name for other panels.

| Panel | Selection |
|---|---|
| SSD1683 black/white/red | `SSD1683-400x300-BWR` |
| Huawei A0 | `JD79665AA-552x768-BWRY-A0`, alias `huawei-a0` |
| Huawei A1 | `JD79665AA-552x768-BWRY-A1`, alias `huawei-a1` |
| SSD1675 black/white | `SSD1675-152x152-BW` |
| SSD1619 black/white/red | `SSD1619-128x250-BWR` |
| JD79668AA four-color | `JD79668AA-400x300-BWRY` |
| Osptek 1.54 E6 | `IST7513-240x240-E6`, alias `osptek-e6-154` |

Color codes are `BW`, `BWR`, `BWRY`, `GRAY4` and `E6`. Changing dimensions alone does not adapt an unknown panel.

## Upload, Clear and Refresh

```sh
python -m ink_nfc_fmsc send assets/examples/mountain-journey-400x300.png --screen SSD1683-400x300-BWR --port COMxx
python -m ink_nfc_fmsc send-test --screen SSD1683-400x300-BWR --port COMxx
python -m ink_nfc_fmsc send-color red --screen SSD1683-400x300-BWR --port COMxx
python -m ink_nfc_fmsc clear --screen SSD1683-400x300-BWR --port COMxx
python -m ink_nfc_fmsc refresh --screen SSD1683-400x300-BWR --port COMxx
```

`send` uploads and refreshes; `send-test` generates color blocks and a bottom checkerboard for the selected panel's resolution and colors, then uploads and refreshes without an image file; `send-color` fills the panel with a specified solid color without an image file; `clear` uploads a white image and refreshes, not a factory reset; `refresh` uses the stored image without uploading or changing the driver. `send-test` and `send-color` follow the same configuration confirmation, compression and segmented-refresh rules as `send`.

`send-color` color names are case-insensitive and separate from panel filters such as `--color BWR`. Solid fills use native panel codes without dithering or photographic processing; unsupported colors are rejected before writes. `send-color white` produces the same white image data as `clear`.

| Color name | Full-panel color | Supported panel color types |
|---|---|---|
| `black`, `white` | Black, white | All |
| `red` | Red | BWR, BWRY, E6 |
| `yellow` | Yellow | BWRY, E6 |
| `green`, `blue` | Green, blue | E6 |
| `dark-gray`, `light-gray` | Dark gray, light gray | GRAY4 |

Test patterns use a landscape layout by default, including portrait panel models. The pattern is automatically rotated into the native pixel format without changing the resolution in the driver configuration.

> [!CAUTION]
> Selecting `--screen` (or a custom profile) for upload or clear overwrites the board's factory panel configuration. This tool cannot restore the factory driver. A risk confirmation is shown before execution; verify the panel model and wiring.

Commands require manual confirmation by default. Add `--yes` for automation to skip the prompt; it does not reduce the risk and is not a PIN or compression setting.

Without `--screen` or a custom profile, upload and clear preserve the stored driver and never write DB/DA. The attached panel must match the stored configuration. The tool first reads the configuration and stops if the image format is ambiguous; use `--ic`, `--resolution` and `--color` to narrow the candidates. D1 cannot distinguish some equal-size panels or A0/A1, and cannot identify the physical panel.

```sh
python -m ink_nfc_fmsc send picture.png --port COMxx --ic SSD1683
python -m ink_nfc_fmsc clear --port COMxx --ic SSD1683
```

D3 + LZO compression is the default; `--no-compress` selects raw D2 transfer. PN532/PCR532 compatibility is automatic by default.

Board activation is automatic. Huawei Nova e-paper phone-case NFC driver boards use private wake frames and a single known-PIN verification when required. Standard-wake FMSC e-paper NFC driver boards do not receive a PIN. Explicit restrictions are available through `--board huawei` or the compatibility parameter `--board iphone`.

## Image Adjustments and Preview

```sh
python -m ink_nfc_fmsc prepare picture.png --screen SSD1683-400x300-BWR --output output/preview
python -m ink_nfc_fmsc algorithms
```

`prepare` creates a local preview without connecting the reader.

| Option | Effect |
|---|---|
| `--rotation 90` | Clockwise rotation; also 0, 180, 270 |
| `--fit stretch` | Fill by stretching; default is centered crop |
| `--algorithm atkinson` | Select a dithering algorithm |
| `--preset vivid` | Vivid preset; default is balanced |
| `--contrast 1.2` | Adjust contrast |
| `--mirror-x` | Horizontal mirror |

See [advanced use](docs/advanced-use.en.md) and [sample assets](assets/README.en.md).

## Status and Errors

Logs show time, stage and level: blue `INFO`, green `SUCCESS`, yellow `WARN`, red `ERROR`. File logs are plain text. Redirected output and `NO_COLOR=1` disable colors.

| Response | Meaning |
|---|---|
| `SW=9000` | Command accepted |
| `DE=01` / `DE=00` | Board reports busy / idle |
| `68CA` / `698A` | Board reports low voltage |
| `68CB` | Board reports a refresh timeout |
| `RF_TIMEOUT` | No valid response; not proof of a power failure |
| `6985` | Conditions not satisfied; authentication may be required |
| `6451` | Configuration-related error; inspect the recipe and detailed log |

Failures stop the operation without automatic authentication, write or refresh replay. Ctrl+C attempts to switch RF off and release the port. Refresh polling does not intentionally switch RF off.

After observing BUSY, three consecutive `DE=00` responses finish the wait without a fixed minimum duration. If BUSY was never observed, the minimum observation duration remains and the tool reports uncertainty. **An idle response still requires visual confirmation of the complete image.**

Operations create `run.log`, `summary.json`, and `preview.png` for uploads (including `send-test` and `send-color`). Include the log directory and actual panel behavior in a problem report.

For six-color panels, handle pin 16 independently. Do not assume pins 15 and 16 both connect to 3.3 V; add `--confirm-e6-wiring` only after verifying the wiring.

[Troubleshooting](docs/troubleshooting.en.md) · [Protocol and compatibility](docs/protocol.en.md) · [Components and licensing](docs/sources.en.md)


## Disclaimer

This tool is provided "as is", without any guarantee that it is suitable for a particular device or purpose.

> [!CAUTION]
> **Changing panels and writing the corresponding configuration overwrites the board's factory panel settings and refresh sequence. This tool cannot restore them, and the original data may be permanently lost.** Proceed with caution and verify the panel model, wiring and power conditions before operation. Do not proceed if you cannot accept these risks.

Use of this tool and any hardware modifications are at the user's own risk. To the extent permitted by applicable law, the authors and contributors are not liable for data loss, equipment damage, refresh failures or other losses arising from use of this tool.

## Licence

Code is licensed under [GNU GPLv3 or any later version](LICENSE). Third-party components and test images retain their own license terms.
