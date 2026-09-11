from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts import compliance_checker, job_queue, text_extractor
from scripts.atomic_io import merge_write_json

ROOT_DIR = Path(__file__).resolve().parent.parent
TENDERS_DIR = ROOT_DIR / "data" / "TENDERS"
STATUS_FILENAME = "analysis_status.json"

# Every job actually executes on the single shared worker in
# scripts/job_queue.py (see that module for why: a bid-compliance job
# and a tender-processing job must never run at the same time). This
# lock only protects the bookkeeping below - _active_bids and the status
# file - from being read/written by two requests at once.
_jobs_lock = threading.RLock()

# (tender_id, bid_id) -> True while an analysis job for that bid is
# queued or running. Lets enqueue() recognise "this bid already has a
# job in flight" and hand back that job instead of piling on a
# duplicate - e.g. if a seller double-clicks "Submit for Compliance
# Check", or reloads the page and it re-submits the form.
_active_bids: set[tuple[str, str]] = set()


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


def _run_job(job):
    """The actual work for one job, run on job_queue's shared worker.
    Guaranteed never to overlap with any other bid-compliance job OR any
    tender-processing job (see scripts/job_queue.py) - so a bid can never
    be evaluated against a requirement set that's mid-(re)extraction."""
    tender_id = job["tender_id"]
    bid_id = job["bid_id"]
    key = (tender_id, bid_id)

    try:
        with _jobs_lock:
            job["status"] = "processing"
            job["started_at"] = _now()
            _persist(job)

        tender_dir = TENDERS_DIR / tender_id
        text_extractor.process_document_folder(tender_dir / "bids" / bid_id / "documents")
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
        with _jobs_lock:
            _active_bids.discard(key)


def enqueue(tender_id, bid_id):
    """Queue compliance analysis for a bid.

    If a job for this bid is already queued or running, that existing
    job is returned instead of enqueuing a duplicate - so a repeated
    "Submit for Compliance Check" click can't stack up redundant runs
    against the same bid. The job itself only starts once it reaches the
    front of the single shared queue (scripts/job_queue.py), so it will
    naturally wait out any tender-processing job that's already running -
    including one for this bid's own tender - rather than racing it.
    """
    key = (tender_id, bid_id)

    with _jobs_lock:
        if key in _active_bids:
            existing = get_status(tender_id, bid_id)
            if existing is not None and existing.get("status") in ("queued", "processing"):
                return existing

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
        _active_bids.add(key)
        _persist(job)

    job_queue.submit(lambda: _run_job(job))
    return job


def get_status(tender_id, bid_id):
    path = _status_path(tender_id, bid_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return None
