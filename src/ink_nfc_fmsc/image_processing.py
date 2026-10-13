from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .image_encoding import RgbaFrame


@dataclass(frozen=True)
class ImageAlgorithm:
    index: int
    name: str
    title: str
    group: str
    direct_strength: bool


IMAGE_ALGORITHMS = (
    ImageAlgorithm(1, "auto", "Hybrid adaptive", "common", True),
    ImageAlgorithm(2, "floydSteinberg", "Floyd-Steinberg", "common", True),
    ImageAlgorithm(3, "atkinson", "Atkinson", "common", True),
    ImageAlgorithm(4, "bayer", "Bayer ordered", "common", True),
    ImageAlgorithm(5, "none", "Palette quantization only", "common", False),
    ImageAlgorithm(6, "stucki", "Stucki", "error diffusion", True),
    ImageAlgorithm(7, "burkes", "Burkes", "error diffusion", True),
    ImageAlgorithm(8, "jarvis", "Jarvis-Judice-Ninke", "error diffusion", True),
    ImageAlgorithm(9, "sierra3", "Sierra 3 (experimental)", "error diffusion", False),
    ImageAlgorithm(10, "sierra2", "Two-row Sierra", "error diffusion", True),
    ImageAlgorithm(11, "sierra2-4a", "Sierra Lite", "error diffusion", True),
    ImageAlgorithm(12, "linearLightSierra", "Linear-light Sierra", "error diffusion", True),
    ImageAlgorithm(13, "blueNoise", "Blue Noise", "spatial", True),
    ImageAlgorithm(14, "riemersma", "Riemersma", "spatial", True),
    ImageAlgorithm(15, "simple2D", "Simple 2D (experimental)", "spatial", False),
    ImageAlgorithm(16, "legacyOstromoukhov", "Ostromoukhov three-point", "spatial", True),
)

PROCESSING_PRESETS = (
    "balanced",
    "dynamic",
    "vivid",
    "soft",
    "grayscale",
    "restore",
    "posterScan",
)

_ALIASES = {
    "hybrid": "auto",
    "legacyhybrid": "auto",
    "floyd": "floydSteinberg",
    "fs": "floydSteinberg",
    "floydsteinberg": "floydSteinberg",
    "sierralite": "sierra2-4a",
    "tworowsierra": "sierra2",
    "linearlightsierra": "linearLightSierra",
    "ostromoukhov": "legacyOstromoukhov",
    "legacyostromoukhov": "legacyOstromoukhov",
    "bluenoise": "blueNoise",
    "simple2d": "simple2D",
}


@dataclass(frozen=True)
class ImageProcessOptions:
    algorithm: str = "auto"
    preset: str = "balanced"
    contrast: float = 1.0
    strength: float = 1.0
    heavy_ink: bool = False

    def validated(self) -> "ImageProcessOptions":
        algorithm = resolve_algorithm(self.algorithm)
        if self.preset not in PROCESSING_PRESETS:
            raise ValueError(f"unknown processing preset: {self.preset}")
        if not 0.5 <= self.contrast <= 2.0:
            raise ValueError("contrast must be between 0.5 and 2.0")
        maximum = 5.0 if algorithm.direct_strength else 1.0
        if not 0.0 <= self.strength <= maximum:
            raise ValueError(
                f"strength for {algorithm.name} must be between 0 and {maximum:g}"
            )
        return ImageProcessOptions(
            algorithm=algorithm.name,
            preset=self.preset,
            contrast=float(self.contrast),
            strength=float(self.strength),
            heavy_ink=bool(self.heavy_ink),
        )


@dataclass(frozen=True)
class ComposedImage:
    frame: RgbaFrame
    coverage: bytes
    rotation: int


def resolve_algorithm(value: str | int) -> ImageAlgorithm:
    if isinstance(value, int) or str(value).strip().isdigit():
        index = int(value)
        for algorithm in IMAGE_ALGORITHMS:
            if algorithm.index == index:
                return algorithm
        raise ValueError(f"algorithm number must be in 1..{len(IMAGE_ALGORITHMS)}")
    raw = str(value).strip()
    normalized = re.sub(r"[^a-zA-Z0-9]", "", raw).lower()
    name = _ALIASES.get(normalized)
    if name is None:
        for algorithm in IMAGE_ALGORITHMS:
            if re.sub(r"[^a-zA-Z0-9]", "", algorithm.name).lower() == normalized:
                return algorithm
        raise ValueError(f"unknown image algorithm: {value}")
    return next(algorithm for algorithm in IMAGE_ALGORITHMS if algorithm.name == name)


def compose_image(
    path: Path,
    width: int,
    height: int,
    *,
    fit: str = "crop",
    rotation: str | int = "auto",
    scale: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> ComposedImage:
    if fit not in {"crop", "stretch"}:
        raise ValueError("fit must be crop or stretch")
    if not 0.25 <= scale <= 6.0:
        raise ValueError("scale must be between 0.25 and 6")
    if width < 1 or height < 1:
        raise ValueError("target dimensions must be positive")

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - packaging dependency guard
        raise RuntimeError("Pillow is required for image input and preview output") from exc

    if isinstance(path, Image.Image):
        source = ImageOps.exif_transpose(path).convert("RGBA")
    else:
        with Image.open(path) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGBA")
    resolved_rotation = _resolve_rotation(rotation, source.width, source.height, width, height)
    quarter_turn = resolved_rotation in {90, 270}
    rotated_width = source.height if quarter_turn else source.width
    rotated_height = source.width if quarter_turn else source.height
    if fit == "crop":
        factor = max(width / rotated_width, height / rotated_height) * scale
        draw_width = max(1, round(source.width * factor))
        draw_height = max(1, round(source.height * factor))
    else:
        draw_width = max(1, round((height if quarter_turn else width) * scale))
        draw_height = max(1, round((width if quarter_turn else height) * scale))

    resized = source.resize((draw_width, draw_height), Image.Resampling.BILINEAR)
    if resolved_rotation == 90:
        rendered = resized.transpose(Image.Transpose.ROTATE_270)
    elif resolved_rotation == 180:
        rendered = resized.transpose(Image.Transpose.ROTATE_180)
    elif resolved_rotation == 270:
        rendered = resized.transpose(Image.Transpose.ROTATE_90)
    else:
        rendered = resized

    left = round(width / 2 + pan_x - rendered.width / 2)
    top = round(height / 2 + pan_y - rendered.height / 2)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    canvas.alpha_composite(rendered, (left, top))
    mask = Image.new("L", (width, height), 0)
    mask.paste(rendered.getchannel("A"), (left, top))
    return ComposedImage(
        frame=RgbaFrame(width, height, canvas.tobytes()),
        coverage=mask.tobytes(),
        rotation=resolved_rotation,
    )


def process_frame(
    frame: RgbaFrame,
    color: int,
    options: ImageProcessOptions,
    *,
    coverage: bytes | None = None,
) -> RgbaFrame:
    validated = options.validated()
    if color not in set(range(1, 12)):
        raise ValueError("screen color must be in 1..11")
    if coverage is not None and len(coverage) != frame.width * frame.height:
        raise ValueError("coverage mask does not match image dimensions")
    request = {
        "width": frame.width,
        "height": frame.height,
        "data": base64.b64encode(frame.data).decode("ascii"),
        "coverage": base64.b64encode(coverage).decode("ascii") if coverage is not None else None,
        "color": color,
        "algorithm": validated.algorithm,
        "preset": validated.preset,
        "contrast": validated.contrast,
        "strength": validated.strength,
        "heavyInk": validated.heavy_ink,
    }
    result = _run_javascript_processor(request)
    output = base64.b64decode(result["data"], validate=True)
    return RgbaFrame(int(result["width"]), int(result["height"]), output)


def save_png(frame: RgbaFrame, path: Path) -> None:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - packaging dependency guard
        raise RuntimeError("Pillow is required for image input and preview output") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGBA", (frame.width, frame.height), frame.data).save(path, format="PNG")


def _resolve_rotation(
    value: str | int,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> int:
    if str(value).lower() == "auto":
        if source_width == source_height or target_width == target_height:
            return 0
        return 0 if (source_height > source_width) == (target_height > target_width) else 90
    try:
        rotation = int(value) % 360
    except ValueError as exc:
        raise ValueError("rotation must be auto, 0, 90, 180 or 270") from exc
    if rotation not in {0, 90, 180, 270}:
        raise ValueError("rotation must be auto, 0, 90, 180 or 270")
    return rotation


def _run_javascript_processor(request: dict[str, Any]) -> dict[str, Any]:
    try:
        from py_mini_racer import MiniRacer
    except ImportError as exc:  # pragma: no cover - packaging dependency guard
        raise RuntimeError("mini-racer is required for WebUI-compatible image algorithms") from exc

    assets = files("ink_nfc_fmsc._assets")
    legacy_source = assets.joinpath("legacyDithering.js").read_text(encoding="utf-8")
    legacy_source = legacy_source.rsplit("export {", 1)[0]
    epd_source = assets.joinpath("epdoptimize.cjs").read_text(encoding="utf-8")
    context = MiniRacer()
    context.set_hard_memory_limit(512 * 1024 * 1024)
    try:
        context.eval("var exports = {};" + epd_source + "\n; globalThis.__inkNfcEpdExports = exports;")
        context.eval(legacy_source)
        context.eval(_PROCESSOR_JAVASCRIPT)
        raw_request = json.dumps(request, separators=(",", ":"))
        promise = context.eval(
            f"inkNfcProcessImage({json.dumps(raw_request)})",
            timeout_sec=300,
        )
        result = json.loads(promise.get(timeout=300))
    except Exception as exc:
        raise RuntimeError(f"embedded image runtime failed: {exc}") from exc
    finally:
        context.close()
    if not result.get("ok"):
        raise RuntimeError(f"image processor failed: {result.get('error', 'unknown error')}")
    return result


_PROCESSOR_JAVASCRIPT = r"""
const epdExports = globalThis.__inkNfcEpdExports;
globalThis.ImageData = globalThis.ImageData || class ImageData {
  constructor(data, width, height) { this.data = data; this.width = width; this.height = height; }
};

const inkNfcB64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
function inkNfcDecodeBase64(value) {
  const clean = value.replace(/=+$/, "");
  const output = new Uint8ClampedArray(Math.floor(clean.length * 3 / 4));
  let buffer = 0, bits = 0, offset = 0;
  for (let index = 0; index < clean.length; index++) {
    const digit = inkNfcB64.indexOf(clean[index]);
    if (digit < 0) continue;
    buffer = (buffer << 6) | digit;
    bits += 6;
    if (bits >= 8) { bits -= 8; output[offset++] = (buffer >> bits) & 255; }
  }
  return output.subarray(0, offset);
}
function inkNfcEncodeBase64(bytes) {
  let output = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const a = bytes[index], b = index + 1 < bytes.length ? bytes[index + 1] : 0;
    const c = index + 2 < bytes.length ? bytes[index + 2] : 0;
    output += inkNfcB64[a >> 2];
    output += inkNfcB64[((a & 3) << 4) | (b >> 4)];
    output += index + 1 < bytes.length ? inkNfcB64[((b & 15) << 2) | (c >> 6)] : "=";
    output += index + 2 < bytes.length ? inkNfcB64[c & 63] : "=";
  }
  return output;
}
function inkNfcHex(rgb) { return "#" + rgb.map(value => value.toString(16).padStart(2, "0")).join(""); }
function inkNfcPalette(color) {
  const black = { name: "black", native: [0,0,0], preview: [30,30,30] };
  const white = { name: "white", native: [255,255,255], preview: [220,215,205] };
  const red = { name: "red", native: [220,48,38], preview: [180,50,50] };
  const yellow = { name: "yellow", native: [235,194,25], preview: [200,195,60] };
  const blue = { name: "blue", native: [44,97,165], preview: [30,40,140] };
  const green = { name: "green", native: [19,116,99], preview: [35,140,35] };
  const orange = { name: "orange", native: [225,139,35], preview: [190,90,35] };
  if (color === 2) return [black, white, red];
  if (color === 3) return [black, white, red, yellow];
  if (color === 4) return [
    { name: "black", native: [0,0,0], preview: [0,0,0] },
    { name: "gray1", native: [85,85,85], preview: [130,130,130] },
    { name: "gray2", native: [170,170,170], preview: [205,205,205] },
    { name: "white", native: [255,255,255], preview: [255,255,255] },
  ];
  if (color === 5) return [black, white, yellow];
  if (color === 6) return [black, white, blue];
  if (color === 7) return [black, white, green];
  if (color === 8) return [black, white, orange];
  if (color === 9) return Array.from({ length: 16 }, (_, index) => {
    const level = Math.round(index * 255 / 15);
    return { name: "gray" + index, native: [level,level,level], preview: [level,level,level] };
  });
  if (color === 10) return [black, white, red, yellow, green, blue];
  if (color === 11) return [black, white, red, yellow, green, blue, orange];
  return [black, white];
}
function inkNfcEpdPalette(palette) {
  return palette.map(entry => ({ name: entry.name, color: inkNfcHex(entry.preview), deviceColor: inkNfcHex(entry.native) }));
}
function inkNfcRestoreUncovered(image, coverage, color) {
  if (!coverage) return;
  for (let pixel = 0; pixel < coverage.length; pixel++) {
    if (coverage[pixel] !== 0) continue;
    const offset = pixel * 4;
    image.data[offset] = color[0]; image.data[offset + 1] = color[1];
    image.data[offset + 2] = color[2]; image.data[offset + 3] = 255;
  }
}
function inkNfcCanvas(image) {
  let current = { width: image.width, height: image.height, data: new Uint8ClampedArray(image.data) };
  return {
    width: image.width, height: image.height,
    getContext() { return {
      getImageData() { return { width: current.width, height: current.height, data: new Uint8ClampedArray(current.data) }; },
      putImageData(next) { current = { width: next.width, height: next.height, data: new Uint8ClampedArray(next.data) }; },
    }; },
    inkNfcImage() { return current; },
  };
}
function inkNfcLegacyMode(color) {
  if (color === 1) return "blackWhiteColor";
  if (color === 2) return "threeColor";
  if (color === 3) return "fourColor";
  if (color === 4) return "gray4Color";
  if (color === 10) return "sixColor";
  return null;
}
function inkNfcLegacyAlgorithm(algorithm) {
  if (algorithm === "auto") return "hybrid";
  if (algorithm === "legacyOstromoukhov") return "ostromoukhov";
  if (algorithm === "sierra2") return "twoRowSierra";
  if (algorithm === "sierra2-4a") return "sierraLite";
  return algorithm;
}
function inkNfcLegacyStrength(algorithm, strength) {
  const clamped = Math.max(0, Math.min(5, strength));
  if (algorithm !== "auto" || clamped <= 1) return clamped;
  return 1 + .65 * (1 - Math.exp(-(clamped - 1) / 1.5));
}
function inkNfcLegacyPaletteIndex(r, g, b, mode) {
  if (mode === "gray4Color") {
    const value = (r + g + b) / 3;
    if (value < 65) return 0; if (value < 168) return 1; if (value < 230) return 2; return 3;
  }
  if (r < 80 && g < 80 && b < 80) return 0;
  if (r > 220 && g > 220 && b > 220) return 1;
  if (mode === "threeColor") return 2;
  if (mode === "fourColor") return g < 128 ? 2 : 3;
  if (r > 180 && g < 100 && b < 100) return 2;
  if (r > 180 && g > 150 && b < 100) return 3;
  if (g > r && g > b) return 4;
  return 5;
}
function inkNfcMapLegacyNative(image, palette, mode) {
  const output = new Uint8ClampedArray(image.data.length);
  for (let offset = 0; offset < image.data.length; offset += 4) {
    const selected = palette[inkNfcLegacyPaletteIndex(image.data[offset], image.data[offset + 1], image.data[offset + 2], mode)];
    output[offset] = selected.native[0]; output[offset + 1] = selected.native[1];
    output[offset + 2] = selected.native[2]; output[offset + 3] = 255;
  }
  return { width: image.width, height: image.height, data: output };
}
function inkNfcAlgorithmOptions(algorithm) {
  if (algorithm === "auto") return { ditheringType: "errorDiffusion", errorDiffusionMatrix: "floydSteinberg", serpentine: true };
  if (algorithm === "none") return { ditheringType: "quantizationOnly" };
  if (algorithm === "bayer") return { ditheringType: "ordered", orderedDitheringType: "bayer", orderedDitheringMatrix: [8,8] };
  if (algorithm === "blueNoise") return { ditheringType: "blueNoise" };
  if (algorithm === "riemersma") return { ditheringType: "riemersma" };
  if (algorithm === "simple2D") return { ditheringType: "simple2D" };
  return { ditheringType: "errorDiffusion", errorDiffusionMatrix: algorithm, serpentine: true };
}
const inkNfcStrengthPattern = [
  0,32,8,40,2,34,10,42,48,16,56,24,50,18,58,26,12,44,4,36,14,46,6,38,60,28,52,20,62,30,54,22,
  3,35,11,43,1,33,9,41,51,19,59,27,49,17,57,25,15,47,7,39,13,45,5,37,63,31,55,23,61,29,53,21,
];
function inkNfcMixStrength(dithered, quantized, width, strength) {
  const amount = Math.max(0, Math.min(1, strength));
  for (let pixel = 0; pixel < dithered.length / 4; pixel++) {
    const x = pixel % width, y = Math.floor(pixel / width);
    if (amount >= (inkNfcStrengthPattern[(y & 7) * 8 + (x & 7)] + 1) / 64) continue;
    const offset = pixel * 4;
    dithered.set(quantized.subarray(offset, offset + 4), offset);
  }
}
async function inkNfcRunImageProcess(config) {
  const palette = inkNfcPalette(config.color), epdPalette = inkNfcEpdPalette(palette);
  const coverage = config.coverage ? inkNfcDecodeBase64(config.coverage) : null;
  let image = { width: config.width, height: config.height, data: inkNfcDecodeBase64(config.data) };
  if (config.preset !== "balanced") {
    image = epdExports.applyImageDataAdjustments(image, { palette: epdPalette, processingPreset: config.preset, adjustmentEngine: "js" }) || image;
  }
  adjustContrast(image, config.contrast);
  const white = palette.find(entry => entry.name === "white") || palette[palette.length - 1];
  inkNfcRestoreUncovered(image, coverage, [255,255,255]);
  const mode = inkNfcLegacyMode(config.color);
  const direct = mode && config.algorithm !== "sierra3" && config.algorithm !== "simple2D";
  let output;
  if (direct) {
    const dithered = ditherImage(image, inkNfcLegacyAlgorithm(config.algorithm), inkNfcLegacyStrength(config.algorithm, config.strength), mode, { heavyInk: config.heavyInk });
    output = inkNfcMapLegacyNative(decodeProcessedData(processImageData(dithered, mode), image.width, image.height, mode), palette, mode);
  } else {
    const source = inkNfcCanvas(image), preview = inkNfcCanvas(image), device = inkNfcCanvas(image);
    const options = Object.assign({
      palette: epdPalette,
      colorMatching: config.color >= 4 ? "lab" : "chroma",
      processingEngine: "js",
      toneMapping: config.heavyInk
        ? { mode: "scurve", contrast: Math.max(.15, config.contrast - 1), saturation: .2, strength: .75, shadowBoost: -.08 }
        : { mode: "off" },
      dynamicRangeCompression: { mode: "off" },
      edgePreservation: { enabled: true, strength: config.algorithm === "bayer" ? .85 : .55, threshold: .16, radius: 1 },
    }, inkNfcAlgorithmOptions(config.algorithm));
    await epdExports.ditherImage(source, preview, options);
    if (config.algorithm !== "none" && config.strength < 1) {
      const quantized = inkNfcCanvas(image);
      await epdExports.ditherImage(source, quantized, Object.assign({}, options, { ditheringType: "quantizationOnly" }));
      inkNfcMixStrength(preview.inkNfcImage().data, quantized.inkNfcImage().data, image.width, config.strength);
    }
    epdExports.replaceColors(preview, device, epdPalette);
    output = device.inkNfcImage();
  }
  inkNfcRestoreUncovered(output, coverage, white.native);
  return { ok: true, width: output.width, height: output.height, data: inkNfcEncodeBase64(output.data) };
}
function inkNfcProcessImage(raw) {
  return inkNfcRunImageProcess(JSON.parse(raw)).then(value => JSON.stringify(value));
}
globalThis.__inkNfcImageResult = null;
function inkNfcStartImageProcess(raw) {
  globalThis.__inkNfcImageResult = null;
  Promise.resolve().then(() => inkNfcRunImageProcess(JSON.parse(raw))).then(
    value => { globalThis.__inkNfcImageResult = JSON.stringify(value); },
    error => { globalThis.__inkNfcImageResult = JSON.stringify({ ok: false, error: String(error) + "\n" + String(error && error.stack || "") }); },
  );
}
"""
