from pathlib import Path
import json
import re

from scripts.model_manager import get_llm, reset_context

ROOT_DIR = Path(__file__).resolve().parent.parent

OUTPUT_FILENAME = "evaluation.json"



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
                "content": build_prompt(
                    requirement,
                    relevant,
                ),
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


def process_bid(tender_dir, bid_id):
    tender_dir = Path(tender_dir)
    bid_dir = tender_dir / "bids" / bid_id

    if not bid_dir.exists():
        raise FileNotFoundError(
            f"Bid directory does not exist: {bid_dir}"
        )

    requirements = load_requirements(tender_dir)
    pages = load_pages(bid_dir)

    if not pages:
        raise ValueError(
            f"No processed bid documents found in: {bid_dir}"
        )

    llm = get_llm()
    reset_context()
    evaluation = []

    for index, requirement in enumerate(
        requirements,
        start=1,
    ):
        print(
            f"Compliance requirement "
            f"{index}/{len(requirements)}"
        )

        reset_context()
        evaluation.append(
            evaluate_requirement(
                llm,
                requirement,
                pages,
            )
        )

    output_path = bid_dir / OUTPUT_FILENAME
    output_path.write_text(
        json.dumps(
            evaluation,
            indent=4,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return evaluation


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
