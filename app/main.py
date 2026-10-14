import base64
import io
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import brother, config, history, render, zebra

app = FastAPI(title="Labelprinter")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=config.BASE_DIR / "templates")

MAX_LABELS = 200
Align = Literal["left", "center", "right"]


class ZebraJob(BaseModel):
    title: str = ""
    body: str = ""
    qr: str = ""
    inverted_title: bool = False
    align: Align = "center"
    copies: int = Field(1, ge=1, le=100)
    counter_start: int = 1
    counter_count: int = Field(1, ge=1, le=MAX_LABELS)


class BrotherJob(BaseModel):
    text: str = ""
    separate_lines: bool = False
    font: Literal["bold", "regular", "condensed"] = "bold"
    qr: str = ""
    align: Align = "center"
    flag: bool = False
    cable_diameter_mm: float = Field(6.0, ge=1, le=60)
    min_length_mm: float = Field(0, ge=0, le=500)
    border: bool = False
    copies: int = Field(1, ge=1, le=50)
    counter_start: int = 1
    counter_count: int = Field(1, ge=1, le=MAX_LABELS)
    tape_mm: int | None = None  # None = autodetect


def counter_values(start: int, count: int, *texts: str) -> list[int]:
    return list(range(start, start + count)) if render.has_counter(*texts) else [start]


def zebra_labels(job: ZebraJob) -> tuple[list[Image.Image], list[str]]:
    images, warnings = [], []
    for n in counter_values(job.counter_start, job.counter_count, job.title, job.body, job.qr):
        sub = lambda s: render.substitute(s, n)  # noqa: E731
        img, warn = render.render_zebra(sub(job.title), sub(job.body), sub(job.qr), job.inverted_title, job.align)
        images.append(img)
        warnings += warn
    return images, sorted(set(warnings))


def brother_labels(job: BrotherJob, tape_mm: int) -> tuple[list[Image.Image], list[str]]:
    texts = [line.replace("|", "\n") for line in render.split_lines(job.text) if line.strip()] \
        if job.separate_lines else [job.text]
    images, warnings = [], []
    for n in counter_values(job.counter_start, job.counter_count, job.text, job.qr):
        for text in texts:
            img, warn = render.render_brother(
                render.split_lines(render.substitute(text, n)), tape_mm, job.font,
                render.substitute(job.qr, n), job.align, job.flag, job.cable_diameter_mm,
                job.min_length_mm, job.border,
            )
            images += [img] * job.copies
            warnings += warn
    if len(images) > MAX_LABELS:
        raise HTTPException(400, f"Too many labels ({len(images)}), max {MAX_LABELS}")
    return images, sorted(set(warnings))


def png_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def brother_preview(img: Image.Image, tape_mm: int, tape_hex: str, text_hex: str) -> Image.Image:
    """Show the label as it comes out: full tape width, feed margins, tape/ink colours."""
    tape_px = render.mm_to_px(3.5 if tape_mm == 4 else tape_mm)
    margin = brother.MARGIN_DOTS
    canvas = Image.new("RGB", (img.width + 2 * margin, max(tape_px, img.height)), tape_hex)
    ink = Image.new("RGB", img.size, text_hex)
    mask = ImageOps.invert(img.convert("L"))
    canvas.paste(ink, (margin, (canvas.height - img.height) // 2), mask)
    return canvas


def summary(*texts: str) -> str:
    for t in texts:
        lines = render.split_lines(t)
        if lines:
            return " / ".join(lines)[:80]
    return "(empty)"


# --------------------------------------------------------------------------- pages


def page(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def loaded_params(load: str | None, printer: str) -> dict | None:
    if not load:
        return None
    entry = history.get(load)
    return entry["params"] if entry and entry["printer"] == printer else None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return page(request, "index.html", recent=history.all_entries()[:8])


@app.get("/zebra", response_class=HTMLResponse)
def zebra_page(request: Request, load: str | None = None):
    w, h = config.ZEBRA_LABEL_WIDTH_MM, config.ZEBRA_LABEL_HEIGHT_MM
    return page(request, "zebra.html", params=loaded_params(load, "zebra"), size=f"{w:g} × {h:g} mm")


@app.get("/brother", response_class=HTMLResponse)
def brother_page(request: Request, load: str | None = None):
    return page(request, "brother.html", params=loaded_params(load, "brother"),
                tapes=sorted(render.BROTHER_PRINTABLE))


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    return page(request, "history.html", entries=history.all_entries())


# --------------------------------------------------------------------------- API


@app.get("/api/status")
def status():
    return {"zebra": zebra.status(), "brother": brother.printer.status()}


@app.post("/api/zebra/preview")
def zebra_preview(job: ZebraJob):
    images, warnings = zebra_labels(job)
    return {"image": png_data_url(images[0]), "count": len(images) * job.copies, "warnings": warnings}


@app.post("/api/zebra/print")
def zebra_print(job: ZebraJob, request: Request):
    images, _ = zebra_labels(job)
    zpl = "".join(zebra.image_to_zpl(img, job.copies) for img in images)
    try:
        msg = zebra.send(zpl)
    except zebra.PrintError as e:
        raise HTTPException(502, str(e))
    history.add("zebra", summary(job.title, job.body), job.model_dump(), request.client.host)
    return {"message": f"{len(images) * job.copies} label(s) sent. {msg}"}


@app.post("/api/brother/preview")
def brother_preview_api(job: BrotherJob):
    st = brother.printer.status()
    detected = st.get("tape_mm") or None
    tape_mm = job.tape_mm or detected or 12
    if tape_mm not in render.BROTHER_PRINTABLE:
        raise HTTPException(400, f"Unsupported tape width {tape_mm} mm")
    images, warnings = brother_labels(job, tape_mm)
    colors = (st.get("tape_hex", "#ffffff"), st.get("text_hex", "#111111")) \
        if detected == tape_mm else ("#ffffff", "#111111")
    img = images[0]
    length_mm = (img.width + 2 * brother.MARGIN_DOTS) / render.PX_PER_MM
    if job.tape_mm and detected and job.tape_mm != detected:
        warnings.append(f"Printer has {detected} mm tape loaded")
    return {
        "image": png_data_url(brother_preview(img, tape_mm, *colors)),
        "count": len(images),
        "tape_mm": tape_mm,
        "length_mm": round(length_mm, 1),
        "warnings": warnings,
    }


@app.post("/api/brother/print")
def brother_print(job: BrotherJob, request: Request):
    st = brother.printer.status(max_age=0)
    if st.get("access"):
        if not st.get("ok"):
            raise HTTPException(409, f"Brother: {st['message']}")
        tape_mm = st["tape_mm"]
    elif job.tape_mm:
        tape_mm = job.tape_mm
    else:
        raise HTTPException(409, f"Can't detect tape ({st['message']}) - select the tape width manually")
    if tape_mm not in render.BROTHER_PRINTABLE:
        raise HTTPException(409, f"Unsupported tape width {tape_mm} mm")
    images, _ = brother_labels(job, tape_mm)
    try:
        msg = brother.printer.print(images, tape_mm)
    except brother.PrintError as e:
        raise HTTPException(502, str(e))
    history.add("brother", summary(job.text, job.qr), job.model_dump(), request.client.host)
    return {"message": f"{msg} on {tape_mm} mm tape"}


@app.post("/api/history/{entry_id}/print")
def reprint(entry_id: str, request: Request):
    entry = history.get(entry_id)
    if not entry:
        raise HTTPException(404, "Not found")
    if entry["printer"] == "zebra":
        return zebra_print(ZebraJob(**entry["params"]), request)
    return brother_print(BrotherJob(**entry["params"]), request)
