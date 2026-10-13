# Components and Licensing

[中文](sources.md) | [English](sources.en.md)

The tool includes Python serial transport, image processing and panel recipes. Installed packages are self-contained and do not require another project checkout, firmware SDK, mobile app or Node.js.

## Third-Party Components

- `pyserial`: serial-port access.
- `Pillow`: image decoding, resizing and rotation.
- `lzokay`: LZO compression.
- `mini-racer`: execution of bundled image-processing scripts.
- `colorama`: Windows terminal-color compatibility.
- [`epdoptimize.cjs`](https://github.com/paperlesspaper/epdoptimize): dithering component with its original Apache-2.0 license and copyright notice.

Dependencies retain their respective licenses and copyright notices. The bundled license is at `src/ink_nfc_fmsc/_assets/EPDOPTIMIZE_LICENSE.txt`. Code is licensed under **GNU GPLv3 or any later version (GPL-3.0-or-later)**; see the root `LICENSE`.

SHA256 values for recipes, scripts and license files are stored in packaged `provenance.json` for integrity checks. Resource integrity does not prove hardware compatibility or a complete optical refresh.

Downloaded test images have separate copyright terms not covered by the code license. Verify the rights holder's permission before publication or redistribution.
