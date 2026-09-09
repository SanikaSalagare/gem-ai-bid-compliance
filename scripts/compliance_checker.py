from pathlib import Path
import json
import re
from llama_cpp import Llama

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT_DIR / "AI" / "models" / "Qwen3-8B-Q4_K_M.gguf"

OUTPUT_FILENAME = "compliance.json"

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


def load_processed_text(folder):
    documents_dir = folder / "processed_documents"

    if not documents_dir.exists():
        return ""

    texts = []

    for text_file in sorted(documents_dir.glob("*.txt")):
        content = text_file.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        match = re.search(
            r"^PROCESSED:.*?$",
            content,
            re.MULTILINE,
        )

        if match:
            content = content[match.end():]

        texts.append(content.strip())

    return "\n\n".join(texts)


def load_requirements(tender_dir):
    requirements_path = tender_dir / "req.json"

    if not requirements_path.exists():
        raise FileNotFoundError(
            f"Requirement file not found: {requirements_path}"
        )

    return json.loads(
        requirements_path.read_text(
            encoding="utf-8"
        )
    )


def extract_seller_information(seller_text):
    llm = get_llm()

    prompt = f"""
You are analyzing a seller's bid documents for a government procurement tender.

Extract only information explicitly present in the seller documents.

Return ONLY valid JSON using this structure:

{{
    "company_name": "",
    "contact": "",
    "certifications_held": [],
    "financial_details": [],
    "technical_capabilities": [],
    "eligibility_claims": [],
    "other_information": []
}}

Do not invent information.

SELLER DOCUMENTS:

{seller_text}
"""

    response = llm.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": "You extract structured information from seller bid documents.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    return json.loads(
        response["choices"][0]["message"]["content"]
    )


def check_compliance(requirements, seller_information):
    llm = get_llm()

    prompt = f"""
You are a procurement bid compliance verification system.

Compare the tender requirements against the seller information.

Determine whether each requirement is:

COMPLIANT
- The seller provides sufficient evidence that the requirement is satisfied.

NON_COMPLIANT
- The seller provides evidence that the requirement is not satisfied.

INSUFFICIENT_EVIDENCE
- The available seller documents do not provide enough evidence to determine compliance.

Do not assume missing information is compliant.

Return ONLY valid JSON.

Required structure:

{{
    "overall_status": "",
    "summary": {{
        "total_requirements": 0,
        "compliant": 0,
        "non_compliant": 0,
        "insufficient_evidence": 0
    }},
    "requirements": [
        {{
            "requirement_id": "",
            "category": "",
            "description": "",
            "mandatory": true,
            "status": "",
            "evidence": "",
            "reason": ""
        }}
    ]
}}

Tender requirements:

{json.dumps(requirements, indent=2, ensure_ascii=False)}

Seller information:

{json.dumps(seller_information, indent=2, ensure_ascii=False)}

Rules:
- Evaluate every requirement.
- Preserve the original requirement ID.
- Do not invent seller evidence.
- Missing evidence must normally be marked INSUFFICIENT_EVIDENCE.
- If a mandatory requirement is NON_COMPLIANT, overall_status should be NON_COMPLIANT.
- If no mandatory requirement is violated but some requirements lack evidence, overall_status should be INSUFFICIENT_EVIDENCE.
- Use COMPLIANT only when the seller evidence clearly supports the requirement.
"""

    response = llm.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": "You verify seller compliance against procurement requirements.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    return json.loads(
        response["choices"][0]["message"]["content"]
    )


def process_bid(tender_dir, seller_id):
    tender_dir = Path(tender_dir)

    seller_dir = tender_dir / "bids" / seller_id

    if not seller_dir.exists():
        raise FileNotFoundError(
            f"Seller directory does not exist: {seller_dir}"
        )

    requirements = load_requirements(tender_dir)

    seller_text = load_processed_text(seller_dir)

    if not seller_text.strip():
        raise ValueError(
            f"No processed seller documents found in: {seller_dir}"
        )

    seller_information = extract_seller_information(
        seller_text
    )

    compliance = check_compliance(
        requirements,
        seller_information,
    )

    output = {
        "seller_id": seller_id,
        "seller_information": seller_information,
        "compliance": compliance,
    }

    output_path = seller_dir / OUTPUT_FILENAME

    output_path.write_text(
        json.dumps(
            output,
            indent=4,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return output


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(
            "Usage: python compliance_checker.py "
            "<tender_id> <seller_id>"
        )
        raise SystemExit(1)

    tender_id = sys.argv[1]
    seller_id = sys.argv[2]

    tender_dir = ROOT_DIR / "data" / "TENDERS" / tender_id

    result = process_bid(
        tender_dir,
        seller_id,
    )

    print(
        json.dumps(
            result,
            indent=4,
            ensure_ascii=False,
        )
    )