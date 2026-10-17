# Labelprinter

Small web app (FastAPI + Jinja2) for two label printers on the LAN:

| Printer | Used for | How it prints |
|---|---|---|
| Zebra GK420d (88 × 37 mm labels) | boxes | label rendered as an image → ZPL `^GFA` → `lp -o raw` to CUPS queue `ZTC-GK420d` |
| Brother P-Touch Cube PT-P710BT | computers, cables | P-touch raster protocol directly over USB; tape width/colour is auto-detected |

Every label is rendered with Pillow, so the preview in the browser is exactly what gets printed.

## Features

- Live preview while typing, `Ctrl+Enter` prints
- **Box labels:** title, content lines, optional QR code, inverted title, copies
- **Tape labels:** 1–n lines auto-sized to the loaded tape, QR code, border, min. length,
  **cable flag** (text on both ends, middle wraps around the cable), one label per line
- **Numbering:** `{n}` / `{n:03}` in any field + start/count → `BOX-001`, `BOX-002`, …
- **Batch from CSV / Excel:** upload a `.csv` or `.xlsx` (first row = column names, first sheet only)
  and use the columns as placeholders, e.g. title `{Room}`, QR `https://inventory/box/{ID}`.
  One label per row (max 200); step through the rows in the preview. CSV separator (`,` `;` tab)
  and encoding (UTF-8 / Windows-1252) are detected automatically. `{n}` counts the rows.
- **Download PDF:** the same labels as a PDF, one label per page at real size (all rows / copies)
- History with *Edit* and *Reprint*

## Run

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<this-host>:8000`.

### USB permission for the Brother

The printer's USB device node is owned by group `lp`. Without access the app still prints
(through the CUPS queue `PT-P710BT`), but it cannot detect the tape, so you have to select the
width manually. Either:

- run it as the systemd service (has `SupplementaryGroups=lp`), or
- `sudo usermod -aG lp $USER` and log in again.

### As a service

```bash
sudo cp deploy/labelprinter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now labelprinter
```

## Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `LP_ZEBRA_QUEUE` | `ZTC-GK420d` | CUPS queue |
| `LP_ZEBRA_WIDTH_MM` / `LP_ZEBRA_HEIGHT_MM` | `88` / `37` | label size as you read it (landscape) |
| `LP_ZEBRA_ROTATE` | `90` | rotation before printing: `270` if upside down, `0` if the long side runs across the head |
| `LP_BROTHER_QUEUE` | `PT-P710BT` | CUPS fallback queue |
| `LP_DATA_DIR` | `./data` | history storage |
