"""Zebra GK420d: label images -> ZPL ^GFA graphics, sent as a raw job through CUPS."""

import os
import subprocess

from PIL import Image

from . import config

# The labels are designed landscape (88 x 37 mm), but the roll feeds the 37 mm side
# across the print head, so the image is turned clockwise before printing.
# 270 if the text comes out upside down, 0 for a roll with the long side across the head.
ROTATE = int(os.environ.get("LP_ZEBRA_ROTATE", "90"))


class PrintError(Exception):
    pass


def image_to_zpl(img: Image.Image, copies: int = 1) -> str:
    if ROTATE:
        img = img.rotate(-ROTATE, expand=True)
    img = img.convert("1")
    if img.width % 8:  # padding bits would otherwise be inverted to black
        padded = Image.new("1", ((img.width + 7) // 8 * 8, img.height), 1)
        padded.paste(img, (0, 0))
        img = padded
    bytes_per_row = (img.width + 7) // 8
    # Pillow: 1 = white; ZPL: 1 = black
    data = bytes(b ^ 0xFF for b in img.tobytes())
    total = len(data)
    return (
        "^XA^LH0,0"
        f"^PW{img.width}^LL{img.height}"
        f"^FO0,0^GFA,{total},{total},{bytes_per_row},{data.hex().upper()}^FS"
        f"^PQ{copies}"
        "^XZ\n"
    )


def send(zpl: str) -> str:
    try:
        result = subprocess.run(
            ["lp", "-d", config.ZEBRA_QUEUE, "-o", "raw", "-t", "labelprinter"],
            input=zpl.encode("ascii"),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PrintError(f"lp failed: {e}") from e
    if result.returncode != 0:
        raise PrintError(result.stderr.decode(errors="replace").strip() or "lp failed")
    return result.stdout.decode(errors="replace").strip()


def status() -> dict:
    """Queue state from CUPS (the GK420d has no useful status channel through CUPS)."""
    try:
        out = subprocess.run(
            ["lpstat", "-p", config.ZEBRA_QUEUE], capture_output=True, timeout=5, text=True
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "message": str(e)}
    text = (out.stdout or out.stderr).strip()
    ok = out.returncode == 0 and "disabled" not in text
    return {"ok": ok, "message": text.splitlines()[0] if text else "unknown"}
