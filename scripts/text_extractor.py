import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from pypdf import PdfReader
from paddleocr import PaddleOCR

ROOT_DIR = Path(__file__).resolve().parent.parent

PDF_TYPE_DIGITAL = "DIGITAL"
PDF_TYPE_SCANNED = "SCANNED"

SCANNED_TEXT_THRESHOLD_CHARS = 50
SCANNED_PAGE_FRACTION = 0.5

_ocr_engine = None


def get_ocr_engine():
    global _ocr_engine

    if _ocr_engine is None:
        _ocr_engine = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="en",
        )

    return _ocr_engine


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def get_pdf_hash(pdf_path: Path) -> str:
    sha256 = hashlib.sha256()

    with pdf_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def read_existing_hash(txt_path: Path):
    if not txt_path.exists():
        return None

    try:
        first_line = txt_path.read_text(
            encoding="utf-8"
        ).splitlines()[0]
    except (OSError, IndexError):
        return None

    if first_line.startswith("HASH:"):
        return first_line.split("HASH:", 1)[1].strip()

    return None


def rotate_backups(txt_path: Path) -> None:
    backup1 = txt_path.with_name(txt_path.name + ".backup1")
    backup2 = txt_path.with_name(txt_path.name + ".backup2")

    if backup2.exists():
        backup2.unlink()

    if backup1.exists():
        backup1.rename(backup2)

    if txt_path.exists():
        txt_path.rename(backup1)


def is_scanned(pdf_path: Path) -> bool:
    reader = PdfReader(pdf_path)

    if not reader.pages:
        return False

    pages_with_text = sum(
        1
        for page in reader.pages
        if len((page.extract_text() or "").strip())
        > SCANNED_TEXT_THRESHOLD_CHARS
    )

    return pages_with_text < len(reader.pages) * SCANNED_PAGE_FRACTION


def extract_digital_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(pdf_path)
    pages = []

    for page in reader.pages:
        text = page.extract_text() or "[No text detected]"
        pages.append(clean_text(text))

    return pages


def extract_scanned_pages(pdf_path: Path) -> list[str]:
    ocr = get_ocr_engine()
    results = ocr.predict(input=str(pdf_path))
    pages = []

    for result in results:
        rec_texts = result["rec_texts"]
        page_text = "\n".join(rec_texts)

        pages.append(
            clean_text(page_text)
            if page_text.strip()
            else "[No text detected]"
        )

    return pages


def build_text_content(
    pdf_hash: str,
    pdf_name: str,
    pdf_type: str,
    pages: list[str],
) -> str:
    header = (
        f"HASH: {pdf_hash}\n"
        f"SOURCE: {pdf_name}\n"
        f"PDF TYPE: {pdf_type}\n"
        f"PROCESSED: {datetime.now(timezone.utc).isoformat()}\n\n"
    )

    body = "\n\n".join(
        f"<[PAGE {index}]>\n{page_text}"
        for index, page_text in enumerate(pages, start=1)
    )

    return header + body + "\n"


def process_pdf(pdf_path: Path, processed_dir: Path) -> bool:
    processed_dir.mkdir(parents=True, exist_ok=True)

    txt_path = processed_dir / f"{pdf_path.stem}.txt"
    current_hash = get_pdf_hash(pdf_path)
    existing_hash = read_existing_hash(txt_path)

    if existing_hash == current_hash:
        return False

    pdf_type = (
        PDF_TYPE_SCANNED
        if is_scanned(pdf_path)
        else PDF_TYPE_DIGITAL
    )

    try:
        pages = (
            extract_scanned_pages(pdf_path)
            if pdf_type == PDF_TYPE_SCANNED
            else extract_digital_pages(pdf_path)
        )
    except Exception as exc:
        print(f"ERROR extracting {pdf_path.name}: {exc}")
        return False

    if txt_path.exists():
        rotate_backups(txt_path)

    txt_path.write_text(
        build_text_content(
            current_hash,
            pdf_path.name,
            pdf_type,
            pages,
        ),
        encoding="utf-8",
    )

    return True


def process_document_folder(document_folder: Path) -> None:
    document_folder = Path(document_folder)
    processed_dir = document_folder / "processed"

    for pdf_path in sorted(document_folder.glob("*.pdf")):
        action = (
            "PROCESSED"
            if process_pdf(pdf_path, processed_dir)
            else "SKIPPED"
        )
        print(f"{action}: {pdf_path}")


def process_tender(tender_dir: Path) -> None:
    tender_dir = Path(tender_dir)

    if not tender_dir.exists():
        raise FileNotFoundError(
            f"Tender directory not found: {tender_dir}"
        )

    tender_documents = tender_dir / "tender_documents"

    if tender_documents.exists():
        process_document_folder(tender_documents)

    bids_dir = tender_dir / "bids"

    if bids_dir.exists():
        for bid_dir in sorted(
            p for p in bids_dir.iterdir() if p.is_dir()
        ):
            documents_dir = bid_dir / "documents"

            if documents_dir.exists():
                process_document_folder(documents_dir)


def process_all_tenders(tenders_dir: Path | None = None) -> None:
    tenders_dir = Path(
        tenders_dir or ROOT_DIR / "data" / "TENDERS"
    )

    if not tenders_dir.exists():
        raise FileNotFoundError(
            f"Tenders directory not found: {tenders_dir}"
        )

    for tender_dir in sorted(
        p for p in tenders_dir.iterdir() if p.is_dir()
    ):
        process_tender(tender_dir)


if __name__ == "__main__":
    process_all_tenders()
