"""screen reference processing API followed by the FMSC-specific pixel layouts."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .image_encoding import RgbaFrame
from .image_processing import ImageProcessOptions, compose_image, process_frame

BLACK, WHITE, RED, YELLOW = (0, 0, 0), (255, 255, 255), (220, 48, 38), (235, 194, 25)
GREEN, BLUE = (19, 116, 99), (44, 97, 165)


def palette(screen):
    return {"bw": [BLACK, WHITE], "bwr": [BLACK, WHITE, RED],
            "bwry": [BLACK, WHITE, YELLOW, RED], "e6": [BLACK, WHITE, RED, YELLOW, GREEN, BLUE],
            "gray4": [BLACK, (85, 85, 85), (170, 170, 170), WHITE]}[screen.color]


def test_pattern(screen):
    """Landscape color bands and checkerboard, with asymmetric orientation marks."""
    screen.require_supported()
    _, _, width, height = screen.visible
    width, height = max(width, height), min(width, height)
    colors = palette(screen)
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"
    header = min(28, height // 6)
    bottom = height * 2 // 3
    for index, color in enumerate(colors):
        left, right = index * width // len(colors), (index + 1) * width // len(colors)
        draw.rectangle((left, header, right - 1, bottom - 1), fill=color)
    cell = max(1, min(width // 16, (height - bottom) // 4))
    for y in range(bottom, height, cell):
        for x in range(0, width, cell):
            color = colors[(x // cell + (y - bottom) // cell) % len(colors)]
            draw.rectangle((x, y, min(width - 1, x + cell - 1), min(height - 1, y + cell - 1)), fill=color)
    label = f"INK NFC  {width}x{height}"
    mark = max(1, min(6, header // 2))
    if header >= 12 and draw.textbbox((0, 0), label)[2] + mark + 8 <= width:
        draw.text((mark + 4, 3), label, fill=BLACK)
    draw.rectangle((0, 0, mark - 1, mark - 1), fill=BLACK)
    return image


def pack_indices(indices, screen):
    p = screen.value
    width, height, fmt = p["width"], p["height"], p["packing"]
    if len(indices) != width * height or any(i >= len(palette(screen)) for i in indices):
        raise ValueError("Indexed pixels do not fit selected screen")
    if fmt == "huawei_2bpp":
        scan = [indices[y * width + x] for x in range(width - 1, -1, -1) for y in range(height)]
        raw = bytes((scan[i] << 6) | (scan[i + 1] << 4) | (scan[i + 2] << 2) | scan[i + 3]
                    for i in range(0, len(scan), 4))
        if p.get("row_mapping") == "a0_interleaved":
            mapped = bytearray(len(raw))
            for row in range(552):
                target = row * 2 if row < 276 else 1103 - row * 2
                mapped[target * 192:(target + 1) * 192] = raw[row * 192:(row + 1) * 192]
            raw = bytes(mapped)
        return (raw,)
    if p.get("reverse_rows"):
        indices = b"".join(indices[y * width:(y + 1) * width] for y in range(height - 1, -1, -1))
    if fmt in ("bw", "bwr", "bwr_inverted"):
        stride = (width + 7) // 8
        planes = [bytearray(b"\xff" * p["plane_bytes"]) for _ in range(p["planes"])]
        for y in range(height):
            for x in range(width):
                code, offset, bit = indices[y * width + x], y * stride + x // 8, 0x80 >> (x % 8)
                if code == 0 or fmt == "bwr" and code != 1:
                    planes[0][offset] &= ~bit
                if len(planes) == 2 and code == 2:
                    planes[1][offset] &= ~bit
        if fmt == "bwr_inverted":
            planes = [bytearray(v ^ 255 for v in plane) for plane in planes]
    elif fmt == "gray4_planes":
        planes = [bytearray(p["plane_bytes"]) for _ in range(2)]
        stride = (width + 7) // 8
        for y in range(height):
            for x in range(width):
                code, offset, bit = indices[y * width + x], y * stride + x // 8, 0x80 >> (x % 8)
                for plane in range(2):
                    if not code & (1 << plane):
                        planes[plane][offset] |= bit
    elif fmt == "indexed_2bpp":
        codes = [3 if p["color"] == 3 and i == 2 else i for i in indices]
        planes = [bytearray((codes[i] << 6) | (codes[i + 1] << 4) | (codes[i + 2] << 2) | codes[i + 3]
                            for i in range(0, len(codes), 4))]
    elif fmt == "indexed_4bpp":
        codes = [ (0, 1, 3, 2, 6, 5)[i] for i in indices]
        planes = [bytearray((codes[i] << 4) | codes[i + 1] for i in range(0, len(codes), 2))]
    else:
        raise ValueError(f"Packing {fmt} has no enabled NFC encoder")
    for plane, raw in enumerate(planes):
        if p.get("invert_planes", 0) & (1 << plane):
            planes[plane] = bytearray(v ^ 255 for v in raw)
    result = tuple(bytes(plane) for plane in planes)
    if any(len(plane) != p["plane_bytes"] for plane in result):
        raise ValueError("Native image length mismatch")
    return result


def prepare_image(screen, image=None, *, white=False, rotation="auto", fit="crop",
                  scale=1.0, pan_x=0.0, pan_y=0.0, mirror_x=False, mirror_y=False,
                  options=None, native_palette=False):
    screen.require_supported()
    p = screen.value
    x, y, w, h = screen.visible
    colors = palette(screen)
    white_index = len(colors) - 1 if screen.color == "gray4" else 1
    if white:
        indices = bytes([white_index]) * (p["width"] * p["height"])
        preview = Image.new("RGB", (p["width"], p["height"]), WHITE)
    else:
        if image is None:
            raise ValueError("An image path is required; use clear for white")
        source = image if isinstance(image, Image.Image) else Path(image)
        composed = compose_image(source, w, h, rotation=rotation, fit=fit, scale=scale, pan_x=pan_x, pan_y=pan_y)
        if native_palette:
            # Diagnostic colors must retain every native color, without photographic correction.
            color_table = Image.new("P", (1, 1))
            color_table.putpalette([channel for color in colors for channel in color] + list(colors[0]) * (256 - len(colors)))
            image_data = Image.frombytes("RGBA", (w, h), composed.frame.data).convert("RGB")
            image_data = image_data.quantize(palette=color_table, dither=Image.Dither.NONE).convert("RGB")
        else:
            color_mode = {"bw": 1, "bwr": 2, "bwry": 3, "gray4": 4, "e6": 10}[screen.color]
            processed = process_frame(composed.frame, color_mode, options or ImageProcessOptions(), coverage=composed.coverage)
            image_data = Image.frombytes("RGBA", (w, h), processed.data).convert("RGB")
        if mirror_x:
            image_data = ImageOps.mirror(image_data)
        if mirror_y:
            image_data = ImageOps.flip(image_data)
        preview = Image.new("RGB", (p["width"], p["height"]), WHITE)
        preview.paste(image_data, (x, y))
        rgb = preview.tobytes()
        lookup = {color: i for i, color in enumerate(colors)}
        try:
            indices = bytes(lookup[tuple(rgb[i:i + 3])] for i in range(0, len(rgb), 3))
        except KeyError as exc:
            raise ValueError("screen reference engine produced a color outside the native palette") from exc
    return pack_indices(indices, screen), preview
