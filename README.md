# GEMC — GeM Compliance Verification Platform

**GEMC** is an AI-powered procurement compliance verification platform designed around the **Government e-Marketplace (GeM)** tender workflow.

The system processes tender documents, extracts structured requirements, processes seller bid documents, and generates a compliance report showing how well each bid satisfies the tender requirements.

---

## Overview

A typical procurement workflow can be represented as:

```text
Buyer
  │
  ▼
Tender
  │
  ├── Tender Documents
  │
  └── Tender Requirements
          │
          ▼
       Seller
          │
          ▼
         Bid
          │
          ├── Bid Documents
          │
          ▼
   Compliance Analysis
          │
          ▼
   Compliance Report
```

GEMC automates the document-heavy parts of this process.

---

## Core Workflow

```text
Tender Documents
       │
       ▼
PDF Text Extraction / OCR
       │
       ▼
Processed Text
       │
       ▼
Requirement Extraction
       │
       ▼
tender_requirements.json
       │
       │
       ▼
Seller Bid Documents
       │
       ▼
PDF Text Extraction / OCR
       │
       ▼
Processed Seller Text
       │
       ▼
Compliance Analysis
       │
       ▼
compliance.json
```

---

## What GEMC Does

### 1. Tender Document Processing

Buyer-side documents are stored under a tender.

The document processing pipeline supports:

* Digitally generated PDFs
* Scanned PDFs
* OCR-based extraction
* Conversion of documents into machine-readable text
* Hash-based detection of unchanged files

The extracted text is stored separately from the original documents.

---

### 2. Tender Requirement Extraction

After the tender documents are processed, the system analyzes the extracted text and creates:

```text
tender_requirements.json
```

This JSON contains the structured representation of the requirements found across the tender documents.

It acts as the **source of truth for compliance checking**.

---

### 3. Seller Bid Processing

A seller submits a bid against a tender.

Each bid can contain multiple documents such as:

* Company information
* Technical documents
* Certificates
* Eligibility documents
* Financial documents
* Other supporting documents

These documents go through the same PDF → text extraction pipeline.

---

### 4. Compliance Analysis

The seller's processed documents are compared against:

```text
tender_requirements.json
```

The compliance builder then produces a seller-specific:

```text
compliance.json
```

This represents whether and how the seller satisfies the tender requirements.

---

## Tender vs Bid

An important distinction in GEMC is:

```text
Tender
│
├── Tender Documents
├── Tender Requirements
│
└── Bids
    ├── Bid 1
    ├── Bid 2
    └── Bid 3
```

A **Tender** is created by the buyer.

A **Bid** is submitted by a seller in response to that tender.

Therefore, bids belong inside a tender rather than being treated as separate tenders.

---

## Project Structure

```text
gemc/
│
├── manage.py
│
├── scripts/
│   ├── ...
│   └── templates/
│       └── ...
│
├── AI/
│   ├── __init__.py
│   ├── models/
│   │   └── ...
│   └── ...
│
└── data/
    └── tenders/
        └── ...
```

### `manage.py`

Main project entry point used to run the application.

### `scripts/`

Contains the main application and backend code, including processing logic and HTML templates/blueprints.

### `AI/`

Contains AI-related components and model management scripts.

The `models/` directory contains downloaded model files and is excluded from Git.

### `data/`

Contains tender, bid, original document, processed document, and generated JSON data.

---

## Data Structure

The intended data hierarchy is:

```text
data/
└── tenders/
    └── tender_{id}/
        │
        ├── tender_requirements.json
        │
        ├── tender_documents/
        │   ├── original/
        │   └── processed/
        │
        └── bids/
            │
            ├── bid_{id}/
            │   ├── documents/
            │   │   ├── original/
            │   │   └── processed/
            │   │
            │   └── compliance.json
            │
            └── bid_{id}/
                └── ...
```

### Original vs Processed

```text
original/
    ↓
Uploaded source documents

processed/
    ↓
Extracted machine-readable text
```

Original files are preserved while processed files are generated for AI analysis.

---

## Document Processing

The PDF processing module is responsible only for **document-to-text extraction**.

```text
PDF
 │
 ├── Digital PDF ──────► Direct Text Extraction
 │
 └── Scanned PDF ──────► OCR
                              │
                              ▼
                         Extracted Text
```

The extraction layer does **not** determine compliance or requirements.

This separation keeps document processing independent from the AI reasoning layer.

---

## AI Pipeline

GEMC uses locally hosted AI models for document understanding.

The main AI stages are:

```text
Processed Tender Documents
          │
          ▼
Requirement Extraction
          │
          ▼
tender_requirements.json
```

and:

```text
tender_requirements.json
          │
          +
Processed Seller Documents
          │
          ▼
Compliance Analysis
          │
          ▼
compliance.json
```

Models are stored locally under:

```text
AI/models/
```

This directory is intentionally excluded from version control.

---

## Hash-Based Document Processing

GEMC avoids unnecessarily processing the same document multiple times.

The basic idea is:

```text
Document
   │
   ▼
Check existence
   │
   ▼
Check stored hash
   │
   ├── Same file ──────► Reuse processed text
   │
   └── Changed/New ────► Process document
                              │
                              ▼
                         Store new hash
```

This reduces unnecessary PDF extraction and OCR operations, especially when a tender contains many documents.

---

## Main Components

| Component                  | Responsibility                                           |
| -------------------------- | -------------------------------------------------------- |
| `manage.py`                | Main application entry point                             |
| Document extraction module | PDF → text / OCR                                         |
| Requirement extraction     | Tender documents → requirements JSON                     |
| Compliance builder         | Tender requirements + seller documents → compliance JSON |
| `AI/`                      | Local AI models and model management                     |
| `data/`                    | Tender, bid, document and generated data storage         |

---

## Installation

Clone the repository and install the required Python packages:

```bash
pip install -r requirements.txt
```

---

## AI Model Setup

AI models are stored locally and are not included in the Git repository.

The supported model download scripts are located inside:

```text
AI/
```

Downloaded models are placed under:

```text
AI/models/
```

---

## Running GEMC

Run the main application using:

```bash
python manage.py
```

Development/testing scripts may also be provided inside the project for running specific processing pipelines.

---

## Example

A tender might contain:

```text
tender_123/
├── tender_requirements.json
├── tender_documents/
│   ├── original/
│   │   ├── technical.pdf
│   │   ├── eligibility.pdf
│   │   └── terms.pdf
│   │
│   └── processed/
│       ├── technical.txt
│       ├── eligibility.txt
│       └── terms.txt
│
└── bids/
    └── bid_001/
        ├── documents/
        │   ├── original/
        │   │   ├── company.pdf
        │   │   └── certificate.pdf
        │   │
        │   └── processed/
        │       ├── company.txt
        │       └── certificate.txt
        │
        └── compliance.json
```

The resulting flow is:

```text
Tender PDFs
    ↓
Text Extraction
    ↓
Tender Requirements
    ↓
Seller Bid PDFs
    ↓
Text Extraction
    ↓
Compliance Analysis
    ↓
Compliance JSON
```

---

## Current Implementation

GEMC currently focuses on the core document-processing and compliance pipeline:

* PDF text extraction
* OCR for scanned PDFs
* Processed text generation
* Tender document organization
* Seller bid document organization
* Local AI model integration
* Tender requirement generation
* Seller compliance generation
* Hash-based document processing

The project is being developed toward a complete integrated tender and bid compliance verification system.
