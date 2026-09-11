from pathlib import Path
import json
import uuid
import shutil

from scripts import text_extractor
from scripts import requirement_detector
from scripts import compliance_checker
from scripts.atomic_io import atomic_write_json, merge_write_json


ROOT_DIR = Path(__file__).resolve().parent
TENDERS_DIR = ROOT_DIR / "data" / "TENDERS"

# Key inside tender.json (alongside the buyer-entered metadata fields)
# used to remember each tender document's SHA-256 hash, so unchanged
# tender documents don't trigger a fresh (expensive) requirement
# extraction pass on every process_tender() call.
TENDER_FILE_HASHES_KEY = "_file_hashes"


def create_tender(metadata=None):
    tender_id = f"tender_{uuid.uuid4().hex[:8]}"
    tender_dir = TENDERS_DIR / tender_id

    (tender_dir / "tender_documents" / "processed").mkdir(parents=True, exist_ok=True)
    (tender_dir / "bids").mkdir(parents=True, exist_ok=True)

    metadata = metadata or {}
    metadata.setdefault("title", "Untitled GeM Procurement")
    metadata.setdefault("category", "General")
    metadata.setdefault("quantity", "")
    metadata.setdefault("bid_type", "Open Bid")
    metadata.setdefault("delivery_period", "")
    metadata.setdefault("bid_validity", "")
    metadata.setdefault("emd_required", False)
    metadata.setdefault("performance_security_required", False)
    metadata.setdefault("eligibility", "")
    metadata.setdefault("description", "")
    metadata.setdefault("buyer_id", "buyer_001")

    (tender_dir / "tender.json").write_text(
        json.dumps(metadata, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    return tender_id


def get_tender_metadata(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        return None
    path = tender_dir / "tender.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    # Hide the internal file-hash bookkeeping key from callers that treat
    # this as "the tender's metadata" (buyer-entered fields only).
    return {
        key: value
        for key, value in data.items()
        if key != TENDER_FILE_HASHES_KEY
    }


def update_tender_metadata(tender_id, metadata):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")
    path = tender_dir / "tender.json"

    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    data = dict(metadata)
    if TENDER_FILE_HASHES_KEY in existing:
        data[TENDER_FILE_HASHES_KEY] = existing[TENDER_FILE_HASHES_KEY]

    atomic_write_json(path, data)


def _resolve_within(base_dir, candidate_dir):
    """Resolve candidate_dir and confirm it is actually inside base_dir.

    tender_id/bid_id come straight from the URL and are used to build
    filesystem paths; without this check a value like ".." would resolve
    outside TENDERS_DIR while still passing a plain exists()/is_dir()
    test. Returns the resolved dir, or None if it escapes base_dir or
    doesn't exist.
    """
    base_dir = base_dir.resolve()
    try:
        resolved = candidate_dir.resolve()
        resolved.relative_to(base_dir)
    except (OSError, ValueError):
        return None
    return resolved if resolved.exists() and resolved.is_dir() else None


def get_tender(tender_id):
    tender_dir = TENDERS_DIR / tender_id
    return _resolve_within(TENDERS_DIR, tender_dir)


def list_tenders():
    if not TENDERS_DIR.exists():
        return []
    return sorted(
        folder.name for folder in TENDERS_DIR.iterdir()
        if folder.is_dir()
    )


def delete_tender(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        return False
    shutil.rmtree(tender_dir)
    return True


def add_tender_document(tender_id, file_path):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if source.suffix.lower() != ".pdf":
        raise ValueError("Tender documents must be PDF files.")

    documents_dir = tender_dir / "tender_documents"
    documents_dir.mkdir(parents=True, exist_ok=True)

    destination = documents_dir / source.name
    shutil.copy2(source, destination)
    return destination


def _compute_tender_document_hashes(tender_dir):
    documents_dir = tender_dir / "tender_documents"
    processed_dir = documents_dir / "processed"
    hashes = {}

    if documents_dir.exists():
        for pdf_path in sorted(documents_dir.glob("*.pdf")):
            # Only record a hash for files that were actually extracted
            # successfully. A file whose extraction failed is left out
            # entirely, so it keeps looking "changed" on every future
            # run instead of being silently treated as done.
            if text_extractor.processed_hash_matches(pdf_path, processed_dir):
                hashes[pdf_path.name] = text_extractor.get_pdf_hash(pdf_path)

    return hashes


def process_tender_documents(tender_id):
    """Run text extraction over the tender's documents, then record each
    document's current hash in tender.json.

    Returns True if the tender's document set changed (a file was added,
    removed, or its content changed) since the last time this ran, False
    if every document's hash is unchanged.
    """
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    # text_extractor already skips re-extracting/re-OCRing any individual
    # PDF whose hash hasn't changed (see text_extractor.process_pdf); this
    # is preserved as-is.
    text_extractor.process_tender(tender_dir)

    tender_json_path = tender_dir / "tender.json"
    old_data = {}
    if tender_json_path.exists():
        try:
            old_data = json.loads(tender_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_data = {}

    old_hashes = old_data.get(TENDER_FILE_HASHES_KEY, {})
    new_hashes = _compute_tender_document_hashes(tender_dir)
    changed = old_hashes != new_hashes

    merge_write_json(tender_json_path, {TENDER_FILE_HASHES_KEY: new_hashes})

    return changed


def generate_requirements(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    return requirement_detector.process_tender(tender_dir)


def process_tender(tender_id):
    """Synchronous document processing + requirement extraction.

    Kept for CLI/scripting use (see `if __name__ == "__main__"` at the
    bottom of this module and scripts/*.py's own __main__ blocks). The web
    app does NOT call this directly - it can take minutes for a
    multi-page tender, so it uses check_tender_processing() below to run
    the same work on a background thread instead of blocking a request.
    """
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    changed = process_tender_documents(tender_id)

    requirements_path = tender_dir / "requirement.json"
    if not changed and requirements_path.exists():
        # No tender document changed and requirements already exist for
        # this tender: reuse them instead of re-running the LLM.
        return get_requirements(tender_id)

    return generate_requirements(tender_id)


def check_tender_processing(tender_id):
    """Queue document processing + requirement extraction on the
    background worker and return immediately with the queued job.

    This is what web views should call (see web/views.py process_tender)
    instead of process_tender(), which runs synchronously and would block
    the request for as long as the LLM takes.
    """
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    from scripts.tender_queue import enqueue
    return enqueue(tender_id)


def get_tender_processing_status(tender_id):
    from scripts.tender_queue import get_status
    return get_status(tender_id)


def get_requirements(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    requirements_path = tender_dir / "requirement.json"
    if not requirements_path.exists():
        return None

    return json.loads(requirements_path.read_text(encoding="utf-8"))


BID_METADATA_FILENAME = "bid.json"


def create_bid(tender_id, metadata=None):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    bid_id = f"bid_{uuid.uuid4().hex[:8]}"
    bid_dir = tender_dir / "bids" / bid_id
    (bid_dir / "documents").mkdir(parents=True, exist_ok=True)

    if metadata:
        atomic_write_json(bid_dir / BID_METADATA_FILENAME, metadata)

    return bid_id


def get_bid_metadata(tender_id, bid_id):
    bid_dir = get_bid(tender_id, bid_id)
    if bid_dir is None:
        return None

    path = bid_dir / BID_METADATA_FILENAME
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def get_bid(tender_id, bid_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        return None

    bids_dir = tender_dir / "bids"
    bid_dir = bids_dir / bid_id
    return _resolve_within(bids_dir, bid_dir)


def list_bids(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    bids_dir = tender_dir / "bids"
    if not bids_dir.exists():
        return []

    return sorted(
        folder.name for folder in bids_dir.iterdir()
        if folder.is_dir()
    )


def add_bid_document(tender_id, bid_id, file_path):
    bid_dir = get_bid(tender_id, bid_id)
    if bid_dir is None:
        raise FileNotFoundError(f"Bid not found: {bid_id}")

    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if source.suffix.lower() != ".pdf":
        raise ValueError("Bid documents must be PDF files.")

    documents_dir = bid_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)

    destination = documents_dir / source.name
    shutil.copy2(source, destination)
    return destination


def process_bid_documents(tender_id, bid_id):
    bid_dir = get_bid(tender_id, bid_id)
    if bid_dir is None:
        raise FileNotFoundError(f"Bid not found: {bid_id}")

    text_extractor.process_document_folder(bid_dir / "documents")
    return True


def check_bid_compliance(tender_id, bid_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    from scripts.compliance_queue import enqueue
    return enqueue(tender_id, bid_id)


def get_analysis_status(tender_id, bid_id):
    from scripts.compliance_queue import get_status
    return get_status(tender_id, bid_id)


def process_bid(tender_id, bid_id):
    process_bid_documents(tender_id, bid_id)
    return check_bid_compliance(tender_id, bid_id)


def is_bid_analysis_stale(tender_id, bid_id):
    """True if this bid's documents have changed since its last completed
    compliance run - i.e. there's a completed/queued/processing analysis
    on record, but a newly-uploaded (or removed/edited) document means
    re-running would pick up something new.

    Returns False if no analysis has ever run yet (nothing to be "stale"
    relative to), even if the bid already has documents.
    """
    bid_dir = get_bid(tender_id, bid_id)
    if bid_dir is None:
        raise FileNotFoundError(f"Bid not found: {bid_id}")

    status = get_analysis_status(tender_id, bid_id) or {}
    recorded_hash = status.get("bid_documents_hash")
    if recorded_hash is None:
        return False

    from scripts.compliance_checker import compute_bid_documents_hash
    return compute_bid_documents_hash(bid_dir) != recorded_hash


def get_compliance(tender_id, bid_id):
    bid_dir = get_bid(tender_id, bid_id)
    if bid_dir is None:
        raise FileNotFoundError(f"Bid not found: {bid_id}")

    compliance_path = bid_dir / "evaluation.json"
    if not compliance_path.exists():
        return None

    return json.loads(compliance_path.read_text(encoding="utf-8"))


def get_documents(tender_id, bid_id=None):
    if bid_id is None:
        tender_dir = get_tender(tender_id)
        if tender_dir is None:
            raise FileNotFoundError(f"Tender not found: {tender_id}")
        documents_dir = tender_dir / "tender_documents"
    else:
        bid_dir = get_bid(tender_id, bid_id)
        if bid_dir is None:
            raise FileNotFoundError(f"Bid not found: {bid_id}")
        documents_dir = bid_dir / "documents"

    if not documents_dir.exists():
        return []

    return sorted(
        file.name for file in documents_dir.iterdir()
        if file.is_file() and file.suffix.lower() == ".pdf"
    )


if __name__ == "__main__":
    print("GeM Bid Compliance System")
    print("Tenders directory:", TENDERS_DIR)
