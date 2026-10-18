# Release Notes

[中文](RELEASE_NOTES.md) | [English](RELEASE_NOTES.en.md)

## 0.1.21 · 2026-10-19

- Added Huawei A1.1: `JD79665AA-552x768-BWRY-A1.1`, alias `huawei-a1.1`.
- Added the 768x600 controller layout and full window. Physical 552x768 images are padded automatically with 48 white rows; no manual resizing is required.
- Supports image upload, test patterns, solid colors, clearing and stored-image refresh. A full frame occupies 115200 bytes. Insufficient capacity stops the operation without automatic splitting.

A1 and A1.1 are not interchangeable, including when using a stored driver. Existing panel recipes are unchanged. Experimental power tuning is not included. Complete refresh still depends on power delivery, coupling and panel condition. Installing a panel configuration overwrites factory information that cannot be restored.

```sh
python -m ink_nfc_fmsc send-test --screen huawei-a1.1 --port COMxx
```

`COMxx` is a Windows port placeholder; replace it with the actual port. On macOS and Linux, use the corresponding device path.
