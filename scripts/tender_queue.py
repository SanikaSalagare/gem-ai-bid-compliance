from __future__ import annotations

import hashlib
import json
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts import job_queue, requirement_detector, text_extractor
from scripts.atomic_io import merge_write_json

ROOT_DIR = Path(__file__).resolve().parent.parent
TENDERS_DIR = ROOT_DIR / "data" / "TENDERS"
STATUS_FILENAME = "processing_status.json"

# Key inside tender.json (alongside the buyer-entered metadata fields) used
# to remember each tender document's SHA-256 hash, so unchanged tender
# documents don't trigger a fresh (expensive) requirement extraction pass
# on every run. Mirrors manage.TENDER_FILE_HASHES_KEY - kept as a separate
# constant here (rather than importing manage) to avoid a circular import,
# since manage.py imports from this module's siblings.
TENDER_FILE_HASHES_KEY = "_file_hashes"

# Every job actually executes on the single shared worker in
# scripts/job_queue.py (see that module for why: a tender-processing job
# and a bid-compliance job must never run at the same time). This lock
# only protects the bookkeeping below - _active_tenders and the status
# file - from being read/written by two requests at once.
_jobs_lock = threading.RLock()

# tender_id -> True while a processing job for that tender is queued or
# running. Lets enqueue() recognise "this tender already has a job in
# flight" and hand back that job instead of piling on a duplicate -
# e.g. if a buyer double-clicks "Process Documents", or reloads the page
# and it re-submits the form.
_active_tenders: set[str] = set()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _status_path(tender_id):
    return TENDERS_DIR / tender_id / STATUS_FILENAME


def _persist(job):
    # processing_status.json is only ever written by this queue, but we
    # still merge (rather than overwrite) for the same reason
    # compliance_queue does: it's cheap insurance against any future
    # writer sharing the file, and it's what atomic_io.merge_write_json
    # is for.
    merge_write_json(_status_path(job["tender_id"]), job)


def _compute_document_hashes(tender_dir):
    documents_dir = tender_dir / "tender_documents"
    processed_dir = documents_dir / "processed"
    hashes = {}

    if documents_dir.exists():
        for pdf_path in sorted(documents_dir.glob("*.pdf")):
            # Mirrors manage._compute_tender_document_hashes: only count a
            # file as "processed" if its extraction actually succeeded,
            # so a failed file keeps retrying instead of being silently
            # marked done forever.
            if text_extractor.processed_hash_matches(pdf_path, processed_dir):
                hashes[pdf_path.name] = text_extractor.get_pdf_hash(pdf_path)

    # The buyer-entered eligibility text is fed into requirement
    # extraction too (see requirement_detector.load_eligibility_page),
    # so it must participate in change-detection - otherwise editing
    # eligibility text alone would never trigger a re-run since no PDF
    # hash changed.
    eligibility_page = requirement_detector.load_eligibility_page(tender_dir)
    if eligibility_page is not None:
        hashes[requirement_detector.ELIGIBILITY_SOURCE_LABEL] = hashlib.sha256(
            eligibility_page["text"].encode("utf-8")
        ).hexdigest()

    return hashes


def _run_processing(tender_id):
    """Extract text from every tender document, then (re-)run requirement
    extraction only if the document set actually changed since last time.
    """
    tender_dir = TENDERS_DIR / tender_id
    tender_documents = tender_dir / "tender_documents"

    if tender_documents.exists():
        text_extractor.process_document_folder(tender_documents)

    tender_json_path = tender_dir / "tender.json"
    old_data = {}
    if tender_json_path.exists():
        try:
            old_data = json.loads(tender_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_data = {}

    old_hashes = old_data.get(TENDER_FILE_HASHES_KEY, {})
    new_hashes = _compute_document_hashes(tender_dir)
    changed = old_hashes != new_hashes

    merge_write_json(tender_json_path, {TENDER_FILE_HASHES_KEY: new_hashes})

    requirements_path = tender_dir / "requirement.json"
    if not changed and requirements_path.exists():
        # No tender document changed and requirements already exist:
        # reuse them instead of re-running the LLM.
        return

    requirement_detector.process_tender(tender_dir)


def _run_job(job):
    """The actual work for one job, run on job_queue's shared worker.
    Guaranteed never to overlap with any other tender-processing job OR
    any bid-compliance job (see scripts/job_queue.py)."""
    try:
        with _jobs_lock:
            job["status"] = "processing"
            job["started_at"] = _now()
            _persist(job)

        _run_processing(job["tender_id"])

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
            _active_tenders.discard(job["tender_id"])


def enqueue(tender_id):
    """Queue document processing + requirement extraction for a tender.

    If a job for this tender is already queued or running, that existing
    job is returned instead of enqueuing a duplicate - so a repeated
    "Process Documents" click (double-click, page refresh resubmitting
    the form, etc.) can't stack up redundant runs against the same
    tender.
    """
    with _jobs_lock:
        if tender_id in _active_tenders:
            existing = get_status(tender_id)
            if existing is not None and existing.get("status") in ("queued", "processing"):
                return existing

        job_id = f"processing_{uuid.uuid4().hex[:10]}"
        job = {
            "job_id": job_id,
            "tender_id": tender_id,
            "status": "queued",
            "queued_at": _now(),
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        _active_tenders.add(tender_id)
        _persist(job)

    job_queue.submit(lambda: _run_job(job))
    return job


def get_status(tender_id):
    path = _status_path(tender_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return None
