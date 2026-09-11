from pathlib import Path
import json
import re

from scripts.model_manager import LLM_LOCK, get_llm, reset_context
from scripts import text_extractor

ROOT_DIR = Path(__file__).resolve().parent.parent

OUTPUT_FILENAME = "requirement.json"

MAX_CHUNK_CHARS = 12000

# The full shape of one requirement.json entry. Shared with web/views.py
# (requirement_detail) so the view's "any extra/unexpected fields" check
# stays in sync with this module instead of hardcoding its own copy.
REQUIREMENT_FIELDS = {"id", "requirement", "file", "page"}


ELIGIBILITY_SOURCE_LABEL = "Eligibility (tender form)"


def load_pages(tender_dir):
    return text_extractor.read_processed_pages(
        tender_dir / "tender_documents" / "processed"
    )


def load_eligibility_page(tender_dir):
    """Turn the buyer's free-text "Eligibility information" tender-form
    field into a page-shaped dict so it flows through the same
    chunking/prompt/extraction pipeline as PDF pages, letting the AI
    pull eligibility requirements out of it just like any other page.
    """
    metadata_path = tender_dir / "tender.json"
    if not metadata_path.exists():
        return None

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    eligibility = str(metadata.get("eligibility", "")).strip()
    if not eligibility:
        return None

    return {"file": ELIGIBILITY_SOURCE_LABEL, "page": 1, "text": eligibility}


def build_chunks(pages, max_chars=MAX_CHUNK_CHARS):
    chunks = []
    current = []
    current_size = 0

    for page in pages:
        block = (
            f'FILE: {page["file"]}\n'
            f'PAGE: {page["page"]}\n'
            f'{page["text"]}'
        )

        if current and current_size + len(block) > max_chars:
            chunks.append(current)
            current = []
            current_size = 0

        if len(block) > max_chars:
            chunks.append([page])
            continue

        current.append(page)
        current_size += len(block)

    if current:
        chunks.append(current)

    return chunks


def build_prompt(chunk):
    text = "\n\n".join(
        f'FILE: {page["file"]}\n'
        f'PAGE: {page["page"]}\n'
        f'{page["text"]}'
        for page in chunk
    )

    return f"""
You are a procurement analyst extracting buyer requirements from an
Indian Government e-Marketplace (GeM) tender document, so each
requirement can later be checked one-by-one against a seller's bid.

WHAT COUNTS AS A REQUIREMENT
A condition the tender imposes on the seller that their bid must
satisfy: a technical specification, an eligibility criterion, a
certification/standard, a commercial/delivery term, or a
quantity/turnaround limit.

WHAT DOES NOT COUNT
Section headings, page numbers, table-of-contents entries, generic
boilerplate ("terms and conditions apply"), or narrative text that
only describes the buyer's context (e.g. department background) and
asks nothing of the seller. Leave these out entirely.

SOURCE DISCIPLINE
Read ONLY the tender pages supplied below. Do not use outside
knowledge of typical GeM tenders, this product category, or common
industry standards to fill gaps or add requirements the text doesn't
state.

SPLITTING RULE
Each "requirement" string must state exactly ONE atomic condition -
never a paragraph, a whole bullet block, or several conditions joined
by semicolons/commas/"and". Test: if a sentence contains two things a
seller could separately pass or fail, split it into two items. If the
tender presents a labeled specification table or list (e.g.
"Processor: ...; Memory: ...; Storage: ...; Warranty: ..."), emit one
item per label/line - never merge them - and keep each label with its
own value so the item reads standalone. Drop a trailing separator
(";", ",") when it isn't part of the value itself.

FIDELITY RULE
Preserve numbers, units, standards, and wording exactly as written for
each item - no rounding, unit conversion, or paraphrasing of specifics.

CITATION RULE
Every item must carry the exact source PDF filename and page number,
copied verbatim from that page's FILE/PAGE marker - never guessed or
borrowed from a neighboring page.

OUTPUT FORMAT
Return ONLY a valid JSON object - no markdown fences, no commentary -
with exactly one key, "requirements", holding a JSON array. Each array
item has exactly these three keys and no others:
{{
    "requirement": "",
    "page": 0,
    "file": ""
}}
Do not assign an "id" - IDs are assigned later, after all chunks are
consolidated and deduplicated.

EXAMPLE (one requirement object per line item, all citing the page/file they came from)
{{
    "requirements": [
        {{"requirement": "Processor: Intel Core i5 / AMD Ryzen 5 equivalent or better", "page": 4, "file": "example.pdf"}},
        {{"requirement": "Memory: Minimum 16 GB RAM", "page": 4, "file": "example.pdf"}},
        {{"requirement": "Storage: Minimum 512 GB SSD", "page": 4, "file": "example.pdf"}},
        {{"requirement": "Warranty: Minimum 3 years onsite warranty", "page": 4, "file": "example.pdf"}}
    ]
}}

If this chunk contains no requirements, return {{"requirements": []}}.

TENDER PAGES:

{text}
"""


def extract_chunk_requirements(llm, chunk):
    # Held for the whole call: a llama_cpp Llama instance can't safely
    # serve two concurrent create_chat_completion() calls, and this LLM
    # is shared with scripts/compliance_checker.py's background worker.
    with LLM_LOCK:
        response = llm.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract explicit procurement requirements "
                        "from page-aware tender text."
                    ),
                },
                {
                    "role": "user",
                    "content": build_prompt(chunk),
                },
            ],
            temperature=0.1,
            # Reserve explicit room for the JSON response instead of
            # letting it compete with the input for whatever is left of
            # n_ctx. Without this, a large chunk could leave the model
            # almost no budget to write anything back, and
            # grammar-constrained JSON decoding would just close out with
            # an empty "[]"/"{}" rather than erroring.
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

    content = response["choices"][0]["message"]["content"]

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        print(
            "WARNING: could not parse requirement JSON for this chunk "
            f"({exc}). Raw model output was:\n{content!r}"
        )
        return []

    if isinstance(data, list):
        if not data:
            print(
                "WARNING: model returned an empty requirement list for "
                "a non-empty chunk. If this keeps happening, the chunk "
                "may still be too large for the model's context window."
            )
        return data

    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                return value

    return []


def normalize_requirement(item):
    if not isinstance(item, dict):
        return None

    requirement = str(
        item.get("requirement", "")
    ).strip()

    file_name = str(
        item.get("file", "")
    ).strip()

    try:
        page = int(item.get("page"))
    except (TypeError, ValueError):
        page = None

    if not requirement or not file_name or page is None:
        return None

    return {
        "requirement": requirement,
        "file": file_name,
        "page": page,
    }


def deduplicate(requirements):
    result = []
    seen = set()

    for item in requirements:
        item = normalize_requirement(item)

        if item is None:
            continue

        key = (
            re.sub(r"\s+", " ", item["requirement"].lower()),
            item["file"].lower(),
            item["page"],
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    for index, item in enumerate(result, start=1):
        item["id"] = index

    return result


def detect_requirements(tender_dir):
    pages = load_pages(tender_dir)

    eligibility_page = load_eligibility_page(tender_dir)
    if eligibility_page is not None:
        # Put it first so it lands in its own/earliest chunk rather than
        # being silently appended after however many PDF pages exist.
        pages = [eligibility_page] + pages

    if not pages:
        raise ValueError(
            f"No processed tender pages or eligibility text found in: {tender_dir}"
        )

    chunks = build_chunks(pages)
    llm = get_llm()
    reset_context()

    extracted = []

    for index, chunk in enumerate(chunks, start=1):
        print(f"Requirement chunk {index}/{len(chunks)}")
        reset_context()
        extracted.extend(extract_chunk_requirements(llm, chunk))

    requirements = deduplicate(extracted)

    output_path = tender_dir / OUTPUT_FILENAME
    output_path.write_text(
        json.dumps(requirements, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    return requirements


def process_tender(tender_dir):
    tender_dir = Path(tender_dir)

    if not tender_dir.exists():
        raise FileNotFoundError(
            f"Tender directory does not exist: {tender_dir}"
        )

    return detect_requirements(tender_dir)


def process_all_tenders():
    tenders_dir = ROOT_DIR / "data" / "TENDERS"

    if not tenders_dir.exists():
        return

    for tender_dir in sorted(tenders_dir.iterdir()):
        if tender_dir.is_dir():
            try:
                print(f"Processing tender: {tender_dir.name}")
                process_tender(tender_dir)
                print(
                    f"Requirements saved: "
                    f"{tender_dir / OUTPUT_FILENAME}"
                )
            except Exception as exc:
                print(
                    f"Failed to process "
                    f"{tender_dir.name}: {exc}"
                )


if __name__ == "__main__":
    process_all_tenders()
