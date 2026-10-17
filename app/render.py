"""Label layout: everything is rendered to 1-bit Pillow images, so preview == print."""

import math
import re
from functools import lru_cache

import segno
from PIL import Image, ImageDraw, ImageFont

from . import config

FONTS = {
    "regular": config.FONT_REGULAR,
    "bold": config.FONT_BOLD,
    "condensed": config.FONT_CONDENSED_BOLD,
}

COUNTER = re.compile(r"\{n(?::0?(\d+))?\}")


PLACEHOLDER = re.compile(r"\{([^{}\n]+)\}")


def substitute(text: str, n: int, row: dict[str, str] | None = None) -> str:
    """Replace {n} / {n:3} / {n:03} with the (zero-padded) counter value and,
    with a batch row, {Column} with that row's value (column names are case-insensitive)."""
    text = COUNTER.sub(lambda m: str(n).zfill(int(m.group(1) or 0)), text)
    if row:
        values = {k.strip().lower(): v for k, v in row.items()}
        text = PLACEHOLDER.sub(lambda m: values.get(m.group(1).strip().lower(), m.group(0)), text)
    return text


def has_counter(*texts: str) -> bool:
    return any(COUNTER.search(t) for t in texts)


def unknown_placeholders(columns, *texts: str) -> list[str]:
    known = {c.strip().lower() for c in columns}
    found = {m.group(1) for t in texts for m in PLACEHOLDER.finditer(t) if not COUNTER.fullmatch(m.group(0))}
    return sorted(f for f in found if f.strip().lower() not in known)


@lru_cache(maxsize=512)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _measure(lines: list[str], fnt: ImageFont.FreeTypeFont) -> tuple[int, int, int]:
    """Return (max ink width, ink top relative to baseline, ink line height) for a set of lines."""
    boxes = [fnt.getbbox(line or " ", anchor="ls") for line in lines]
    width = max(b[2] - b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)
    return width, top, bottom - top


def fit_font(lines, box_w, box_h, font_path, max_size=400, line_gap=0.18):
    """Largest font size at which all lines fit into box_w x box_h."""

    def fits(size):
        width, _, line_h = _measure(lines, _font(font_path, size))
        total_h = len(lines) * line_h + (len(lines) - 1) * int(line_h * line_gap)
        return width <= box_w and total_h <= box_h

    lo, hi = 4, max(4, int(max_size))
    if not fits(lo):
        return lo
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def draw_lines(draw, lines, box, font_path, size, align="center", line_gap=0.18, fill=0):
    """Draw lines vertically centered in box=(x0, y0, x1, y1)."""
    fnt = _font(font_path, size)
    _, top, line_h = _measure(lines, fnt)
    gap = int(line_h * line_gap)
    total_h = len(lines) * line_h + (len(lines) - 1) * gap
    x0, y0, x1, y1 = box
    y = y0 + (y1 - y0 - total_h) // 2 - top
    for line in lines:
        l, _, r, _ = fnt.getbbox(line or " ", anchor="ls")
        if align == "left":
            x = x0 - l
        elif align == "right":
            x = x1 - r
        else:
            x = x0 + (x1 - x0 - (r - l)) // 2 - l
        draw.text((x, y), line, font=fnt, fill=fill, anchor="ls")
        y += line_h + gap


def text_width(lines, font_path, size) -> int:
    return _measure(lines, _font(font_path, size))[0]


def qr_image(data: str, max_px: int, border: int = 1) -> Image.Image | None:
    """Crisp QR code (integer module size) no larger than max_px, or None if it can't be legible."""
    qr = segno.make(data, error="m", micro=False)
    matrix = qr.matrix
    n = len(matrix)
    module = max_px // (n + 2 * border)
    if module < 2:
        return None
    size = (n + 2 * border) * module
    img = Image.new("1", (size, size), 1)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, bit in enumerate(row):
            if bit:
                px, py = (x + border) * module, (y + border) * module
                draw.rectangle((px, py, px + module - 1, py + module - 1), fill=0)
    return img


def split_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r", "").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


# --------------------------------------------------------------------------- Zebra


def zebra_size_px() -> tuple[int, int]:
    w = int(config.ZEBRA_LABEL_WIDTH_MM * config.ZEBRA_DOTS_PER_MM) // 8 * 8
    h = int(config.ZEBRA_LABEL_HEIGHT_MM * config.ZEBRA_DOTS_PER_MM)
    return w, h


def render_zebra(title: str, body: str, qr: str, inverted_title: bool, align: str) -> tuple[Image.Image, list[str]]:
    """Box label: big title, smaller body lines, optional QR code on the right."""
    W, H = zebra_size_px()
    img = Image.new("1", (W, H), 1)
    draw = ImageDraw.Draw(img)
    warnings = []
    pad = 14

    text_x1 = W - pad
    if qr.strip():
        code = qr_image(qr.strip(), H - 2 * pad)
        if code is None:
            warnings.append("QR content too long for this label")
        else:
            qx = W - pad - code.width
            img.paste(code, (qx, (H - code.height) // 2))
            text_x1 = qx - pad

    title_lines = split_lines(title)
    body_lines = split_lines(body)
    box_w = text_x1 - pad
    if box_w < 40:
        return img, warnings

    title_h = 0
    title_size = 0
    if title_lines:
        title_h = int((H - 2 * pad) * (0.46 if body_lines else 1.0))
        inner = 8 if inverted_title else 0
        title_size = fit_font(title_lines, box_w - 2 * inner, title_h - 2 * inner, config.FONT_BOLD)
        box = (pad, pad, text_x1, pad + title_h)
        if inverted_title:
            draw.rounded_rectangle(box, radius=8, fill=0)
            box = (pad + inner, pad + inner, text_x1 - inner, pad + title_h - inner)
        draw_lines(draw, title_lines, box, config.FONT_BOLD, title_size, align, fill=1 if inverted_title else 0)

    if body_lines:
        top = pad + title_h
        if title_lines:
            if not inverted_title:
                draw.rectangle((pad, top + 5, text_x1, top + 7), fill=0)
            top += 14
        max_size = title_size * 0.62 if title_size else 400
        box = (pad, top, text_x1, H - pad)
        size = fit_font(body_lines, box_w, box[3] - box[1], config.FONT_REGULAR, max_size=max_size)
        draw_lines(draw, body_lines, box, config.FONT_REGULAR, size, align)

    return img, warnings


# --------------------------------------------------------------------------- Brother

PX_PER_MM = config.BROTHER_DPI / 25.4

# tape width (mm, as reported by the printer) -> printable pins on the 128-pin head
BROTHER_PRINTABLE = {4: 24, 6: 32, 9: 50, 12: 70, 18: 112, 24: 128}


def mm_to_px(mm: float) -> int:
    return round(mm * PX_PER_MM)


def render_brother(
    lines: list[str],
    tape_mm: int,
    font: str = "bold",
    qr: str = "",
    align: str = "center",
    flag: bool = False,
    cable_diameter_mm: float = 6.0,
    min_length_mm: float = 0.0,
    border: bool = False,
) -> tuple[Image.Image, list[str]]:
    """Tape label: height = printable area of the loaded tape, length grows with the content."""
    H = BROTHER_PRINTABLE.get(tape_mm)
    if H is None:
        raise ValueError(f"Unsupported tape width: {tape_mm} mm")
    font_path = FONTS.get(font, config.FONT_BOLD)
    warnings = []
    vpad = 2 + (3 if border else 0)
    hpad = mm_to_px(1.5) + (4 if border else 0)

    parts: list[Image.Image] = []
    if qr.strip():
        code = qr_image(qr.strip(), H)
        if code is None:
            warnings.append(f"QR code doesn't fit on {tape_mm} mm tape")
        else:
            parts.append(code)

    if lines:
        size = fit_font(lines, 10_000, H - 2 * vpad, font_path)
        tw = text_width(lines, font_path, size)
        text_img = Image.new("1", (tw + 2, H), 1)
        draw_lines(ImageDraw.Draw(text_img), lines, (0, vpad, tw + 2, H - vpad), font_path, size, align)
        parts.append(text_img)

    gap = mm_to_px(2)
    content_w = sum(p.width for p in parts) + gap * max(0, len(parts) - 1)
    unit_w = max(content_w + 2 * hpad, mm_to_px(min_length_mm), mm_to_px(6))
    unit = Image.new("1", (unit_w, H), 1)
    if align == "left":
        x = hpad
    elif align == "right":
        x = unit_w - hpad - content_w
    else:
        x = (unit_w - content_w) // 2
    for part in parts:
        unit.paste(part, (x, (H - part.height) // 2))
        x += part.width + gap
    if border:
        ImageDraw.Draw(unit).rectangle((0, 0, unit_w - 1, H - 1), outline=0, width=2)

    if not flag:
        return unit, warnings

    # Cable flag: [label][wrap around the cable][label] - both ends stick together
    wrap_w = mm_to_px(math.pi * cable_diameter_mm + 2)
    img = Image.new("1", (unit_w * 2 + wrap_w, H), 1)
    img.paste(unit, (0, 0))
    img.paste(unit, (unit_w + wrap_w, 0))
    draw = ImageDraw.Draw(img)
    for xm in (unit_w, unit_w + wrap_w - 1):  # small fold marks at the tape edges
        draw.line((xm, 0, xm, 3), fill=0)
        draw.line((xm, H - 4, xm, H - 1), fill=0)
    return img, warnings
