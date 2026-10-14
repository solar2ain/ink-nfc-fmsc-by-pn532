# Test Images

[中文](README.md) | [English](README.en.md)

`examples/` contains ready-to-use sample images. `gooddisplay/` contains 30 original samples (16 BMP and 14 PNG), all in the top-level directory.

Use `prepare` to inspect scale, colors and orientation before upload. Mismatched dimensions are center-cropped by default. Prepare a correctly sized image when its complete composition must be preserved.

```sh
python -m ink_nfc_fmsc prepare assets/examples/mountain-journey-400x300.png --screen SSD1683-400x300-BWR --output output/preview
```

Images do not contain panel drivers and cannot identify unknown initialization or wiring requirements.

## Good Display Samples

Images come from [Good Display's official download](https://www.good-display.com/companyfile/29/) and retain their original content. `bw` indicates black/white samples, `r` black/white/red, and `f` four-color. Filenames do not replace checking the panel model and colors.

`COMxx` in the command is a Windows serial-port placeholder. Replace it with the actual port reported by `python -m ink_nfc_fmsc ports`; use the corresponding device path on macOS or Linux.

```sh
python -m ink_nfc_fmsc send assets/gooddisplay/f420.bmp --screen JD79668AA-400x300-BWRY --port COMxx
```

Images retain their original rights. They are not covered by this project's GPL license, and no separate sublicensing rights are claimed. Sources, original filenames, dimensions and SHA-256 hashes are recorded in the [image manifest](gooddisplay-manifest.json).
