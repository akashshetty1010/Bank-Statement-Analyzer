-# Bank Statement Analyzer

## 1. Overview

Bank Statement Analyzer is a lightweight local Proof of Concept (PoC) for extracting transactions from bank statements, classifying transactions, identifying items that need manual verification, and generating an explainable credit-risk summary.

The project is deliberately small and local. It demonstrates a complete path from a bank statement file to structured transactions, transaction classification, review flags and an explainable credit-risk summary without introducing a separate frontend build system or production infrastructure.

## Visual Guide

[View the Bank Statement Analyzer Visual Guide](Bank_Statement_Analyzer_Visual_Guide.pdf)

---

## 2. Prerequisites

The project is intended to run on Windows.

Install or have available:

- Python 3.10+ (the code uses modern Python typing syntax).
- Tesseract OCR for scanned PDFs and images.
- Ollama with at least one local generative model. Ollama is used automatically when the deterministic classifier cannot confidently classify a transaction.
- A modern browser such as Chrome or Edge.

No Node.js, npm, React, Next.js or frontend build step is required.

---

## 3. Setup on Windows

### Step 1 — Extract the project

Extract the ZIP to a working folder, for example:

```powershell
<project-folder>
```

Open PowerShell in that folder.

### Step 2 — Create a virtual environment

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, use a process-scoped policy change for the current terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### Step 3 — Install Python dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

The important runtime packages are:

- FastAPI / Uvicorn — local API and web server.
- PyMuPDF — digital PDF reading and page rendering.
- Pillow — image handling.
- pytesseract — OCR bridge to Tesseract.
- reportlab — generated sample statements and HTML report support used by the PoC.
- pytest — automated tests.

### Step 4 — Confirm Tesseract

Run:

```powershell
tesseract --version
```

If the command is not found, install Tesseract and make sure its executable is available on PATH. The application also contains a configuration helper for the Windows installation location.

### Step 5 — Confirm Ollama

Start Ollama if it is not already running, then run:

```powershell
ollama list
```

There should be at least one local generative model.

The application communicates with Ollama through its local HTTP service, normally:

```text
http://127.0.0.1:11434
```

The application does **not** read the model files under `<project_folder> directly. Ollama owns those files and exposes the models through its API.

If you want to select a specific model, set this before starting the backend:

```powershell
$env:BSA_OLLAMA_MODEL="llama3.2:3b"
```

If the variable is not set, the application discovers an available local model from Ollama. Ollama fallback is enabled by default; there is no feature flag to turn it on.

### Step 6 — Start the application

From the project root:

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

### Step 7 — Run automated tests

Open another PowerShell window, activate the same virtual environment and run:

```powershell
python -m pytest -q
```

The tests cover the health endpoint, frontend serving, upload validation, default Ollama fallback invocation and multi-account extraction behavior.

---

## 4. Architecture overview

The project is organized as a small pipeline rather than a large service architecture.

```text
                    Browser
                       |
                       v
              FastAPI upload API
                       |
                       v
              File validation layer
                       |
                       v
            Statement extraction layer
             /                     \
        Digital PDF                OCR
             \                     /
              +--------+----------+
                       |
                       v
             Page/order validation
                       |
                       v
                3-page batches
                       |
                       v
             Transaction records
                       |
                       v
             Rule classification
                       |
              OTHER + <70% confidence
                       |
                       v
                Local Ollama
                       |
                       v
              Review flagging
                       |
                       v
               Risk calculation
                       |
                       v
       Results page + JSON/HTML export
```

### Main code areas

| File | Responsibility |
|---|---|
| `backend/app/main.py` | HTTP endpoints, upload validation, friendly errors, local error logging and static frontend serving |
| `backend/bsa_processor/processor.py` | PDF/image extraction, page metadata, page-order checks, 3-page batching and transaction extraction |
| `backend/bsa_processor/classifier.py` | Built-in taxonomy, deterministic classification, Ollama fallback, confidence/method tracking and manual overrides |
| `backend/bsa_processor/risk.py` | Six risk components, weighted score, rating and recommendation |
| `backend/bsa_processor/models.py` | Structured page, transaction, issue and document records |
| `backend/bsa_processor/analysis.py` | Connects extraction → classification → risk calculation |
| `backend/bsa_processor/report.py` | HTML report generation |
| `frontend/index.html` | Lightweight upload and results UI |
| `backend/tests/` | Automated tests |
| `samples/` | Test statements and ground truth fixtures |
| `data/error_history.jsonl` | Historical local record of failed/rejected uploads |
| `README.md` | Short project assumptions reference |

---

## 5. Processing flow in detail

### 7.1 Upload validation

The browser accepts PDF, PNG, JPG and JPEG. The backend repeats the validation so that the restriction does not depend on browser behavior.

The file must be strictly smaller than 10 MB. Unsupported types, empty files and oversized files receive a user-friendly message and are logged locally.

### 7.2 Digital PDF extraction

For a PDF page, PyMuPDF first extracts its text. If the page contains insufficient usable text, the page is rendered and sent through Tesseract OCR.

Digital pages use the established coordinate-aware transaction parser. The parser uses the visible transaction-table headers to locate the Debit, Credit and Balance columns instead of assuming one fixed horizontal layout. This corrects common column-order differences while retaining the lightweight, known-layout approach.

### 7.3 Scanned PDF/image extraction

When the page does not contain sufficient digital text, it is rendered as an image and OCR is applied. The OCR output is then passed through the same transaction parsing and classification stages.

### 7.4 Metadata and accounts

The extractor captures metadata such as account number, account type where stated, bank name, currency and statement period. Metadata may be inherited by subsequent pages when a statement omits repeated header information.

Transactions retain their account identity. The results UI groups transactions by account number and displays each account in its own table. The sample multi-account statement demonstrates three account types:

- Individual
- Joint
- Credit Card

The grouping is based on the account identifier found in each page's statement header; the implementation does not merge different accounts into one transaction table.

### 7.5 Page-order validation

Page numbers are read from the statement. If an explicit page sequence is detected and it is not continuous, the document is rejected before classification and risk scoring. This prevents a jumbled statement from producing a misleading balance-based analysis.

### 7.6 Three-page batching

Pages are grouped into batches of three. Each batch is written as a local Markdown artifact for traceability. This keeps the processing design suitable for longer statements without sending the entire document through one large model context.

### 7.7 Transaction extraction

Each transaction record can contain:

- transaction date
- value date
- narration
- debit
- credit
- running balance
- account number
- account type where available
- month
- counterparty
- payment channel
- currency
- special attributes from narration
- extraction confidence
- classification information
- source page/batch

The parser is conservative: if it cannot produce credible transaction rows, the application does not invent data and does not calculate a risk score.

---

## 6. Classification and Ollama behavior

### Rule-first approach

The built-in rules handle recognizable patterns such as salary, refunds, EMI, bank charges, utility payments, grocery, ATM activity, investment activity and common payment channels.

A user-provided taxonomy CSV can prepend additional rules so that custom keywords take precedence over the default taxonomy.

### Automatic Ollama fallback

The fallback is **enabled by default**.

It is triggered when the deterministic classifier returns `OTHER` with confidence below `0.70`.

The local model receives the transaction narration and amount direction and is asked for a constrained JSON result. The response is validated against the allowed categories before it can replace the rule result.

The implementation also caches:

- the discovered Ollama model for the lifetime of the backend process; and
- repeated classification results for identical narration/amount combinations.

This prevents a long statement with repeated ambiguous rows from repeatedly calling `/api/tags` or generating the same model response. It is an important performance optimization because local model inference is normally much slower than deterministic keyword classification.

If Ollama is unavailable or returns invalid output, the application keeps the deterministic result and can flag the transaction for review. It does not fail the entire document merely because the inference request could not be completed.

> The fallback is part of the intended classification flow. A successful LLM result still depends on Ollama being installed, running and having a usable local model.

### Review status

The user-facing table shows `Review` or `OK` rather than raw confidence values. Review is triggered when classification confidence or counterparty confidence falls below the configured 0.70 threshold. The Review popup exposes the relevant confidence and threshold so the reason for the flag is visible.

---

## 7. Risk scoring implementation

The risk engine produces a deterministic composite score on a **0–1000 scale** from six weighted components:

| Component | Weight | Implemented metrics/signals |
|---|---:|---|
| Income Stability | 25% | Monthly salary totals, salary-month regularity, income-source diversity and a first-vs-last observed salary growth signal |
| Debt Service | 20% | Identified EMI totals, FOIR using observed salary/EMI data and bounce/return indicators |
| Liquidity | 15% | Average EOD balance, minimum EOD balance and negative-balance days |
| Banking Behaviour | 10% | Bounce indicators, bank-charge amount and overdraft indicators |
| Fraud Indicators | 15% | Balance arithmetic mismatches, possible circular-transfer patterns and possible structuring patterns |
| Expense Management | 15% | Essential vs discretionary/other expense split and discretionary ratio |

### Risk component indicators

The results UI does not expose the individual numerical component scores. Instead, each component is presented as a qualitative indicator:

| Internal component score | Indicator |
|---:|---|
| 75–100 | **Strong** |
| 50–74 | **Moderate** |
| 0–49 | **Needs attention** |

These labels are derived directly from the underlying component score; they are not manually assigned.

For example:

```text
Income Stability = 82
→ Strong
```

```text
Liquidity = 63
→ Moderate
```

```text
Banking Behaviour = 38
→ Needs attention
```

Therefore, **Needs attention** means that the underlying component score is below 50 based on the signals implemented for that component. It does not mean that the customer or statement has been independently determined to be high-risk or problematic. It simply indicates that the available statement evidence produced a weaker result for that particular risk dimension.

### Income Stability — 25%

This component considers:

- Monthly identifiable income
- Regularity of income
- Income-source diversity
- Observed income growth trend

Regular salary-like credits contribute positively when they appear consistently across the statement period. Irregular or limited identifiable income can reduce the component score.

The indicator is therefore based on the calculated income-stability score:

```text
75–100 → Strong
50–74  → Moderate
0–49   → Needs attention
```

### Debt Service — 20%

This component focuses primarily on:

- Identified EMI transactions
- EMI burden relative to identified income
- Bounce/return indicators where detectable

Lower debt obligations relative to observed income generally produce a stronger component score, while higher recurring debt obligations or negative repayment signals reduce it.

The PoC does not attempt to infer every possible lending metric from a bank statement. A complete due-date-based on-time-payment model is outside the current scope.

### Liquidity — 15%

This component uses available balance information, including:

- Average end-of-day/running balance
- Minimum observed balance
- Negative-balance occurrences where detectable

Higher and more consistent balances support the component score. Very low balances or negative-balance evidence reduce it.

The resulting score is translated into the same three indicators:

```text
75–100 → Strong
50–74  → Moderate
0–49   → Needs attention
```

### Banking Behaviour — 10%

This component looks for negative banking-behaviour signals such as:

- Cheque/payment bounce indicators
- Bounce-related charges
- Bank charges
- Overdraft indicators where identifiable

Fewer negative events support the score. Repeated bounce, penalty or overdraft-related indicators can reduce the score and result in **Needs attention** when the calculated component score falls below 50.

### Fraud Indicators — 15%

The fraud component looks for detectable anomalies such as:

- Balance arithmetic inconsistencies
- Possible circular-transfer patterns
- Possible structuring patterns

The dashboard presents the result using the same qualitative indicator bands:

```text
75–100 → Strong
50–74  → Moderate
0–49   → Needs attention
```

A **Strong** fraud indicator means that the implemented checks did not identify significant anomalies in the available statement data. It does **not** mean that the statement has been proven to be fraud-free.

### Expense Management — 15%

This component evaluates the observed expense profile, including:

- Essential expenses
- Discretionary/other expenses
- Discretionary expense ratio

A more manageable expense profile supports the component score, while a high proportion of discretionary/other spending can reduce it.

Where sufficient statement history exists, the implementation can use the available monthly information, but a dedicated fixed-vs-variable expense score and explicit month-on-month trend score are not currently calculated separately.

### Metrics deliberately not calculated separately


- Debt Service: a separate payment-due-date/on-time-payment calculation is not implemented.
- Banking Behaviour: penalty fees are not separately isolated from general charges.
- Fraud Indicators: overnight transaction behavior is not scored when reliable transaction timestamps are unavailable.
- Expense Management: a separate fixed-vs-variable expense ratio and explicit month-on-month trend score are not currently calculated.

The implementation focuses on the highest-impact metrics and explicitly documents which requested sub-metrics are not calculated.

### Rating bands

The current score mapping is:

| Composite score | Rating |
|---:|---|
| 900–1000 | Excellent |
| 750–899 | Good |
| 600–749 | Fair |
| 450–599 | Below Average |
| 0–449 | Poor |

### Recommendation labels

The system uses these labels:

- `APPROVE`
- `APPROVE WITH CONDITIONS`
- `DECLINE`
- `REFER`

The current recommendation logic first checks for unresolved transaction review flags or fraud-pattern flags. If either exists, the system returns `REFER`. Otherwise:

- score ≥ 750 → `APPROVE`
- score ≥ 600 → `APPROVE WITH CONDITIONS`
- score < 600 → `DECLINE`

This is the current PoC rule implemented in `risk.py`; it is not a production credit policy.

## 8. Outputs and exports

### Results page

The results page shows:

- composite credit-risk score
- rating
- recommendation
- transaction count
- total credits
- total debits
- six risk component indicators
- account-separated transaction tables
- transaction category/method/status
- review explanations

The transaction table intentionally does not show raw confidence scores. Review explanations can show the confidence and threshold relevant to the flagged condition.

### Export data

The results page provides **Export data** with two choices:

1. **JSON** — structured extracted transactions plus classification information and the credit-risk summary.
2. **HTML** — human-readable report containing the extracted transactions and risk summary.

Both formats can be generated directly from an analyzed statement and contain the extracted transactions together with the risk summary.

---

## 9. Error handling and historical error log

All rejected/failed uploads are recorded in:

```text
data/error_history.jsonl
```

It is one historical file using JSON Lines format, with one record appended per error.

A record captures, where available:

- UTC timestamp
- processing start and failure timestamps
- processing duration
- document ID
- original filename
- extension
- file size
- uploaded content type
- processing stage
- user-facing error message
- exact technical error
- traceback for unexpected processing exceptions

The browser deliberately receives a normal-user message instead of a Python exception or stack trace. The local history retains the technical detail for debugging and for identifying recurring unsupported statement formats.

The file is included in the repository in empty form. Runtime error records should remain local and should not be committed to source control.

---

## 10. Test data included

The `samples/` folder contains:

- `indian_bank_digital.pdf` — digital PDF covering multiple months and accounts.
- `indian_bank_scanned.pdf` — rasterized/scanned version used to exercise OCR.
- `indian_bank_jumbled.pdf` — intentionally reordered pages used to verify page-order rejection.
- `multi_account_statement.pdf` — six-page synthetic statement containing Individual, Joint and Credit Card accounts in one upload.
- `Scb_sample.pdf` - scb bank real sample statement from the internet.
- `sample_statement_1_account.png` — sample one-account statement in PNG format
- corresponding ground-truth/reference JSON files.

The multi-account fixture uses one currency across the accounts so the demonstration focuses on account separation without introducing an FX conversion assumption into the composite risk score.

---

## 11. Key design decisions

### Lightweight local application

FastAPI and a single static HTML frontend were selected to keep installation and demonstration simple. There is no frontend compilation step.

### Conservative extraction rather than fabricated data

The application only calculates risk after credible transactions are extracted. An unreadable or unrelated file is rejected instead of receiving a random-looking score.

### Rules before LLM

Common classifications are faster and easier to audit when handled deterministically. Ollama is reserved for ambiguous `OTHER` cases.

### Local model service

Ollama keeps statement data inside the local environment for the PoC and avoids requiring an external API key.

### Cached LLM fallback

Model discovery and repeated identical classifications are cached to prevent unnecessary local inference and reduce processing time for statements containing repeated narrations.

### Account-aware transaction storage

Account identity is retained on every transaction so multiple accounts in one statement can be separated in the UI and downstream exports.

### Fail-fast page ordering

Balance-dependent calculations become unreliable when pages are reordered. The system therefore rejects a detected page-order violation before risk scoring.

### Explainable risk summary

The composite score is accompanied by six named components, weights, supporting metrics and flags. This makes the score easier to inspect than a single opaque number.

### Separate user-facing and technical errors

Normal users see clear guidance. The historical JSONL error file retains exact diagnostic information.

---

## 12. Known limitations

1. **Bank-format coverage:** The transaction extractor is not a universal parser for every bank's layout. It supports the digital/table structures represented by the PoC and uses OCR for scanned inputs. A substantially different bank format may require another parser strategy.
2. **OCR quality:** Scanned documents with poor resolution, skew, unusual fonts, compression artifacts or complex backgrounds can produce extraction errors.
3. **Mixed statement documents:** Statements may contain transaction tables, summaries, loan sections, interest calculations and informational pages. The parser is conservative about what constitutes a transaction and may not capture every non-standard transaction layout.
4. **Multiple account semantics:** Account separation is based on account metadata detected on statement pages. If a bank does not clearly identify account boundaries, the system cannot reliably infer them.
5. **Credit-card risk interpretation:** Credit-card accounts can be extracted and displayed separately, but the current risk engine is primarily designed around bank-account cash-flow behavior and does not implement a dedicated credit-card underwriting model.
6. **Cross-currency risk aggregation:** The PoC can retain transaction currency and display multi-currency amounts, but it does not perform FX conversion before combining transactions in the risk engine. A production implementation should establish an FX policy before aggregating multiple currencies into one score.
7. **Ollama model dependency:** Automatic fallback requires a running Ollama service and a suitable local generative model. If inference fails, the deterministic result remains available and the transaction can be sent to review.
8. **Model variability:** Different local models may classify ambiguous narrations differently. The response is constrained and validated, but semantic model differences remain possible.
9. **Risk-model scope:** The score is a PoC analytical model and is not a production underwriting policy, regulatory score or guarantee of repayment behavior.
10. **Unavailable metrics:** Some requested sub-metrics require timestamps, explicit due dates, richer account information or historical data that may not exist in a statement. Those metrics are not invented.

---

## 13. Implementation assumptions

The following assumptions are part of the current implementation:

- Supported uploads are PDF, PNG, JPG and JPEG and must be strictly below 10 MB.
- Digital PDF text is preferred; OCR is used when a page does not provide sufficient usable text.
- Pages are processed in three-page batches.
- Repeated metadata can be inherited from neighboring statement pages when a page omits it.
- Account number is the primary key used to separate transaction groups.
- Account type is recorded when explicitly stated by the statement.
- Unknown counterparties are not invented.
- The 0.70 threshold is used for manual-review confidence checks.
- Rules are attempted before the automatic Ollama fallback.
- The first available local Ollama model is used when no explicit model is configured.
- Risk calculations do not fabricate missing financial data.
- The rating/recommendation bands documented above are the current PoC implementation.
- Fraud checks identify heuristic patterns and are not definitive fraud determinations.

---

## 14. Validation checklist

### Environment

- [ ] Python environment created
- [ ] Dependencies installed
- [ ] Tesseract available
- [ ] Ollama running
- [ ] At least one suitable Ollama model available

### Core processing

- [ ] Digital PDF processes
- [ ] Scanned PDF processes
- [ ] Supported image input processes
- [ ] Unsupported file types are rejected
- [ ] Files at/above 10 MB are rejected
- [ ] Unrelated/empty files are rejected without a risk score
- [ ] Jumbled pages are rejected
- [ ] Three-page batches are created
- [ ] Multiple months are retained
- [ ] Multiple accounts remain separated
- [ ] Individual, Joint and Credit Card sample accounts are displayed separately

### Transactions

- [ ] Required transaction fields are populated where available
- [ ] Counterparty is shown
- [ ] Payment channel is shown
- [ ] Currency is retained
- [ ] Classification category and method are shown
- [ ] Review status can be opened
- [ ] Review popup explains the reason and threshold

### Classification

- [ ] Deterministic rules classify obvious transactions
- [ ] Ambiguous transaction can trigger Ollama automatically
- [ ] Successful fallback is recorded as `LLM`
- [ ] Manual override path remains available

### Risk

- [ ] Six risk components appear
- [ ] Weights are 25/20/15/10/15/15
- [ ] Composite score is 0–1000
- [ ] Rating uses the documented five bands
- [ ] Recommendation uses the documented four labels
- [ ] Supporting flags/explanations are visible

### Output and diagnostics

- [ ] JSON export works
- [ ] HTML export works
- [ ] Both contain transactions and risk summary
- [ ] `data/error_history.jsonl` is updated after a failed upload
- [ ] README.md is available with setup, processing, risk and assumption details

---
## 15. Future Improvements

   - Support more varied statement layouts, including pages with embedded images or random formatting.
   - Upgrade the UI with Next.js, Tailwind CSS for a more polished experience.
   - Refactor into a scalable FastAPI + PostgreSQL + Redis + Celery for a strong BE architecture.
   - Improve extraction from low-quality, rotated and complex scanned statements.
   - Expand classification and counterparty detection for unfamiliar narrations.
   - Add more risk metrics and configurable scoring rules.

---
## 16. Useful commands at a glance

Start backend:

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Run tests:

```powershell
python -m pytest -q
```

Check Ollama:

```powershell
ollama list
```

Check Tesseract:

```powershell
tesseract --version
```

Open the application:

```text
http://127.0.0.1:8000
```
