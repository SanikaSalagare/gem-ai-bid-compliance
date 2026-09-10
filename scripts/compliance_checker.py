from pathlib import Path
import gc
import hashlib
import json
import re

from scripts.model_manager import get_llm, reset_context
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
    processed_dir = bid_dir / "documents" / "processed"

    if not processed_dir.exists():
        return []

    pages = []

    for text_file in sorted(processed_dir.glob("*.txt")):
        content = text_file.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        matches = list(
            re.finditer(
                r"<\[PAGE\s+(\d+)\]>",
                content,
            )
        )

        for index, match in enumerate(matches):
            start = match.end()
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(content)
            )

            text = content[start:end].strip()

            if text:
                pages.append(
                    {
                        "file": text_file.stem + ".pdf",
                        "page": int(match.group(1)),
                        "text": text,
                    }
                )

    return pages


def find_relevant_pages(requirement, pages, limit=4):
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

    return [
        page
        for _, page in scored[:limit]
    ]


def build_prompt(requirement, pages):
    evidence_text = "\n\n".join(
        f'FILE: {page["file"]}\n'
        f'PAGE: {page["page"]}\n'
        f'{page["text"]}'
        for page in pages
    )

    return f"""
You are verifying one procurement requirement against seller documents.

Requirement:
{json.dumps(requirement, ensure_ascii=False)}

Seller document evidence:
{evidence_text}

Return ONLY valid JSON with exactly:
{{
    "score": 0,
    "file": null,
    "page": null,
    "evidence": null
}}

Scoring instructions:
- Score must be an integer from 0 to 100.
- Evaluate the requirement against the supplied seller evidence, not against assumptions.
- 100: the seller evidence clearly and completely satisfies the requirement.
- 90-99: essentially complete compliance with only a very minor gap.
- 75-89: strong compliance but one meaningful detail is incomplete or uncertain.
- 60-74: substantial but incomplete/partial compliance.
- 40-59: mixed evidence; important parts are missing or unclear.
- 20-39: weak evidence or substantial failure to meet the requirement.
- 1-19: evidence exists but provides almost no compliance.
- 0: no supporting evidence is present OR the evidence clearly contradicts/fails the requirement.
- Do NOT treat compliance as a binary decision. Intermediate scores are expected whenever the evidence is incomplete, partially satisfies the requirement, or leaves reasonable uncertainty.
- Do not default to 0 or 100 merely because the requirement is difficult.
- Never invent evidence, specifications, certifications, pages, or facts.
- If evidence supports the requirement, return the exact source file and page containing the strongest evidence.
- If no useful evidence exists, use null for file, page and evidence.
- Keep evidence concise and grounded in the supplied text.
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

    output = {
        "id": requirement["id"],
        "score": score,
        "file": file_name,
        "page": page,
        "evidence": evidence,
    }

    # Drop per-call temporaries (evidence pages, the built prompt, and the
    # raw LLM response object) now that we've extracted what we need. The
    # LLM instance itself is left loaded/untouched.
    del relevant, prompt, response, result
    gc.collect()

    return output


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
        finally:
            gc.collect()

        entry["status"] = "completed"
        entry["result"] = result
        entry.pop("error", None)

        save_status(bid_dir, bid_documents_hash, requirements_status)
        save_evaluation(
            bid_dir,
            build_evaluation(requirements, requirements_status),
        )

        # This requirement's result now lives in requirements_status /
        # evaluation.json; drop the local reference before moving on.
        del result
        gc.collect()

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
