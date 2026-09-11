from pathlib import Path
import hashlib
import json
import re

from scripts.model_manager import LLM_LOCK, get_llm, reset_context
from scripts.atomic_io import atomic_write_json, merge_write_json
from scripts import text_extractor

ROOT_DIR = Path(__file__).resolve().parent.parent

OUTPUT_FILENAME = "evaluation.json"
STATUS_FILENAME = "analysis_status.json"



def load_requirements(tender_dir):
    path = tender_dir / "requirement.json"

    if not path.exists():
        raise FileNotFoundError(
            f"Requirement file not found: {path}"
        )

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def load_pages(bid_dir):
    return text_extractor.read_processed_pages(bid_dir / "documents" / "processed")


def find_relevant_pages(requirement, pages, limit=4):
    """Pick the `limit` pages most likely to contain evidence for
    `requirement`, ranked by shared-keyword overlap.

    If nothing overlaps at all - e.g. the bid document phrases the same
    spec differently than the tender ("CPU" vs "Processor") - this used to
    return an empty list, which made evaluate_requirement() score the
    requirement 0 without ever consulting the model. Fall back to the
    first `limit` pages instead, so the LLM still gets a chance to find
    evidence that doesn't share vocabulary with the requirement text; a
    genuine lack of evidence is then a model judgement, not a keyword
    miss.
    """
    words = {
        word.lower()
        for word in re.findall(
            r"[A-Za-z0-9]{4,}",
            requirement,
        )
    }

    scored = []

    for page in pages:
        page_words = set(
            re.findall(
                r"[A-Za-z0-9]{4,}",
                page["text"].lower(),
            )
        )

        score = len(words & page_words)

        if score:
            scored.append((score, page))

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    top = [page for _, page in scored[:limit]]

    return top if top else pages[:limit]


def build_prompt(requirement, pages):
    evidence_text = "\n\n".join(
        f'FILE: {page["file"]}\n'
        f'PAGE: {page["page"]}\n'
        f'{page["text"]}'
        for page in pages
    )

    return f"""
You are a procurement compliance reviewer for an Indian Government
e-Marketplace (GeM) tender. Your task is to decide, strictly from the
seller evidence provided below, how well the seller's bid satisfies
ONE specific tender requirement.

REQUIREMENT TO CHECK
{json.dumps(requirement, ensure_ascii=False)}

SELLER EVIDENCE (your only source of truth - do not use outside
knowledge of products, standards, or typical specifications)
{evidence_text}

HOW TO DECIDE
1. Identify the exact condition the requirement imposes: a number, a
   standard/certification, a delivery or commercial term, etc.
2. Search the evidence for a passage addressing that exact condition,
   even if it uses different wording for the same thing (e.g. "CPU"
   for "Processor", "12 months" for "1 year").
3. Compare what the evidence actually states against what the
   requirement asks for:
   - An equal-or-better spec satisfies the requirement (32 GB RAM
     satisfies "Minimum 16 GB RAM").
   - A lesser, vaguer, or unstated spec does not, even if it is close.
   - Compliance is a spectrum: score the degree to which it is met
     rather than forcing a binary pass/fail.
4. Never invent, assume, or infer a fact, number, or certification
   that is not written in the evidence. Evidence that is silent on the
   requirement means "no evidence found" - not a pass.

SCORING SCALE (integer 0-100)
- 100: evidence completely and unambiguously satisfies the requirement.
- 90-99: essentially complete compliance; only a trivial, non-material gap.
- 75-89: strong compliance; one meaningful detail is incomplete or unclear.
- 60-74: substantial compliance; more of the requirement is met than missing.
- 40-59: mixed evidence; important parts of the requirement are missing or unclear.
- 20-39: weak evidence that clearly falls short of the requirement.
- 1-19: evidence exists but offers almost no real support for compliance.
- 0: no relevant evidence anywhere in the supplied pages, OR the evidence directly contradicts/fails the requirement.
Use the full scale. Do not default to a round number (0, 50, 100)
just because the judgment call is hard - an honest intermediate score
is more useful than a false binary one.

OUTPUT
Return ONLY valid JSON - no markdown fences, no commentary, no keys
other than these four:
{{
    "score": <integer 0-100>,
    "file": <source filename of the strongest supporting passage, or null if score is 0>,
    "page": <page number of that passage, or null if score is 0>,
    "evidence": <one short sentence quoting/closely grounded in that passage - never a stronger claim than the source supports - or null if score is 0>
}}
"""


def evaluate_requirement(llm, requirement, pages):
    relevant = find_relevant_pages(
        requirement["requirement"],
        pages,
    )

    if not relevant:
        return {
            "id": requirement["id"],
            "score": 0,
            "file": None,
            "page": None,
            "evidence": None,
        }

    prompt = build_prompt(requirement, relevant)

    # Held for the whole call: a llama_cpp Llama instance can't safely
    # serve two concurrent create_chat_completion() calls, and this LLM is
    # shared with scripts/requirement_detector.py's background worker.
    with LLM_LOCK:
        response = llm.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You evaluate procurement requirements "
                        "using only supplied seller evidence."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.25,
            response_format={"type": "json_object"},
        )

    result = json.loads(
        response["choices"][0]["message"]["content"]
    )

    try:
        score = max(
            0,
            min(100, int(result.get("score", 0))),
        )
    except (TypeError, ValueError):
        score = 0

    file_name = result.get("file")
    page = result.get("page")
    evidence = result.get("evidence")

    if file_name is not None:
        file_name = str(file_name)

    if page is not None:
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = None

    return {
        "id": requirement["id"],
        "score": score,
        "file": file_name,
        "page": page,
        "evidence": evidence,
    }


def compute_bid_documents_hash(bid_dir):
    """A single hash representing the current set of bidder files.

    Changes if any file under bid_dir/documents is added, removed, or
    modified (each file's own SHA-256 changes when its content changes).
    """
    documents_dir = bid_dir / "documents"
    parts = []

    if documents_dir.exists():
        for pdf_path in sorted(documents_dir.glob("*.pdf")):
            parts.append(
                f"{pdf_path.name}:{text_extractor.get_pdf_hash(pdf_path)}"
            )

    combined = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def compute_requirement_hash(requirement):
    payload = json.dumps(requirement, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_input_hash(requirement_hash, bid_documents_hash):
    combined = f"{requirement_hash}:{bid_documents_hash}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def load_status(bid_dir):
    path = bid_dir / STATUS_FILENAME

    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def save_status(bid_dir, bid_documents_hash, requirements_status):
    # Merge (not overwrite): compliance_queue.py also writes job-level
    # keys ("status", "error", timestamps, ...) onto this same file.
    merge_write_json(
        bid_dir / STATUS_FILENAME,
        {
            "bid_documents_hash": bid_documents_hash,
            "requirements": requirements_status,
        },
    )


def save_evaluation(bid_dir, evaluation):
    atomic_write_json(bid_dir / OUTPUT_FILENAME, evaluation)


def build_evaluation(requirements, requirements_status):
    """evaluation.json content: the result of every requirement that is
    currently completed, in the tender's requirement order."""
    evaluation = []

    for requirement in requirements:
        entry = requirements_status.get(str(requirement["id"]))
        if entry and entry.get("status") == "completed" and entry.get("result"):
            evaluation.append(entry["result"])

    return evaluation


def process_bid(tender_dir, bid_id):
    tender_dir = Path(tender_dir)
    bid_dir = tender_dir / "bids" / bid_id

    if not bid_dir.exists():
        raise FileNotFoundError(
            f"Bid directory does not exist: {bid_dir}"
        )

    requirements = load_requirements(tender_dir)
    bid_documents_hash = compute_bid_documents_hash(bid_dir)

    existing_status = load_status(bid_dir)
    existing_requirements = existing_status.get("requirements", {})

    # Seed this run's status for every requirement: reuse a cached result
    # only if the requirement itself, the bidder documents, and the
    # previous completion are all still valid (same requirement hash +
    # same bid-documents hash + status "completed"). A requirement whose
    # last run was interrupted mid-way ("processing") is treated as
    # unfinished and re-run, which is what makes this resumable after a
    # crash.
    requirements_status = {}

    for requirement in requirements:
        req_id = str(requirement["id"])
        requirement_hash = compute_requirement_hash(requirement)
        input_hash = compute_input_hash(requirement_hash, bid_documents_hash)

        cached = existing_requirements.get(req_id)
        cache_is_valid = (
            cached is not None
            and cached.get("status") == "completed"
            and cached.get("input_hash") == input_hash
            and cached.get("result") is not None
        )

        if cache_is_valid:
            requirements_status[req_id] = cached
        else:
            requirements_status[req_id] = {
                "status": "pending",
                "requirement_hash": requirement_hash,
                "input_hash": input_hash,
            }

    save_status(bid_dir, bid_documents_hash, requirements_status)
    save_evaluation(bid_dir, build_evaluation(requirements, requirements_status))

    llm = None
    pages = None

    for index, requirement in enumerate(requirements, start=1):
        req_id = str(requirement["id"])
        entry = requirements_status[req_id]

        if entry["status"] == "completed":
            # Valid cached result (see cache_is_valid above) - the LLM is
            # not called for this requirement.
            continue

        print(
            f"Compliance requirement "
            f"{index}/{len(requirements)}"
        )

        entry["status"] = "processing"
        save_status(bid_dir, bid_documents_hash, requirements_status)

        if llm is None:
            # Loaded/lazily fetched at most once per process_bid call; if
            # every requirement was cache-valid this never runs.
            llm = get_llm()
            pages = load_pages(bid_dir)

            if not pages:
                raise ValueError(
                    f"No processed bid documents found in: {bid_dir}"
                )

        reset_context()

        try:
            result = evaluate_requirement(llm, requirement, pages)
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            save_status(bid_dir, bid_documents_hash, requirements_status)
            raise

        entry["status"] = "completed"
        entry["result"] = result
        entry.pop("error", None)

        save_status(bid_dir, bid_documents_hash, requirements_status)
        save_evaluation(
            bid_dir,
            build_evaluation(requirements, requirements_status),
        )

    return build_evaluation(requirements, requirements_status)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(
            "Usage: python compliance_checker.py "
            "<tender_id> <bid_id>"
        )
        raise SystemExit(1)

    tender_id = sys.argv[1]
    bid_id = sys.argv[2]

    tender_dir = (
        ROOT_DIR
        / "data"
        / "TENDERS"
        / tender_id
    )

    result = process_bid(
        tender_dir,
        bid_id,
    )

    print(
        json.dumps(
            result,
            indent=4,
            ensure_ascii=False,
        )
    )
