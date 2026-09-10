"""
Frontend data-access layer.

This module ONLY reads data that the existing backend (manage.py /
scripts/*) has already produced, and shapes it for templates. It never
performs PDF extraction, OCR, requirement extraction or compliance
scoring — that logic lives in scripts/text_extractor.py,
scripts/requirement_detector.py and scripts/compliance_checker.py and
is invoked here only through the existing manage.py functions.

The only "new" logic in this file is presentation-only:
- turning a raw 0-100 compliance score into a small set of labelled
  buckets (COMPLIANT / PARTIALLY_COMPLIANT / NON_COMPLIANT / MISSING)
  so the UI can show a consistent badge, and
- counting/summing those buckets for simple on-screen statistics.

Nothing here is stored back to disk and nothing here changes how the
backend classifies or scores a bid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import manage

PAGE_MARKER_RE = re.compile(r"<\[PAGE\s+(\d+)\]>")

# ---------------------------------------------------------------------------
# Compliance status buckets (display-only; backend only returns a 0-100
# score). Thresholds are isolated here so they are easy to see and tune,
# and are never used to alter the score itself.
# ---------------------------------------------------------------------------
STATUS_COMPLIANT = "COMPLIANT"
STATUS_PARTIAL = "PARTIALLY_COMPLIANT"
STATUS_NON_COMPLIANT = "NON_COMPLIANT"
STATUS_MISSING = "MISSING"
STATUS_PENDING = "PENDING"
STATUS_UNKNOWN = "UNKNOWN"

COMPLIANT_THRESHOLD = 80
PARTIAL_THRESHOLD = 40

STATUS_META = {
    STATUS_COMPLIANT: {"label": "Compliant", "icon": "\u2713", "css": "compliant"},
    STATUS_PARTIAL: {"label": "Partially Compliant", "icon": "\u26a0", "css": "partial"},
    STATUS_NON_COMPLIANT: {"label": "Non-Compliant", "icon": "\u2715", "css": "non-compliant"},
    STATUS_MISSING: {"label": "Missing", "icon": "!", "css": "missing"},
    STATUS_PENDING: {"label": "Pending", "icon": "\u25cb", "css": "pending"},
    STATUS_UNKNOWN: {"label": "Unknown", "icon": "?", "css": "unknown"},
}


def classify_score(evaluation_entry: Optional[dict]) -> str:
    """Map one evaluation.json entry to a display status bucket."""
    if evaluation_entry is None:
        return STATUS_UNKNOWN

    score = evaluation_entry.get("score")
    has_evidence = bool(evaluation_entry.get("file")) or bool(evaluation_entry.get("evidence"))

    if score is None:
        return STATUS_PENDING
    if score == 0 and not has_evidence:
        return STATUS_MISSING
    if score >= COMPLIANT_THRESHOLD:
        return STATUS_COMPLIANT
    if score >= PARTIAL_THRESHOLD:
        return STATUS_PARTIAL
    return STATUS_NON_COMPLIANT



def score_label(score):
    if score is None:
        return "Pending"
    score = max(0, min(100, int(score)))
    if score >= 80:
        return "Highly Compliant"
    if score >= 60:
        return "Mostly Compliant"
    if score >= 40:
        return "Needs Review"
    return "Poor Compliance"

def status_meta(status: str) -> dict:
    return STATUS_META.get(status, STATUS_META[STATUS_UNKNOWN])


# ---------------------------------------------------------------------------
# Tenders
# ---------------------------------------------------------------------------

@dataclass
class TenderSummary:
    tender_id: str
    requirements: Optional[list]
    bids: list
    documents: list
    metadata: dict
    requirements_ready: bool = field(init=False)

    def __post_init__(self):
        self.requirements_ready = self.requirements is not None

    @property
    def requirement_count(self):
        return len(self.requirements) if self.requirements else 0

    @property
    def bid_count(self):
        return len(self.bids)


def get_all_tenders() -> list[str]:
    return manage.list_tenders()


def get_tender_summary(tender_id: str) -> Optional[TenderSummary]:
    if manage.get_tender(tender_id) is None:
        return None

    requirements = manage.get_requirements(tender_id)
    bids = manage.list_bids(tender_id)
    documents = manage.get_documents(tender_id)
    metadata = manage.get_tender_metadata(tender_id) or {}

    return TenderSummary(
        tender_id=tender_id,
        requirements=requirements,
        bids=bids,
        documents=documents,
        metadata=metadata,
    )


def get_requirement(tender_id: str, requirement_id: int) -> Optional[dict]:
    requirements = manage.get_requirements(tender_id) or []
    for item in requirements:
        if item.get("id") == requirement_id:
            return item
    return None


# ---------------------------------------------------------------------------
# Bids / compliance
# ---------------------------------------------------------------------------

@dataclass
class ComplianceRow:
    requirement: dict
    evaluation: Optional[dict]
    status: str
    status_meta: dict


@dataclass
class BidSummary:
    bid_id: str
    documents: list
    rows: list  # list[ComplianceRow]
    evaluation_ready: bool
    counts: dict
    overall_score: Optional[int]
    overall_status: str
    overall_label: str


def get_bid_summary(tender_id: str, bid_id: str) -> Optional[BidSummary]:
    if manage.get_bid(tender_id, bid_id) is None:
        return None

    requirements = manage.get_requirements(tender_id) or []
    evaluation = manage.get_compliance(tender_id, bid_id)
    documents = manage.get_documents(tender_id, bid_id)

    evaluation_by_id = {}
    if evaluation:
        evaluation_by_id = {entry.get("id"): entry for entry in evaluation}

    rows = []
    counts = {key: 0 for key in STATUS_META}
    scored = []

    for requirement in requirements:
        entry = evaluation_by_id.get(requirement.get("id"))
        status = classify_score(entry) if evaluation is not None else STATUS_PENDING
        rows.append(
            ComplianceRow(
                requirement=requirement,
                evaluation=entry,
                status=status,
                status_meta=status_meta(status),
            )
        )
        counts[status] += 1
        if entry is not None and entry.get("score") is not None:
            scored.append(entry["score"])

    overall_score = round(sum(scored) / len(scored)) if scored else None

    overall_status = classify_score({"score": overall_score, "evidence": True}) if overall_score is not None else STATUS_PENDING
    overall_label = score_label(overall_score)

    return BidSummary(
        bid_id=bid_id,
        documents=documents,
        rows=rows,
        evaluation_ready=evaluation is not None,
        counts=counts,
        overall_score=overall_score,
        overall_status=overall_status,
        overall_label=overall_label,
    )


def get_compliance_row(tender_id: str, bid_id: str, requirement_id: int) -> Optional[ComplianceRow]:
    summary = get_bid_summary(tender_id, bid_id)
    if summary is None:
        return None
    for row in summary.rows:
        if row.requirement.get("id") == requirement_id:
            return row
    return None


# ---------------------------------------------------------------------------
# Documents / processed page text
# ---------------------------------------------------------------------------

def _processed_txt_path(tender_id: str, filename: str, bid_id: Optional[str] = None) -> Optional[Path]:
    if bid_id is None:
        tender_dir = manage.get_tender(tender_id)
        if tender_dir is None:
            return None
        processed_dir = tender_dir / "tender_documents" / "processed"
    else:
        bid_dir = manage.get_bid(tender_id, bid_id)
        if bid_dir is None:
            return None
        processed_dir = bid_dir / "documents" / "processed"

    return processed_dir / f"{Path(filename).stem}.txt"


def get_document_pages(tender_id: str, filename: str, bid_id: Optional[str] = None) -> list[dict]:
    """Read an already-processed .txt file and split it back into pages.

    This only re-reads text the backend already extracted (page markers
    written by scripts/text_extractor.py); it performs no PDF reading,
    OCR or text extraction of its own.
    """
    txt_path = _processed_txt_path(tender_id, filename, bid_id)
    if txt_path is None or not txt_path.exists():
        return []

    content = txt_path.read_text(encoding="utf-8", errors="ignore")
    matches = list(PAGE_MARKER_RE.finditer(content))
    pages = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        pages.append({
            "page": int(match.group(1)),
            "text": content[start:end].strip(),
        })

    return pages


def is_document_processed(tender_id: str, filename: str, bid_id: Optional[str] = None) -> bool:
    txt_path = _processed_txt_path(tender_id, filename, bid_id)
    return bool(txt_path and txt_path.exists())
