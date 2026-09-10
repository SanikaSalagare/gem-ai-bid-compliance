from pathlib import Path
import tempfile

from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render

import manage
from web import services
from web.demo_accounts import BUYERS, SELLERS


# ---------------------------------------------------------------------------
# Marketing / landing
# ---------------------------------------------------------------------------

def home(request):
    return render(request, "web/home.html")


def switch_account(request):
    if request.method == "POST":
        if request.POST.get("buyer_id") in {a["id"] for a in BUYERS}:
            request.session["buyer_id"] = request.POST["buyer_id"]
        if request.POST.get("seller_id") in {a["id"] for a in SELLERS}:
            request.session["seller_id"] = request.POST["seller_id"]
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or "home")


# ---------------------------------------------------------------------------
# Upload staging
# ---------------------------------------------------------------------------
# manage.add_tender_document / add_bid_document expect a filesystem path to
# an existing PDF (they were written to accept files already on disk), so an
# uploaded file is first streamed into a temporary staging folder and that
# path is handed to the unmodified backend function.

_UPLOAD_STAGING_DIR = Path(tempfile.gettempdir()) / "sih_upload_staging"


def _stage_upload(uploaded_file) -> Path:
    _UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    destination = _UPLOAD_STAGING_DIR / uploaded_file.name
    with destination.open("wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
    return destination


# ---------------------------------------------------------------------------
# Buyer portal
# ---------------------------------------------------------------------------

def buyer_dashboard(request):
    tender_ids = manage.list_tenders()
    summaries = [services.get_tender_summary(tid) for tid in tender_ids]
    summaries = [s for s in summaries if s is not None]

    total_bids = sum(s.bid_count for s in summaries)
    ready = sum(1 for s in summaries if s.requirements_ready)

    context = {
        "stats": {
            "total_tenders": len(summaries),
            "requirements_ready": ready,
            "awaiting_processing": len(summaries) - ready,
            "total_bids": total_bids,
        },
        "recent_tenders": summaries[-5:][::-1],
        "active_nav": "dashboard",
    }
    return render(request, "web/buyer_dashboard.html", context)


def tender_list(request):
    tender_ids = manage.list_tenders()
    summaries = [services.get_tender_summary(tid) for tid in tender_ids]
    summaries = [s for s in summaries if s is not None]

    query = request.GET.get("q", "").strip().lower()
    status_filter = request.GET.get("status", "")

    if query:
        summaries = [s for s in summaries if query in s.tender_id.lower()]

    if status_filter == "ready":
        summaries = [s for s in summaries if s.requirements_ready]
    elif status_filter == "pending":
        summaries = [s for s in summaries if not s.requirements_ready]

    return render(request, "web/tenders.html", {
        "tenders": summaries,
        "query": request.GET.get("q", ""),
        "status_filter": status_filter,
        "active_nav": "tenders",
    })


def create_tender(request):
    if request.method == "POST":
        metadata = {
            "title": request.POST.get("title", "").strip() or "Untitled GeM Procurement",
            "category": request.POST.get("category", "").strip() or "General",
            "quantity": request.POST.get("quantity", "").strip(),
            "bid_type": request.POST.get("bid_type", "Open Bid").strip(),
            "delivery_period": request.POST.get("delivery_period", "").strip(),
            "bid_validity": request.POST.get("bid_validity", "").strip(),
            "emd_required": request.POST.get("emd_required") == "on",
            "performance_security_required": request.POST.get("performance_security_required") == "on",
            "eligibility": request.POST.get("eligibility", "").strip(),
            "description": request.POST.get("description", "").strip(),
            "buyer_id": request.session.get("buyer_id", BUYERS[0]["id"]),
        }
        tender_id = manage.create_tender(metadata)
        messages.success(request, f"Tender {tender_id} created.")
        return redirect("tender_detail", tender_id=tender_id)
    return render(request, "web/tender_create.html", {
        "active_nav": "tenders",
    })


def tender_detail(request, tender_id):
    summary = services.get_tender_summary(tender_id)
    if summary is None:
        raise Http404("Tender not found")

    bid_summaries = [services.get_bid_summary(tender_id, bid_id) for bid_id in summary.bids]
    bid_summaries = [b for b in bid_summaries if b is not None]

    tab = request.GET.get("tab", "overview")
    if tab not in {"overview", "requirements", "documents", "bids"}:
        tab = "overview"

    return render(request, "web/tender_detail.html", {
        "tender": summary,
        "bids": bid_summaries,
        "tab": tab,
        "active_nav": "tenders",
    })


def upload_tender_document(request, tender_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    if request.method == "POST" and request.FILES.get("document"):
        try:
            manage.add_tender_document(tender_id, _stage_upload(request.FILES["document"]))
            messages.success(request, "Document uploaded.")
        except (FileNotFoundError, ValueError) as exc:
            messages.error(request, str(exc))

    return redirect(f"{redirect('tender_detail', tender_id=tender_id).url}?tab=documents")


def process_tender(request, tender_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    if request.method == "POST":
        try:
            manage.process_tender(tender_id)
            messages.success(request, "Documents processed and requirements extracted.")
        except Exception as exc:  # backend processing failure, not a frontend bug
            messages.error(request, f"Processing failed: {exc}")

    return redirect(f"{redirect('tender_detail', tender_id=tender_id).url}?tab=requirements")


def requirement_detail(request, tender_id, requirement_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    requirement = services.get_requirement(tender_id, requirement_id)
    if requirement is None:
        raise Http404("Requirement not found")

    known_fields = {"id", "requirement", "file", "page"}
    extra_fields = {k: v for k, v in requirement.items() if k not in known_fields}

    return render(request, "web/requirement_detail.html", {
        "tender_id": tender_id,
        "requirement": requirement,
        "extra_fields": extra_fields,
        "active_nav": "tenders",
    })


def bid_detail(request, tender_id, bid_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    summary = services.get_bid_summary(tender_id, bid_id)
    if summary is None:
        raise Http404("Bid not found")

    return render(request, "web/bid_detail.html", {
        "tender_id": tender_id,
        "bid": summary,
        "analysis_status": manage.get_analysis_status(tender_id, bid_id),
        "active_nav": "tenders",
    })


def process_bid(request, tender_id, bid_id):
    if manage.get_bid(tender_id, bid_id) is None:
        raise Http404("Bid not found")

    if request.method == "POST":
        try:
            job = manage.check_bid_compliance(tender_id, bid_id)
            messages.success(request, f"Compliance analysis queued as {job['job_id']}.")
        except Exception as exc:
            messages.error(request, f"Could not queue analysis: {exc}")

    return redirect("bid_detail", tender_id=tender_id, bid_id=bid_id)


def compliance_detail(request, tender_id, bid_id, requirement_id):
    if manage.get_bid(tender_id, bid_id) is None:
        raise Http404("Bid not found")

    row = services.get_compliance_row(tender_id, bid_id, requirement_id)
    if row is None:
        raise Http404("Requirement not found")

    return render(request, "web/compliance_detail.html", {
        "tender_id": tender_id,
        "bid_id": bid_id,
        "row": row,
        "active_nav": "tenders",
    })


def document_viewer(request, tender_id, filename, bid_id=None):
    if bid_id is None:
        if manage.get_tender(tender_id) is None:
            raise Http404("Tender not found")
        available = manage.get_documents(tender_id)
    else:
        if manage.get_bid(tender_id, bid_id) is None:
            raise Http404("Bid not found")
        available = manage.get_documents(tender_id, bid_id)

    if filename not in available:
        raise Http404("Document not found")

    pages = services.get_document_pages(tender_id, filename, bid_id)
    requested_page = request.GET.get("page")
    try:
        requested_page = int(requested_page) if requested_page else None
    except ValueError:
        requested_page = None

    return render(request, "web/document_viewer.html", {
        "tender_id": tender_id,
        "bid_id": bid_id,
        "filename": filename,
        "documents": available,
        "pages": pages,
        "processed": services.is_document_processed(tender_id, filename, bid_id),
        "requested_page": requested_page,
        "view": request.GET.get("view", "text"),
        "active_nav": "tenders",
    })


def document_pdf(request, tender_id, filename, bid_id=None):
    if bid_id is None:
        tender_dir = manage.get_tender(tender_id)
        if tender_dir is None:
            raise Http404("Tender not found")
        document_dir = tender_dir / "tender_documents"
    else:
        bid_dir = manage.get_bid(tender_id, bid_id)
        if bid_dir is None:
            raise Http404("Bid not found")
        document_dir = bid_dir / "documents"

    pdf_path = (document_dir / filename).resolve()
    try:
        pdf_path.relative_to(document_dir.resolve())
    except ValueError:
        raise Http404("Document not found")
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise Http404("Document not found")
    return FileResponse(pdf_path.open("rb"), content_type="application/pdf")


def analysis_status(request, tender_id, bid_id):
    if manage.get_bid(tender_id, bid_id) is None:
        raise Http404("Bid not found")
    return JsonResponse(manage.get_analysis_status(tender_id, bid_id) or {"status": "not_started"})


# ---------------------------------------------------------------------------
# Seller portal
# ---------------------------------------------------------------------------

def seller_dashboard(request):
    tender_ids = manage.list_tenders()
    summaries = [services.get_tender_summary(tid) for tid in tender_ids]
    summaries = [s for s in summaries if s is not None and s.requirements_ready]

    return render(request, "web/seller_dashboard.html", {
        "tenders": summaries,
        "active_nav": "seller",
    })


def seller_tender_detail(request, tender_id):
    summary = services.get_tender_summary(tender_id)
    if summary is None:
        raise Http404("Tender not found")

    bid_summaries = [services.get_bid_summary(tender_id, bid_id) for bid_id in summary.bids]
    bid_summaries = [b for b in bid_summaries if b is not None]

    return render(request, "web/seller_tender_detail.html", {
        "tender": summary,
        "bids": bid_summaries,
        "active_nav": "seller",
    })


def seller_create_bid(request, tender_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    if request.method == "POST":
        bid_id = manage.create_bid(tender_id)
        messages.success(request, f"Bid {bid_id} created. Upload your documents to continue.")
        return redirect("seller_bid_detail", tender_id=tender_id, bid_id=bid_id)

    return redirect("seller_tender_detail", tender_id=tender_id)


def seller_bid_detail(request, tender_id, bid_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    summary = services.get_bid_summary(tender_id, bid_id)
    if summary is None:
        raise Http404("Bid not found")

    return render(request, "web/seller_bid_detail.html", {
        "tender_id": tender_id,
        "bid": summary,
        "analysis_status": manage.get_analysis_status(tender_id, bid_id),
        "active_nav": "seller",
    })


def seller_upload_bid_document(request, tender_id, bid_id):
    if manage.get_bid(tender_id, bid_id) is None:
        raise Http404("Bid not found")

    if request.method == "POST" and request.FILES.get("document"):
        try:
            manage.add_bid_document(tender_id, bid_id, _stage_upload(request.FILES["document"]))
            messages.success(request, "Document uploaded.")
        except (FileNotFoundError, ValueError) as exc:
            messages.error(request, str(exc))

    return redirect("seller_bid_detail", tender_id=tender_id, bid_id=bid_id)



