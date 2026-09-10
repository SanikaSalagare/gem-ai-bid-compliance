from pathlib import Path
import json
import uuid
import shutil

from scripts import text_extractor
from scripts import requirement_detector
from scripts import compliance_checker


ROOT_DIR = Path(__file__).resolve().parent
TENDERS_DIR = ROOT_DIR / "data" / "TENDERS"


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
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def update_tender_metadata(tender_id, metadata):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")
    path = tender_dir / "tender.json"
    path.write_text(json.dumps(metadata, indent=4, ensure_ascii=False), encoding="utf-8")


def get_tender(tender_id):
    tender_dir = TENDERS_DIR / tender_id
    return tender_dir if tender_dir.exists() and tender_dir.is_dir() else None


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


def process_tender_documents(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    text_extractor.process_tender(tender_dir)
    return True


def generate_requirements(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    return requirement_detector.process_tender(tender_dir)


def process_tender(tender_id):
    process_tender_documents(tender_id)
    return generate_requirements(tender_id)


def get_requirements(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    requirements_path = tender_dir / "requirement.json"
    if not requirements_path.exists():
        return None

    return json.loads(requirements_path.read_text(encoding="utf-8"))


def create_bid(tender_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        raise FileNotFoundError(f"Tender not found: {tender_id}")

    bid_id = f"bid_{uuid.uuid4().hex[:8]}"
    bid_dir = tender_dir / "bids" / bid_id / "documents"
    bid_dir.mkdir(parents=True, exist_ok=True)

    return bid_id


def get_bid(tender_id, bid_id):
    tender_dir = get_tender(tender_id)
    if tender_dir is None:
        return None

    bid_dir = tender_dir / "bids" / bid_id
    return bid_dir if bid_dir.exists() and bid_dir.is_dir() else None


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
