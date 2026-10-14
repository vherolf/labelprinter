"""Settings, overridable via environment variables (LP_*)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LP_DATA_DIR", BASE_DIR.parent / "data"))

FONT_REGULAR = os.environ.get("LP_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = os.environ.get("LP_FONT_BOLD", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
FONT_CONDENSED_BOLD = os.environ.get(
    "LP_FONT_CONDENSED_BOLD", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
)

# Zebra GK420d: 203 dpi = 8 dots/mm, printed through CUPS as raw ZPL
ZEBRA_QUEUE = os.environ.get("LP_ZEBRA_QUEUE", "ZTC-GK420d")
ZEBRA_DOTS_PER_MM = 8
ZEBRA_LABEL_WIDTH_MM = float(os.environ.get("LP_ZEBRA_WIDTH_MM", "88"))
ZEBRA_LABEL_HEIGHT_MM = float(os.environ.get("LP_ZEBRA_HEIGHT_MM", "37"))

# Brother PT-P710BT: 180 dpi, talked to directly over USB (falls back to CUPS raw)
BROTHER_VENDOR_ID = 0x04F9
BROTHER_PRODUCT_ID = int(os.environ.get("LP_BROTHER_PID", "0x20AF"), 16)
BROTHER_QUEUE = os.environ.get("LP_BROTHER_QUEUE", "PT-P710BT")
BROTHER_DPI = 180
