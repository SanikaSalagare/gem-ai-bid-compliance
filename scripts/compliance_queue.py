from __future__ import annotations

import json
import queue
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts import compliance_checker, text_extractor
from scripts.model_manager import reset_context
from scripts.atomic_io import merge_write_json

ROOT_DIR = Path(__file__).resolve().parent.parent
TENDERS_DIR = ROOT_DIR / "data" / "TENDERS"
STATUS_FILENAME = "analysis_status.json"

_jobs: queue.Queue[dict] = queue.Queue()
_jobs_lock = threading.RLock()
_worker_started = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _status_path(tender_id, bid_id):
    return TENDERS_DIR / tender_id / "bids" / bid_id / STATUS_FILENAME


def _persist(job):
    # analysis_status.json is shared with compliance_checker.process_bid,
    # which writes the "bid_documents_hash" / "requirements" keys onto the
    # same file for per-requirement progress/caching. Merge instead of
    # overwriting so neither writer clobbers the other's keys.
    path = _status_path(job["tender_id"], job["bid_id"])
    merge_write_json(path, job)


def _worker():
    while True:
        job = _jobs.get()
        try:
            with _jobs_lock:
                job["status"] = "processing"
                job["started_at"] = _now()
                _persist(job)

            tender_dir = TENDERS_DIR / job["tender_id"]
            bid_id = job["bid_id"]
            text_extractor.process_document_folder(tender_dir / "bids" / bid_id / "documents")
            reset_context()
            compliance_checker.process_bid(tender_dir, bid_id)

            with _jobs_lock:
                job["status"] = "completed"
                job["finished_at"] = _now()
                _persist(job)
        except Exception as exc:
            traceback.print_exc()
            with _jobs_lock:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["finished_at"] = _now()
                _persist(job)
        finally:
            reset_context()
            _jobs.task_done()


def _ensure_worker():
    global _worker_started
    with _jobs_lock:
        if not _worker_started:
            thread = threading.Thread(target=_worker, name="gem-compliance-worker", daemon=True)
            thread.start()
            _worker_started = True


def enqueue(tender_id, bid_id):
    _ensure_worker()
    job_id = f"analysis_{uuid.uuid4().hex[:10]}"
    job = {
        "job_id": job_id,
        "tender_id": tender_id,
        "bid_id": bid_id,
        "status": "queued",
        "queued_at": _now(),
        "started_at": None,
        "finished_at": None,
        "error": None,
    }
    with _jobs_lock:
        _persist(job)
        _jobs.put(job)
    return job


def get_status(tender_id, bid_id):
    path = _status_path(tender_id, bid_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return None
