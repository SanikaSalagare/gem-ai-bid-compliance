from pathlib import Path
import shutil
import tempfile
import uuid

from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

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
# an existing PDF (they were written to accept files already on disk), so
# uploaded files are first streamed into a temporary staging folder and
# those paths are handed to the unmodified backend function.

_UPLOAD_STAGING_DIR = Path(tempfile.gettempdir()) / "sih_upload_staging"


def _stage_uploads(uploaded_files) -> tuple[Path, list[Path]]:
    """Stage a batch of uploaded files on disk and return (staging_dir,
    paths). Each call gets its own uuid-named subdirectory, so concurrent
    uploads - even of files that happen to share a name - never collide or
    clobber each other. Callers must remove `staging_dir` once they're
    done with the files (see _cleanup_staged).
    """
    staging_dir = _UPLOAD_STAGING_DIR / uuid.uuid4().hex
    staging_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for uploaded_file in uploaded_files:
        # uploaded_file.name is client-supplied and untrusted - take only
        # the final path component so a crafted name like "../../x.pdf"
        # can never land outside staging_dir.
        safe_name = Path(uploaded_file.name).name
        if not safe_name or safe_name in {".", ".."}:
            continue
        destination = staging_dir / safe_name
        with destination.open("wb") as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)
        paths.append(destination)

    return staging_dir, paths


def _cleanup_staged(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)


def _save_uploaded_documents(request, add_document, redirect_response):
    """Shared upload handling for both tender and bid document uploads:
    stage every file in request.FILES.getlist("documents"), hand each one
    to `add_document(path)`, report how many succeeded/failed, and always
    clean up the staging directory afterwards - regardless of how many
    documents were uploaded, this can now be called after AI
    processing/compliance analysis has already run, since neither
    manage.add_tender_document nor manage.add_bid_document gate on that.
    """
    uploaded_files = request.FILES.getlist("documents")
    if request.method != "POST" or not uploaded_files:
        return redirect_response

    staging_dir, staged_paths = _stage_uploads(uploaded_files)
    uploaded_count = 0
    errors = []

    try:
        for path in staged_paths:
            try:
                add_document(path)
                uploaded_count += 1
            except (FileNotFoundError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
    finally:
        _cleanup_staged(staging_dir)

    if uploaded_count:
        messages.success(
            request,
            f"{uploaded_count} document{'s' if uploaded_count != 1 else ''} uploaded.",
        )
    for error in errors:
        messages.error(request, error)

    return redirect_response


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
        "processing_status": manage.get_tender_processing_status(tender_id),
        "active_nav": "tenders",
    })


def upload_tender_document(request, tender_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    # Uploading is always allowed, including after requirements have
    # already been extracted - the next "Process Documents" run will pick
    # up any newly added file (see manage.process_tender_documents, which
    # re-hashes the whole document set and only skips re-extraction when
    # nothing has changed).
    redirect_response = redirect(f"{reverse('tender_detail', args=[tender_id])}?tab=documents")
    return _save_uploaded_documents(
        request,
        add_document=lambda path: manage.add_tender_document(tender_id, path),
        redirect_response=redirect_response,
    )


def process_tender(request, tender_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    if request.method == "POST":
        try:
            job = manage.check_tender_processing(tender_id)
            messages.success(request, f"Document processing queued as {job['job_id']}.")
        except Exception as exc:
            messages.error(request, f"Could not queue processing: {exc}")

    return redirect(f"{reverse('tender_detail', args=[tender_id])}?tab=requirements")


def tender_processing_status(request, tender_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")
    return JsonResponse(manage.get_tender_processing_status(tender_id) or {"status": "not_started"})


def requirement_detail(request, tender_id, requirement_id):
    if manage.get_tender(tender_id) is None:
        raise Http404("Tender not found")

    requirement = services.get_requirement(tender_id, requirement_id)
    if requirement is None:
        raise Http404("Requirement not found")

    from scripts.requirement_detector import REQUIREMENT_FIELDS
    extra_fields = {k: v for k, v in requirement.items() if k not in REQUIREMENT_FIELDS}

    return render(request, "web/requirement_detail.html", {
        "tender_id": tender_id,
        "tender_title": services.get_tender_title(tender_id),
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
        "tender_title": services.get_tender_title(tender_id),
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
        "tender_title": services.get_tender_title(tender_id),
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
        "tender_title": services.get_tender_title(tender_id),
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

    query = request.GET.get("q", "").strip().lower()
    if query:
        summaries = [
            s for s in summaries
            if query in s.title.lower() or query in s.tender_id.lower()
        ]

    return render(request, "web/seller_dashboard.html", {
        "tenders": summaries,
        "query": request.GET.get("q", ""),
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
        seller_id = request.session.get("seller_id", SELLERS[0]["id"])
        bid_id = manage.create_bid(tender_id, metadata={"seller_id": seller_id})
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
        "tender_title": services.get_tender_title(tender_id),
        "bid": summary,
        "analysis_status": manage.get_analysis_status(tender_id, bid_id),
        "active_nav": "seller",
    })


def seller_upload_bid_document(request, tender_id, bid_id):
    if manage.get_bid(tender_id, bid_id) is None:
        raise Http404("Bid not found")

    # Uploading is always allowed, including after a compliance analysis
    # has already completed - manage.add_bid_document doesn't gate on
    # analysis status, and manage.is_bid_analysis_stale (surfaced in the
    # template) tells the seller when it's worth re-running the check.
    redirect_response = redirect("seller_bid_detail", tender_id=tender_id, bid_id=bid_id)
    return _save_uploaded_documents(
        request,
        add_document=lambda path: manage.add_bid_document(tender_id, bid_id, path),
        redirect_response=redirect_response,
    )



