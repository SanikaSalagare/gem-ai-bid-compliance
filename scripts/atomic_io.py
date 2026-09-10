import json
import os
from pathlib import Path


def atomic_write_json(path, data) -> None:
    """Write `data` as JSON to `path` atomically.

    Writes to a temp file in the same directory first, then replaces the
    target with os.replace(), so a crash mid-write can never leave a
    truncated/corrupt JSON file behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp_path.write_text(
        json.dumps(data, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def merge_write_json(path, updates: dict) -> dict:
    """Shallow-merge `updates` into the JSON object already at `path`.

    Reads the existing JSON object at `path` (treating a missing or
    unreadable file as {}), applies dict.update(updates) on top of it,
    and writes the merged result back atomically. This lets independent
    writers (e.g. the job queue and the per-requirement evaluator) share
    one status file without clobbering each other's keys.

    Returns the merged dict.
    """
    path = Path(path)
    existing = {}

    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}

    existing.update(updates)
    atomic_write_json(path, existing)
    return existing
