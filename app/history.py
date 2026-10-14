"""Last printed labels, kept in a small JSON file for reprint / load."""

import json
import threading
import time
import uuid

from . import config

PATH = config.DATA_DIR / "history.json"
MAX_ENTRIES = 200
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add(printer: str, summary: str, params: dict, client: str) -> None:
    with _lock:
        entries = _load()
        entries.insert(0, {
            "id": uuid.uuid4().hex[:12],
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "printer": printer,
            "summary": summary,
            "params": params,
            "client": client,
        })
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries[:MAX_ENTRIES], ensure_ascii=False, indent=1))
        tmp.replace(PATH)


def all_entries() -> list[dict]:
    with _lock:
        return _load()


def get(entry_id: str) -> dict | None:
    return next((e for e in all_entries() if e["id"] == entry_id), None)
