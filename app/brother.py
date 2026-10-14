"""Brother PT-P710BT over USB, using Brother's P-touch raster protocol.

Talking to the printer directly (instead of through CUPS) lets us read its status,
so the loaded tape width is detected automatically. If the USB device can't be
opened (permissions), jobs fall back to a raw CUPS job and the width must be given.
"""

import errno
import subprocess
import threading
import time
from dataclasses import dataclass, field

from PIL import Image

from . import config
from .render import BROTHER_PRINTABLE

try:
    import usb.core
    import usb.util
except ImportError:  # pragma: no cover
    usb = None


class PrintError(Exception):
    pass


class NoAccess(PrintError):
    pass


MEDIA_TYPES = {
    0x00: "no tape",
    0x01: "laminated",
    0x03: "non-laminated",
    0x11: "heat-shrink tube 2:1",
    0x13: "fabric",
    0x14: "flexible ID",
    0x15: "satin",
    0x17: "heat-shrink tube 3:1",
    0xFF: "incompatible tape",
}

# tape colour code -> (name, preview hex)
TAPE_COLORS = {
    0x01: ("white", "#ffffff"), 0x02: ("other", "#eeeeee"), 0x03: ("clear", "#e8f0f2"),
    0x04: ("red", "#d93a3a"), 0x05: ("blue", "#2d6fd6"), 0x06: ("yellow", "#f5d90a"),
    0x07: ("green", "#3a9d4a"), 0x08: ("black", "#1c1c1c"), 0x09: ("clear", "#e8f0f2"),
    0x20: ("matte white", "#f7f7f2"), 0x21: ("matte clear", "#e8f0f2"),
    0x22: ("matte silver", "#c4c7ca"), 0x23: ("satin gold", "#d4b25e"),
    0x24: ("satin silver", "#c4c7ca"), 0x30: ("blue", "#2d6fd6"), 0x31: ("red", "#d93a3a"),
    0x40: ("fluorescent orange", "#ff8a2a"), 0x41: ("fluorescent yellow", "#eaff3a"),
    0x50: ("berry pink", "#e0559a"), 0x51: ("light gray", "#cfd2d4"), 0x52: ("lime green", "#9ed64a"),
    0x60: ("yellow", "#f5d90a"), 0x61: ("pink", "#f39ac0"), 0x62: ("blue", "#7fb3f0"),
    0x70: ("white heat-shrink", "#ffffff"), 0x90: ("white flex", "#ffffff"),
    0x91: ("yellow flex", "#f5d90a"),
}
TEXT_COLORS = {
    0x01: ("white", "#ffffff"), 0x04: ("red", "#c62828"), 0x05: ("blue", "#1f4fa8"),
    0x08: ("black", "#111111"), 0x0A: ("gold", "#b8901f"), 0x62: ("blue", "#1f4fa8"),
}

ERRORS_1 = {0x01: "no tape", 0x04: "cutter jam", 0x08: "weak batteries", 0x40: "high-voltage adapter"}
ERRORS_2 = {0x01: "wrong tape", 0x10: "cover open", 0x20: "overheating"}

STATUS_REPLY, STATUS_DONE, STATUS_ERROR, STATUS_OFF, STATUS_PHASE = 0x00, 0x01, 0x02, 0x04, 0x06


@dataclass
class Status:
    raw: bytes
    tape_mm: int = 0
    media: str = ""
    tape_color: str = ""
    tape_hex: str = "#ffffff"
    text_color: str = ""
    text_hex: str = "#111111"
    status_type: int = 0
    errors: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, b: bytes) -> "Status":
        tape = TAPE_COLORS.get(b[24], (f"0x{b[24]:02x}", "#ffffff"))
        text = TEXT_COLORS.get(b[25], (f"0x{b[25]:02x}", "#111111"))
        errors = [msg for bit, msg in ERRORS_1.items() if b[8] & bit]
        errors += [msg for bit, msg in ERRORS_2.items() if b[9] & bit]
        return cls(
            raw=b,
            tape_mm=b[10],
            media=MEDIA_TYPES.get(b[11], f"0x{b[11]:02x}"),
            tape_color=tape[0],
            tape_hex=tape[1],
            text_color=text[0],
            text_hex=text[1],
            status_type=b[18],
            errors=errors,
        )

    def to_dict(self) -> dict:
        return {
            "tape_mm": self.tape_mm,
            "media": self.media,
            "tape_color": self.tape_color,
            "tape_hex": self.tape_hex,
            "text_color": self.text_color,
            "text_hex": self.text_hex,
            "errors": self.errors,
        }


# --------------------------------------------------------------------------- raster job

INVALIDATE = b"\x00" * 100
INITIALIZE = b"\x1b@"
STATUS_REQUEST = b"\x1biS"
MARGIN_DOTS = 14  # feed before/after the content, ~2 mm


def image_to_rasters(img: Image.Image, tape_mm: int) -> list[bytes]:
    """One 16-byte raster line (128 pins) per image column, content centred on the head."""
    height = BROTHER_PRINTABLE[tape_mm]
    if img.height != height:
        raise ValueError(f"image height {img.height} != printable {height} for {tape_mm} mm tape")
    # transpose: image column -> raster line; top of the label -> first pin (MSB)
    col = img.convert("1").transpose(Image.Transpose.TRANSPOSE)
    head = Image.new("1", (128, col.height), 1)
    head.paste(col, ((128 - height) // 2, 0))
    data = bytes(b ^ 0xFF for b in head.tobytes())  # Pillow 1 = white, printer 1 = dot
    return [data[i : i + 16] for i in range(0, len(data), 16)]


def build_job(pages: list[Image.Image], tape_mm: int) -> bytes:
    job = bytearray(INVALIDATE + INITIALIZE)
    job += b"\x1bia\x01"  # switch to raster mode
    for i, page in enumerate(pages):
        rasters = image_to_rasters(page, tape_mm)
        # print information: width valid (printer rejects mismatched tape), quality, recover
        job += b"\x1biz" + bytes([0x84 | 0x40, 0x00, tape_mm, 0x00])
        job += len(rasters).to_bytes(4, "little") + bytes([0 if i == 0 else 1, 0])
        job += b"\x1biM\x40"  # auto cut
        job += b"\x1biA\x01"  # cut after every label
        job += b"\x1biK\x08"  # no chain printing: feed + cut the last label
        job += b"\x1bid" + MARGIN_DOTS.to_bytes(2, "little")
        job += b"M\x02"  # TIFF (PackBits) compression
        for line in rasters:
            if any(line):
                job += b"G\x11\x00\x0f" + line  # one literal PackBits run of 16 bytes
            else:
                job += b"Z"
        job += b"\x1a" if i == len(pages) - 1 else b"\x0c"
    return bytes(job)


# --------------------------------------------------------------------------- USB transport


class BrotherPT:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: tuple[float, dict] | None = None

    def _open(self):
        if usb is None:
            raise NoAccess("pyusb not installed")
        try:
            dev = usb.core.find(idVendor=config.BROTHER_VENDOR_ID, idProduct=config.BROTHER_PRODUCT_ID)
        except usb.core.NoBackendError as e:
            raise NoAccess("libusb not available") from e
        if dev is None:
            raise PrintError("Brother printer not found on USB (switched off?)")
        reattach = False
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                reattach = True
            usb.util.claim_interface(dev, 0)
        except usb.core.USBError as e:
            if e.errno in (errno.EACCES, errno.EPERM):
                raise NoAccess("no permission for the USB device (see README: udev rule)") from e
            if e.errno == errno.EBUSY:
                raise PrintError("printer is busy (CUPS job running?)") from e
            raise PrintError(f"USB error: {e}") from e
        intf = dev.get_active_configuration()[(0, 0)]
        ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        return dev, ep_out, ep_in, reattach

    @staticmethod
    def _close(dev, reattach):
        try:
            usb.util.release_interface(dev, 0)
            if reattach:
                dev.attach_kernel_driver(0)
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(dev)

    @staticmethod
    def _read_status(ep_in, timeout: float) -> Status | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = bytes(ep_in.read(32, timeout=500))
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                if e.errno == errno.ETIMEDOUT:
                    continue
                raise
            if len(data) == 32 and data[0] == 0x80:
                return Status.parse(data)
            time.sleep(0.05)
        return None

    def status(self, max_age: float = 2.0) -> dict:
        """Query the printer (cached briefly, so the UI can poll)."""
        if self._cache and time.monotonic() - self._cache[0] < max_age:
            return self._cache[1]
        with self._lock:
            try:
                dev, ep_out, ep_in, reattach = self._open()
            except NoAccess as e:
                result = {"ok": False, "access": False, "message": str(e)}
            except PrintError as e:
                result = {"ok": False, "access": True, "message": str(e)}
            else:
                try:
                    ep_out.write(INVALIDATE + INITIALIZE + STATUS_REQUEST)
                    st = self._read_status(ep_in, 3)
                    if st is None:
                        result = {"ok": False, "access": True, "message": "no status reply"}
                    else:
                        result = {"ok": not st.errors, "access": True, **st.to_dict(),
                                  "message": ", ".join(st.errors) or "ready"}
                except usb.core.USBError as e:
                    result = {"ok": False, "access": True, "message": f"USB error: {e}"}
                finally:
                    self._close(dev, reattach)
        self._cache = (time.monotonic(), result)
        return result

    def print(self, pages: list[Image.Image], tape_mm: int) -> str:
        job = build_job(pages, tape_mm)
        self._cache = None
        with self._lock:
            try:
                dev, ep_out, ep_in, reattach = self._open()
            except NoAccess:
                return self._print_cups(job)
            try:
                for i in range(0, len(job), 4096):
                    ep_out.write(job[i : i + 4096], timeout=10_000)
                # wait for "printing completed" per page (or an error)
                done = 0
                deadline = time.monotonic() + 20 + 10 * len(pages)
                while done < len(pages) and time.monotonic() < deadline:
                    st = self._read_status(ep_in, deadline - time.monotonic())
                    if st is None:
                        break
                    if st.status_type == STATUS_ERROR or st.errors:
                        raise PrintError("printer error: " + (", ".join(st.errors) or "unknown"))
                    if st.status_type == STATUS_DONE:
                        done += 1
                if done < len(pages):
                    return f"sent, {done}/{len(pages)} confirmed"
                return f"printed {done} label(s)"
            except usb.core.USBError as e:
                raise PrintError(f"USB error: {e}") from e
            finally:
                self._close(dev, reattach)

    @staticmethod
    def _print_cups(job: bytes) -> str:
        result = subprocess.run(
            ["lp", "-d", config.BROTHER_QUEUE, "-o", "raw", "-t", "labelprinter"],
            input=job, capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            raise PrintError(result.stderr.decode(errors="replace").strip() or "lp failed")
        return "sent via CUPS (no USB access, tape width not verified)"


printer = BrotherPT()
