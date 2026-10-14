# Example Image

[中文](README.md) | [English](README.en.md)

`mountain-journey-400x300.png` is a 400×300 black/white/red example for checking layout, orientation and colors. Its original pixels are unchanged and it contains no driver code.

`COMxx` in the command is a Windows serial-port placeholder. Replace it with the actual port reported by `python -m ink_nfc_fmsc ports`; use the corresponding device path on macOS or Linux.

```sh
python -m ink_nfc_fmsc send assets/examples/mountain-journey-400x300.png --screen SSD1683-400x300-BWR --port COMxx
```
