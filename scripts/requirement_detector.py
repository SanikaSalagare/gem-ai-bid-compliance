from pathlib import Path
import json
import re
from llama_cpp import Llama

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT_DIR / "AI" / "models" / "Qwen3-8B-Q4_K_M.gguf"

OUTPUT_FILENAME = "req.json"

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


def load_processed_text(tender_dir):
    documents_dir = tender_dir / "tender_documents" / "processed_documents"

    if not documents_dir.exists():
        return ""

    texts = []

    for text_file in sorted(documents_dir.glob("*.txt")):
        content = text_file.read_text(encoding="utf-8", errors="ignore")

        match = re.search(
            r"^PROCESSED:.*?$",
            content,
            re.MULTILINE,
        )

        if match:
            content = content[match.end():]

        texts.append(content.strip())

    return "\n\n".join(texts)


def build_prompt(text):
    return f"""
You are an AI system for analyzing government procurement tender documents.

Read the tender document text below and extract the buyer's requirements.

Return ONLY valid JSON.

Required JSON structure:

{{
    "buyer": "",
    "scope_of_work": "",
    "requirements": [
        {{
            "requirement_id": "",
            "category": "",
            "description": "",
            "mandatory": true
        }}
    ],
    "certifications": [],
    "financial_requirements": [],
    "technical_requirements": [],
    "key_dates": [],
    "other_information": []
}}

Rules:
- Extract requirements explicitly stated in the documents.
- Do not invent requirements.
- Preserve important numbers, quantities, thresholds, standards and conditions.
- Set "mandatory" to true only when the requirement is explicitly mandatory or clearly required.
- Use meaningful requirement IDs such as REQ-001, REQ-002, etc.
- Keep each requirement specific enough to be checked against a seller's documents.
- Return JSON only.

TENDER DOCUMENT TEXT:

{text}
"""


def detect_requirements(tender_dir):
    text = load_processed_text(tender_dir)

    if not text.strip():
        raise ValueError(
            f"No processed tender documents found in: {tender_dir}"
        )

    llm = get_llm()

    response = llm.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": "You extract structured procurement requirements from tender documents.",
            },
            {
                "role": "user",
                "content": build_prompt(text),
            },
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    content = response["choices"][0]["message"]["content"]

    requirements = json.loads(content)

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
                print(f"Requirements saved: {tender_dir / OUTPUT_FILENAME}")
            except Exception as exc:
                print(f"Failed to process {tender_dir.name}: {exc}")


if __name__ == "__main__":
    process_all_tenders()