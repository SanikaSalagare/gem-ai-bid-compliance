from pathlib import Path
import json
import re

from scripts.model_manager import get_llm, reset_context

ROOT_DIR = Path(__file__).resolve().parent.parent

OUTPUT_FILENAME = "requirement.json"

MAX_CHUNK_CHARS = 12000



def load_pages(tender_dir):
    processed_dir = tender_dir / "tender_documents" / "processed"

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
            page_number = int(match.group(1))
            start = match.end()
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(content)
            )

            page_text = content[start:end].strip()

            if page_text:
                pages.append(
                    {
                        "file": text_file.stem + ".pdf",
                        "page": page_number,
                        "text": page_text,
                    }
                )

    return pages


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
You are extracting buyer requirements from a government procurement tender.

Read ONLY the supplied tender pages.

Return ONLY a valid JSON object with exactly one key, "requirements",
whose value is a JSON array.

Each item in the array must have exactly:
{{
    "requirement": "",
    "page": 0,
    "file": ""
}}

Each "requirement" must be ONE atomic, single-condition requirement -
not a paragraph, not a bullet block, and not several conditions joined
together with semicolons/commas/newlines.

If the tender lists a labeled specification table or a bullet list
(e.g. "Processor: ...; Memory: ...; Storage: ...; Warranty: ..."),
split it into one requirement item PER LABEL/LINE. Do not merge them
into a single combined string. Keep each label with its own value, and
drop the trailing separator (";", ",") when it is not part of the
value itself.

Example of the required shape (note: several requirement objects,
one per line item, all citing the same page/file they came from):
{{
    "requirements": [
        {{"requirement": "Processor: Intel Core i5 / AMD Ryzen 5 equivalent or better", "page": 4, "file": "example.pdf"}},
        {{"requirement": "Memory: Minimum 16 GB RAM", "page": 4, "file": "example.pdf"}},
        {{"requirement": "Storage: Minimum 512 GB SSD", "page": 4, "file": "example.pdf"}},
        {{"requirement": "Warranty: Minimum 3 years onsite warranty", "page": 4, "file": "example.pdf"}}
    ]
}}

Rules:
- Extract only explicit buyer requirements.
- Do not invent or infer requirements.
- Split any compound/list-style requirement into separate atomic items,
  one condition per item, even if the source text presents them as one
  run-on sentence or a semicolon/comma-separated list.
- Never join two or more distinct conditions into a single
  "requirement" string with ";", " and ", or similar.
- Preserve numbers, quantities, limits, standards and conditions
  exactly as written for each individual item.
- Keep each item's own label/prefix (e.g. "Processor:", "Memory:") if
  the source uses labels, so the item stays understandable on its own.
- Include the source PDF filename and exact page number for every item.
- If there are no requirements in this chunk, return {{"requirements": []}}.
- Do not create IDs. IDs will be assigned after consolidation.
- Return JSON only.

TENDER PAGES:

{text}
"""


def extract_chunk_requirements(llm, chunk):
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
        # Reserve explicit room for the JSON response instead of letting
        # it compete with the input for whatever is left of n_ctx. Without
        # this, a large chunk could leave the model almost no budget to
        # write anything back, and grammar-constrained JSON decoding would
        # just close out with an empty "[]"/"{}" rather than erroring.
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

    if not pages:
        raise ValueError(
            f"No processed tender pages found in: {tender_dir}"
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
