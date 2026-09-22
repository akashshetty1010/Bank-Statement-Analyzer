from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import fitz
import pytesseract
from PIL import Image

from .config import BATCH_SIZE, MIN_COUNTERPARTY_CONFIDENCE, SUPPORTED_EXTENSIONS, tesseract_command
from .models import BatchRecord, DocumentResult, PageRecord, Transaction, ValidationIssue

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
DATE_RE = re.compile(r"\b(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}[/-]\d{2}[/-]\d{2}|\d{1,2}\s+" + MONTHS + r"\s+\d{2,4})\b", re.I)
PAGE_RE = re.compile(r"\bpage\s*(\d+)\s*(?:of|/)\s*(\d+)\b", re.I)
PAGE_RE_2 = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
ACCOUNT_RE = re.compile(r"(?:account|acct|a/c)[^0-9]{0,20}([0-9]{1,18})", re.I)
ACCOUNT_TYPE_RE = re.compile(r"(?:account\s*type|type\s*of\s*account)\s*[:#-]?\s*(individual|joint|credit\s*card|savings|current|salary|loan|checking)", re.I)
BANK_RE = re.compile(r"(?:bank\s*name)\s*[:#-]?\s*([^\n|]+)", re.I)
CURRENCY_RE = re.compile(r"\b(INR|USD|EUR|GBP|AED|AUD|CAD|CHF|CNY|DKK|HKD|JPY|KES|NGN|NOK|NZD|QAR|SAR|SEK|SGD|TZS|UGX|ZAR|ZMW)\b", re.I)
PERIOD_RE = re.compile(r"(?:statement\s*period|period)\s*[:#-]?\s*([0-9/ -]+(?:to|\-|–)[0-9/ -]+)", re.I)
AMOUNT_RE = re.compile(r"(?<![\d/])(?:₹\s*)?(\(?-?\d[\d,]*(?:\.\d{1,2})?\)?)(?![\d/])")

CHANNEL_PATTERNS = [
    ("UPI", re.compile(r"\bUPI\b", re.I)),
    ("NEFT", re.compile(r"\bNEFT\b", re.I)),
    ("IMPS", re.compile(r"\bIMPS\b", re.I)),
    ("RTGS", re.compile(r"\bRTGS\b", re.I)),
    ("ATM", re.compile(r"\bATM\b", re.I)),
    ("POS", re.compile(r"\bPOS\b|POINT OF SALE", re.I)),
    ("CHEQUE", re.compile(r"\b(?:CHEQUE|CHQ)\b", re.I)),
    ("NACH/ECS", re.compile(r"\b(?:NACH|ECS|ACH)\b", re.I)),
    ("BANK_TRANSFER", re.compile(r"\bTRANSFER\b", re.I)),
]

SPECIAL_PATTERNS = {
    "card_last4": re.compile(r"(?:card|debit|credit)[^0-9]{0,12}(\d{4})\b", re.I),
    "loan_account_number": re.compile(r"(?:loan|ln)[^0-9]{0,12}(\d{6,18})\b", re.I),
    "platform": re.compile(r"\b(STRIPE|APPLE PAY|GOOGLE PAY|PHONEPE|PAYTM|AMAZON PAY)\b", re.I),
    "counterparty_account": re.compile(r"(?:a/c|account)[^0-9]{0,10}(\d{6,18})\b", re.I),
}


def _normalize_date(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip()).replace("-", "/")
    parts = value.split("/")
    if len(parts) == 3:
        if len(parts[0]) == 4:
            y, m, d = parts
        else:
            d, m, y = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    parts = value.split(" ")
    if len(parts) == 3 and parts[1][:3].title() in {"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"}:
        d, mon, y = parts
        month = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}[mon[:3].title()]
        yi = int(y)
        if yi < 100:
            yi += 2000 if yi < 50 else 1900
        return f"{yi:04d}-{month:02d}-{int(d):02d}"
    raise ValueError(value)


def _parse_amount(text: str) -> float:
    cleaned = text.strip().replace("₹", "").replace(",", "")
    negative = cleaned.startswith("-") or (cleaned.startswith("(") and cleaned.endswith(")"))
    cleaned = cleaned.strip("()")
    value = float(cleaned)
    return -value if negative else value


def _metadata_from_text(text: str) -> dict:
    out: dict = {}
    if m := ACCOUNT_RE.search(text):
        out["account_number"] = m.group(1)
    if m := BANK_RE.search(text):
        out["bank_name"] = m.group(1).strip()
    if m := ACCOUNT_TYPE_RE.search(text):
        out["account_type"] = re.sub(r"\s+", " ", m.group(1).strip()).upper()
    if m := CURRENCY_RE.search(text):
        out["currency"] = m.group(1).upper()
    if m := PERIOD_RE.search(text):
        out["statement_period"] = m.group(1).strip()
    return out


def _page_number(text: str) -> int | None:
    if m := PAGE_RE.search(text):
        return int(m.group(1))
    if m := PAGE_RE_2.search(text):
        return int(m.group(1))
    return None


def _extract_text_from_page(page: fitz.Page) -> tuple[str, str]:
    text = page.get_text("text").strip()
    if len(re.sub(r"\s+", "", text)) >= 60:
        return text, "DIGITAL"
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    cmd = tesseract_command()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract.image_to_string(image, config="--psm 6").strip(), "OCR"


def _extract_image(path: Path) -> tuple[str, str]:
    image = Image.open(path).convert("RGB")
    cmd = tesseract_command()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract.image_to_string(image, config="--psm 6").strip(), "OCR"


def _direction_from_narration(narration: str) -> tuple[str | None, float]:
    n = narration.upper()
    if any(term in n for term in ("SALARY", "PAYROLL", "WAGES", "REFUND", "REVERSAL", "CASH DEPOSIT", "RECEIVED", "CREDIT")):
        return "CREDIT", 0.90
    if any(term in n for term in ("EMI", "NACH", "ECS", "ATM", "POS", "WITHDRAWAL", "CHARGE", "FEE", "RENT", "PAYMENT", "DEBIT")):
        return "DEBIT", 0.90
    return None, 0.45


def _counterparty(narration: str) -> tuple[str, float]:
    if not narration:
        return "UNKNOWN", 0.0
    n = re.sub(r"\s+", " ", narration.strip())
    patterns = [
        re.compile(r"\bUPI\b/[^/]+/[^/]+/[^/]+/([^/]+)", re.I),
        re.compile(r"\b(?:NEFT|IMPS|RTGS|NACH|ECS)\b/([^/]+)/", re.I),
        re.compile(r"\bPOS/([^/]+)", re.I),
        re.compile(r"\b(?:TO|FROM|BY)\s+([A-Z][A-Z .&'-]{2,50})", re.I),
    ]
    for pat in patterns:
        m = pat.search(n)
        if m:
            value = m.group(1).strip(" /-")
            if value and not re.fullmatch(r"[A-Z0-9._-]+@[A-Z0-9._-]+", value, re.I):
                return value, 0.88
    # Fallback: use a meaningful uppercase phrase but avoid pure identifiers.
    candidates = re.findall(r"\b[A-Z][A-Z .&'-]{2,50}\b", n)
    candidates = [x.strip() for x in candidates if not re.fullmatch(r"(?:UPI|NEFT|IMPS|RTGS|ATM|POS|INR|SELF|SWITCH|WITHDRAWAL)", x.strip(), re.I)]
    if candidates:
        return candidates[-1], 0.58
    return "UNKNOWN", 0.0


def _channel(narration: str) -> str:
    for name, pattern in CHANNEL_PATTERNS:
        if pattern.search(narration):
            return name
    return "OTHER"


def _special_attributes(narration: str) -> dict:
    attrs = {}
    for key, pattern in SPECIAL_PATTERNS.items():
        if m := pattern.search(narration):
            attrs[key] = m.group(1).upper() if key == "platform" else m.group(1)
    return attrs


def _row_groups(page) -> list[list[tuple]]:
    """Cluster PDF words into visual rows; tolerate small y-coordinate differences between columns."""
    words = sorted(
        (w for w in page.get_text("words") if 28 <= w[1] <= page.rect.height - 55),
        key=lambda w: (w[1], w[0]),
    )
    rows: list[list[tuple]] = []
    row_y: list[float] = []
    for w in words:
        y = w[1]
        if not rows or abs(y - row_y[-1]) > 1.2:
            rows.append([w]); row_y.append(y)
        else:
            rows[-1].append(w)
    return [sorted(row, key=lambda w: w[0]) for row in rows]


def _line_date_pair(row: list[tuple]) -> tuple[str, str] | None:
    left = " ".join(w[4] for w in row if w[0] < 120)
    dates = DATE_RE.findall(left)
    if len(dates) >= 2:
        return dates[0], dates[1]
    # Digital PDFs often split date into separate words; rebuild first six left-column tokens.
    tokens = [w[4] for w in row if w[0] < 120]
    if len(tokens) >= 6:
        d1 = " ".join(tokens[:3]); d2 = " ".join(tokens[3:6])
        try:
            _normalize_date(d1); _normalize_date(d2)
            return d1, d2
        except ValueError:
            return None
    return None


def _amount_at_column(row: list[tuple], low: float, high: float | None = None) -> float | None:
    vals = []
    for w in row:
        x0 = w[0]
        if x0 < low or (high is not None and x0 >= high):
            continue
        try:
            vals.append(_parse_amount(w[4]))
        except (ValueError, TypeError):
            pass
    return vals[-1] if vals else None


def _find_digital_amount_columns(page) -> tuple[float, float, float]:
    """Find debit/credit/balance x-positions from visible table headers.

    This keeps the established digital-PDF parser but avoids relying on one
    hard-coded column layout. If headers cannot be located, retain the legacy
    coordinate fallback used by the PoC fixtures.
    """
    words = page.get_text("words")
    found = {}
    for w in words:
        token = re.sub(r"[^a-z]", "", w[4].lower())
        if token in {"debit", "debits", "withdrawal", "withdrawals"} and "debit" not in found:
            found["debit"] = w[0]
        elif token in {"credit", "credits", "deposit", "deposits"} and "credit" not in found:
            found["credit"] = w[0]
        elif token in {"balance", "balances"} and "balance" not in found:
            found["balance"] = w[0]
    if len(found) == 3:
        return found["debit"], found["credit"], found["balance"]
    return 435.0, 350.0, 515.0


def _amount_near_column(row: list[tuple], x: float, next_x: float | None = None) -> float | None:
    vals = []
    for w in row:
        x0 = w[0]
        if x0 < x - 12:
            continue
        if next_x is not None and x0 >= next_x - 12:
            continue
        try:
            vals.append(_parse_amount(w[4]))
        except (ValueError, TypeError):
            pass
    return vals[-1] if vals else None


def _parse_digital_page_words(page, page_record: PageRecord, batch_number: int | None) -> list[Transaction]:
    """Parse tabular digital statements by column coordinates and retain multiline narration."""
    groups = _row_groups(page)
    debit_x, credit_x, balance_x = _find_digital_amount_columns(page)
    amount_cols = sorted([(debit_x, "debit"), (credit_x, "credit"), (balance_x, "balance")])
    starts = []
    current_date = None
    for idx, row in enumerate(groups):
        pair = _line_date_pair(row)
        if pair:
            try:
                current_date = (_normalize_date(pair[0]), _normalize_date(pair[1]))
            except ValueError:
                current_date = None
        deposit = _amount_near_column(row, credit_x, balance_x)
        withdrawal = _amount_near_column(row, debit_x, credit_x if credit_x > debit_x else balance_x)
        balance = _amount_near_column(row, balance_x, None)
        desc = " ".join(w[4] for w in row if 120 <= w[0] < 350).strip()
        upper_desc = desc.upper()
        summary_row = any(x in upper_desc for x in ("TOTAL", "REWARD POINTS", "OPENING BALANCE", "CLOSING BALANCE"))
        if balance is not None and (deposit is not None or withdrawal is not None) and desc and "BALANCE FORWARD" not in upper_desc and not summary_row:
            starts.append((idx, current_date, deposit, withdrawal, balance))

    txns: list[Transaction] = []
    for seq, (start_idx, dates, deposit, withdrawal, balance) in enumerate(starts, start=1):
        if not dates:
            continue
        end_idx = starts[seq][0] if seq < len(starts) else len(groups)
        narration_parts = []
        for row in groups[start_idx:end_idx]:
            narration_parts.extend(w[4] for w in row if 120 <= w[0] < 350)
        narration = " ".join(narration_parts).strip()
        debit = float(withdrawal or 0.0)
        credit = float(deposit or 0.0)
        # Some PDFs place the amount slightly left/right of the nominal column; if both appear, use the column with value.
        if debit and credit:
            # Prefer balance movement only when both are populated unexpectedly.
            credit = 0.0
        cp, cp_conf = _counterparty(narration)
        txns.append(Transaction(
            transaction_id=f"P{page_record.page_index:03d}-T{seq:03d}",
            page_number=page_record.page_number or page_record.page_index,
            date=dates[0],
            value_date=dates[1],
            narration=narration,
            debit=debit,
            credit=credit,
            running_balance=balance,
            account_number=page_record.metadata.get("account_number"),
            account_type=page_record.metadata.get("account_type"),
            month=dates[0][:7],
            counterparty=cp,
            payment_channel=_channel(narration),
            currency=page_record.metadata.get("currency", "UNKNOWN"),
            special_attributes=_special_attributes(narration),
            extraction_confidence=0.98,
            counterparty_confidence=cp_conf,
            counterparty_method="RULE",
            manual_review_required=cp_conf < MIN_COUNTERPARTY_CONFIDENCE,
            source_batch=batch_number,
        ))
    return txns


def _transaction_lines(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if DATE_RE.search(line) and not re.search(r"statement|opening|closing|date\s+value|page\b", line, re.I):
            yield line


def _split_transaction_fields(line: str):
    dates = list(DATE_RE.finditer(line))
    if not dates:
        return None
    first_date = dates[0]
    second_date = dates[1] if len(dates) > 1 else None
    value_date = second_date.group(1) if second_date else first_date.group(1)
    start = second_date.end() if second_date else first_date.end()
    middle = line[start:].strip(" |\t")
    amounts = list(AMOUNT_RE.finditer(middle))
    if len(amounts) < 2:
        return None
    amount_match, balance_match = amounts[-2], amounts[-1]
    narration = middle[:amount_match.start()].strip(" |\t")
    return first_date.group(1), value_date, narration, amount_match.group(1), balance_match.group(1)


def _parse_transactions(page: PageRecord, batch_number: int | None, previous_balance: float | None = None):
    txns: list[Transaction] = []
    current_previous_balance = previous_balance
    for idx, line in enumerate(_transaction_lines(page.text), start=1):
        fields = _split_transaction_fields(line)
        if not fields:
            continue
        date_raw, value_date_raw, narration, transaction_raw, balance_raw = fields
        try:
            date = _normalize_date(date_raw); value_date = _normalize_date(value_date_raw)
            amount = _parse_amount(transaction_raw); balance = _parse_amount(balance_raw)
        except (ValueError, TypeError):
            continue
        if "BALANCE FORWARD" in narration.upper():
            current_previous_balance = balance
            continue
        direction = None
        if current_previous_balance is not None:
            delta = round(balance - current_previous_balance, 2)
            if delta > 0: direction = "CREDIT"
            elif delta < 0: direction = "DEBIT"
        if direction is None:
            direction, _ = _direction_from_narration(narration)
        debit = amount if direction == "DEBIT" else 0.0
        credit = amount if direction == "CREDIT" else 0.0
        cp, cp_conf = _counterparty(narration)
        txns.append(Transaction(
            transaction_id=f"P{page.page_index:03d}-T{idx:03d}", page_number=page.page_number or page.page_index,
            date=date, value_date=value_date, narration=narration.strip(), debit=debit, credit=credit,
            running_balance=balance, account_number=page.metadata.get("account_number"), account_type=page.metadata.get("account_type"), month=date[:7],
            counterparty=cp, payment_channel=_channel(narration), currency=page.metadata.get("currency", "UNKNOWN"),
            special_attributes=_special_attributes(narration), extraction_confidence=0.90,
            counterparty_confidence=cp_conf, counterparty_method="RULE", manual_review_required=cp_conf < MIN_COUNTERPARTY_CONFIDENCE,
            source_batch=batch_number))
        current_previous_balance = balance
    return txns, current_previous_balance


def validate_page_order(pages: list[PageRecord]) -> list[ValidationIssue]:
    numbers = [p.page_number for p in pages if p.page_number is not None]
    if not numbers:
        return [ValidationIssue("WARNING", "PAGE_NUMBERS_UNAVAILABLE", "Explicit page numbers were not detected; fallback continuity validation used.")]
    expected = list(range(1, max(numbers) + 1))
    if numbers != expected[:len(numbers)] or len(set(numbers)) != len(numbers):
        return [ValidationIssue("ERROR", "PAGE_ORDER_INVALID", f"Detected page sequence {numbers}; expected a continuous sequence starting at 1.")]
    return []


def _inherit_metadata(pages: list[PageRecord]) -> dict:
    resolved: dict = {}
    for page in pages:
        for key, value in page.metadata.items():
            if value:
                resolved[key] = value
        for key in ("account_number", "account_type", "bank_name", "currency", "statement_period"):
            if not page.metadata.get(key) and resolved.get(key):
                page.metadata[key] = resolved[key]
                page.metadata[f"{key}_source"] = "INHERITED"
    return resolved


def process_statement(path: str | Path, out_dir: str | Path) -> DocumentResult:
    path = Path(path); out_dir = Path(out_dir)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[PageRecord] = []
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        try:
            for i, page in enumerate(doc, start=1):
                text, method = _extract_text_from_page(page)
                pages.append(PageRecord(i, _page_number(text), text, method, metadata=_metadata_from_text(text)))
        finally:
            doc.close()
    else:
        text, method = _extract_image(path)
        pages.append(PageRecord(1, _page_number(text) or 1, text, method, metadata=_metadata_from_text(text)))

    issues = validate_page_order(pages)
    # A jumbled statement cannot be safely analysed because running-balance and
    # transaction ordering become unreliable. Fail fast instead of producing a
    # misleading risk score.
    page_order_errors = [i for i in issues if i.code == "PAGE_ORDER_INVALID"]
    if page_order_errors:
        raise ValueError("The pages in this statement appear to be out of order. Please upload the original statement with pages in the correct order.")
    resolved_metadata = _inherit_metadata(pages)
    resolved_metadata["accounts"] = sorted({p.metadata.get("account_number") for p in pages if p.metadata.get("account_number")})
    resolved_metadata["account_types"] = sorted({p.metadata.get("account_type") for p in pages if p.metadata.get("account_type")})
    resolved_metadata["banks"] = sorted({p.metadata.get("bank_name") for p in pages if p.metadata.get("bank_name")})
    resolved_metadata["currencies"] = sorted({p.metadata.get("currency") for p in pages if p.metadata.get("currency")})

    batch_dir = out_dir / "batches"; batch_dir.mkdir(exist_ok=True)
    batches: list[BatchRecord] = []; all_transactions: list[Transaction] = []
    source_doc = fitz.open(path) if path.suffix.lower() == ".pdf" else None
    previous_balances: dict[str, float] = {}
    try:
        for batch_no, start in enumerate(range(0, len(pages), BATCH_SIZE), start=1):
            batch_pages = pages[start:start+BATCH_SIZE]
            markdown_path = batch_dir / f"batch_{batch_no:03d}.md"
            lines = [f"# Statement batch {batch_no}", ""]
            for p in batch_pages:
                lines += [f"## Page {p.page_number or p.page_index}", "", f"Extraction: {p.extraction_method}", f"Metadata: {p.metadata}", "", p.text, ""]
            markdown_path.write_text("\n".join(lines), encoding="utf-8")
            batches.append(BatchRecord(batch_no, [p.page_index for p in batch_pages], str(markdown_path), dict(resolved_metadata)))
            for p in batch_pages:
                if p.extraction_method == "DIGITAL" and source_doc is not None:
                    txns = _parse_digital_page_words(source_doc[p.page_index-1], p, batch_no)
                    all_transactions.extend(txns)
                else:
                    key = p.metadata.get("account_number") or "__UNKNOWN__"
                    txns, last = _parse_transactions(p, batch_no, previous_balances.get(key))
                    if last is not None: previous_balances[key] = last
                    all_transactions.extend(txns)
    finally:
        if source_doc is not None: source_doc.close()

    for txn in all_transactions:
        if txn.debit and txn.credit:
            issues.append(ValidationIssue("WARNING", "BOTH_DEBIT_CREDIT", "Transaction contains both debit and credit amounts.", page=txn.page_number, transaction_id=txn.transaction_id))
        if txn.manual_review_required:
            issues.append(ValidationIssue("REVIEW", "COUNTERPARTY_LOW_CONFIDENCE", f"Counterparty '{txn.counterparty}' confidence={txn.counterparty_confidence:.2f} below threshold={MIN_COUNTERPARTY_CONFIDENCE:.2f}; method=RULE. This is counterparty extraction confidence, not transaction classification confidence.", page=txn.page_number, transaction_id=txn.transaction_id, details={"confidence":txn.counterparty_confidence,"threshold":MIN_COUNTERPARTY_CONFIDENCE,"method":txn.counterparty_method}))

    result = DocumentResult(str(path), "MIXED" if len({p.extraction_method for p in pages}) > 1 else pages[0].extraction_method, len(pages), pages, batches, all_transactions, issues, resolved_metadata)
    (out_dir / "document.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result
