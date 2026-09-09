from pathlib import Path
import json
import uuid

from scripts import text_extractor
from scripts import requirement_detector
from scripts import compliance_checker


ROOT_DIR = Path(__file__).resolve().parent
TENDERS_DIR = ROOT_DIR / "data" / "TENDERS"


def create_tender():
    tender_id = f"tender_{uuid.uuid4().hex[:8]}"

    tender_dir = TENDERS_DIR / tender_id

    (tender_dir / "tender_documents").mkdir(
        parents=True,
        exist_ok=True
    )

    (tender_dir / "bids").mkdir(
        parents=True,
        exist_ok=True
    )

    return tender_id


def get_tender(tender_id):
    tender_dir = TENDERS_DIR / tender_id

    if not tender_dir.exists():
        return None

    return tender_dir


def list_tenders():
    if not TENDERS_DIR.exists():
        return []

    return [
        folder.name
        for folder in TENDERS_DIR.iterdir()
        if folder.is_dir()
    ]


def delete_tender(tender_id):
    import shutil

    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        return False

    shutil.rmtree(tender_dir)

    return True


def add_tender_document(tender_id, file_path):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    source = Path(file_path)

    if not source.exists():
        raise FileNotFoundError(
            f"File not found: {file_path}"
        )

    destination = (
        tender_dir
        / "tender_documents"
        / source.name
    )

    destination.write_bytes(
        source.read_bytes()
    )

    return destination


def process_tender_documents(tender_id):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    text_extractor.process_tender(
        tender_dir
    )

    return True


def generate_requirements(tender_id):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    requirements = (
        requirement_detector.process_tender(
            tender_dir
        )
    )

    return requirements


def process_tender(tender_id):
    process_tender_documents(tender_id)

    requirements = generate_requirements(
        tender_id
    )

    return requirements


def get_requirements(tender_id):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    requirements_path = (
        tender_dir / "req.json"
    )

    if not requirements_path.exists():
        return None

    with open(
        requirements_path,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def create_bid(tender_id):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    seller_id = f"seller_{uuid.uuid4().hex[:8]}"

    seller_dir = (
        tender_dir
        / "bids"
        / seller_id
    )

    seller_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return seller_id


def get_bid(tender_id, seller_id):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        return None

    seller_dir = (
        tender_dir
        / "bids"
        / seller_id
    )

    if not seller_dir.exists():
        return None

    return seller_dir


def list_bids(tender_id):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    bids_dir = tender_dir / "bids"

    if not bids_dir.exists():
        return []

    return [
        folder.name
        for folder in bids_dir.iterdir()
        if folder.is_dir()
    ]


def add_bid_document(
    tender_id,
    seller_id,
    file_path
):
    seller_dir = get_bid(
        tender_id,
        seller_id
    )

    if seller_dir is None:
        raise FileNotFoundError(
            f"Bid not found: {seller_id}"
        )

    source = Path(file_path)

    if not source.exists():
        raise FileNotFoundError(
            f"File not found: {file_path}"
        )

    destination = (
        seller_dir / source.name
    )

    destination.write_bytes(
        source.read_bytes()
    )

    return destination


def process_bid_documents(
    tender_id,
    seller_id
):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    seller_dir = get_bid(
        tender_id,
        seller_id
    )

    if seller_dir is None:
        raise FileNotFoundError(
            f"Bid not found: {seller_id}"
        )

    text_extractor.process_document_folder(
        seller_dir
    )

    return True


def check_bid_compliance(
    tender_id,
    seller_id
):
    tender_dir = get_tender(tender_id)

    if tender_dir is None:
        raise FileNotFoundError(
            f"Tender not found: {tender_id}"
        )

    result = compliance_checker.process_bid(
        tender_dir,
        seller_id
    )

    return result


def process_bid(
    tender_id,
    seller_id
):
    process_bid_documents(
        tender_id,
        seller_id
    )

    return check_bid_compliance(
        tender_id,
        seller_id
    )


def get_compliance(
    tender_id,
    seller_id
):
    seller_dir = get_bid(
        tender_id,
        seller_id
    )

    if seller_dir is None:
        raise FileNotFoundError(
            f"Bid not found: {seller_id}"
        )

    compliance_path = (
        seller_dir / "compliance.json"
    )

    if not compliance_path.exists():
        return None

    with open(
        compliance_path,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def get_documents(
    tender_id,
    seller_id=None
):
    if seller_id is None:
        tender_dir = get_tender(tender_id)

        if tender_dir is None:
            raise FileNotFoundError(
                f"Tender not found: {tender_id}"
            )

        documents_dir = (
            tender_dir
            / "tender_documents"
        )
    else:
        seller_dir = get_bid(
            tender_id,
            seller_id
        )

        if seller_dir is None:
            raise FileNotFoundError(
                f"Bid not found: {seller_id}"
            )

        documents_dir = seller_dir

    if not documents_dir.exists():
        return []

    return [
        file.name
        for file in documents_dir.iterdir()
        if file.is_file()
        and file.suffix.lower() == ".pdf"
    ]


if __name__ == "__main__":
    print("GeM Bid Compliance System")
    print("Tenders directory:", TENDERS_DIR)