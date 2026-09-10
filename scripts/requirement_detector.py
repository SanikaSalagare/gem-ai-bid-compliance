from pathlib import Path
import json
import re

from llama_cpp import Llama

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT_DIR / "AI" / "models" / "Qwen3-8B-Q4_K_M.gguf"

OUTPUT_FILENAME = "requirement.json"

MAX_CHUNK_CHARS = 12000

_llm = None


def get_llm():
    global _llm

    if _llm is None:
        _llm = Llama(
            model_path=str(MODEL_PATH),
            n_gpu_layers=-1,
            n_ctx=4096,
            verbose=False,
        )

    return _llm


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

Return ONLY a valid JSON array.

Each item must have exactly:
{{
    "requirement": "",
    "page": 0,
    "file": ""
}}

Rules:
- Extract only explicit buyer requirements.
- Do not invent or infer requirements.
- Preserve numbers, quantities, limits, standards and conditions.
- Include the source PDF filename and exact page number.
- If there are no requirements in this chunk, return [].
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
        response_format={"type": "json_object"},
    )

    content = response["choices"][0]["message"]["content"]
    data = json.loads(content)

    if isinstance(data, list):
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

    extracted = []

    for index, chunk in enumerate(chunks, start=1):
        print(
            f"Requirement chunk {index}/{len(chunks)}"
        )
        extracted.extend(
            extract_chunk_requirements(llm, chunk)
        )

    requirements = deduplicate(extracted)

    output_path = tender_dir / OUTPUT_FILENAME
    output_path.write_text(
        json.dumps(
            requirements,
            indent=4,
            ensure_ascii=False,
        ),
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
