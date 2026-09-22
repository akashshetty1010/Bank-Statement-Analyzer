from __future__ import annotations

import csv
import re
import json
import os
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Iterable
from functools import lru_cache

from .models import Transaction, ValidationIssue

DEFAULT_TAXONOMY = [
    ("CREDIT", "SALARY", ["salary", "payroll", "wages", "salary credit"], 0.98),
    ("CREDIT", "REFUND", ["refund", "reversal", "cashback", "credit adjustment", "cradj"], 0.94),
    ("CREDIT", "LOAN_DISBURSAL", ["loan disbursal", "loan disbursement"], 0.96),
    ("CREDIT", "P2P", ["upi", "imps", "transfer", "p2a"], 0.78),
    ("CREDIT", "INTEREST", ["interest credit", "interest"], 0.95),
    ("CREDIT", "INVESTMENT_REDEMPTION", ["redemption", "mutual fund redemption", "mf redemption"], 0.92),
    ("DEBIT", "EMI", ["emi", "home loan", "loan repayment", "nach", "ecs"], 0.97),
    ("DEBIT", "BANK_CHARGES", ["bank service charge", "service charge", "atm usage charges", "charges", "cgst", "sgst", "fee"], 0.96),
    ("DEBIT", "UTILITY", ["electricity", "tneb", "water", "broadband", "airtel", "jio", "mobile", "gas"], 0.93),
    ("DEBIT", "EDUCATION", ["tuition", "school", "college", "education", "fees"], 0.92),
    ("DEBIT", "GROCERY", ["grocery", "dmart", "reliance fresh", "supermarket", "bigbasket"], 0.92),
    ("DEBIT", "RENT", ["rent", "house rent"], 0.95),
    ("DEBIT", "TRAVEL", ["ixigo", "uber", "ola", "airline", "flight", "travel", "hotel", "railway", "season ticket"], 0.89),
    ("DEBIT", "SHOPPING", ["amazon", "lifestyle", "flipkart", "myntra", "purchase"], 0.82),
    ("DEBIT", "DINING", ["restaurant", "hotel", "food", "cafe", "swiggy", "zomato", "ananda vilas"], 0.87),
    ("DEBIT", "ATM_CASH", ["atm withdrawal", "cash withdrawal", "atm cash"], 0.98),
    ("DEBIT", "P2P", ["upi", "imps", "transfer", "p2a"], 0.78),
    ("DEBIT", "INVESTMENT", ["sip", "mutual fund", "demat", "investment", "stock", "equity"], 0.91),
]


def load_taxonomy(csv_path: str | Path | None = None) -> list[tuple[str, str, list[str], float]]:
    taxonomy = list(DEFAULT_TAXONOMY)
    if not csv_path:
        return taxonomy
    rows = []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"level_1", "category", "keywords"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("Taxonomy CSV must contain level_1,category,keywords columns")
        for r in reader:
            kws = [x.strip().lower() for x in (r.get("keywords") or "").split("|") if x.strip()]
            if not kws:
                continue
            conf = float(r.get("confidence") or 0.90)
            rows.append((r["level_1"].strip().upper(), r["category"].strip().upper(), kws, conf))
    # CSV rules are prepended so user rules override defaults.
    return rows + taxonomy


def _semantic_counterparty(txn: Transaction) -> str:
    text = f"{txn.narration} {txn.counterparty}".upper()
    if txn.counterparty and txn.counterparty != "UNKNOWN":
        return txn.counterparty.upper()
    if "SELF" in text:
        return "SELF"
    return "UNKNOWN"


def classify_transaction(txn: Transaction, taxonomy: list[tuple[str, str, list[str], float]]) -> tuple[str, str, float, str]:
    text = f"{txn.narration} {txn.counterparty}".lower()
    level1 = "CREDIT" if txn.credit > txn.debit else "DEBIT"

    # Strong semantic rules before generic keyword rules.
    if txn.credit > 0 and re.search(r"\b(salary|payroll|wages|salary credit)\b", text, re.I):
        return "CREDIT", "SALARY", 0.99, "RULE"
    if txn.credit > 0 and re.search(r"\b(refund|reversal|cradj|cashback)\b", text, re.I):
        return "CREDIT", "REFUND", 0.97, "RULE"
    if txn.debit > 0 and re.search(r"\b(emi|home loan|loan repayment|nach|ecs)\b", text, re.I):
        return "DEBIT", "EMI", 0.98, "RULE"
    if txn.debit > 0 and re.search(r"\b(atm withdrawal|cash withdrawal)\b", text, re.I):
        return "DEBIT", "ATM_CASH", 0.99, "RULE"
    if txn.debit > 0 and re.search(r"\b(cgst|sgst|service charge|usage charges|bank.*charge|fee)\b", text, re.I):
        return "DEBIT", "BANK_CHARGES", 0.97, "RULE"

    # Apply taxonomy keywords only when direction matches.
    for l1, category, keywords, conf in taxonomy:
        if l1 != level1:
            continue
        if any(k in text for k in keywords):
            # Avoid treating merchant UPI/POS as P2P when a merchant category matched.
            if category == "P2P" and any(k in text for k in ["amazon", "paytm", "phonepe", "airtel", "dmart", "reliance fresh", "ixigo"]):
                continue
            return level1, category, conf, "RULE"

    # Strong channel-based P2P fallback.
    if txn.payment_channel in {"UPI", "IMPS", "NEFT", "BANK_TRANSFER"}:
        cp = _semantic_counterparty(txn)
        if cp not in {"UNKNOWN", "SELF", "PAYTM", "AMAZONPAY", "AMAZON", "PHONEPE"}:
            return level1, "P2P", 0.76, "RULE"

    return level1, "OTHER", 0.55, "RULE"


def apply_manual_overrides(transactions: list[Transaction], csv_path: str | Path | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not csv_path:
        return issues
    rows = {}
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"transaction_id", "category"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("Review CSV must contain transaction_id,category columns")
        for r in reader:
            rows[r["transaction_id"].strip()] = r
    for txn in transactions:
        r = rows.get(txn.transaction_id)
        if not r:
            continue
        category = r["category"].strip().upper()
        l1 = r.get("level_1") or ("CREDIT" if txn.credit > txn.debit else "DEBIT")
        txn.classification_level_1 = l1.upper()
        txn.classification_level_2 = category
        txn.classification_confidence = 1.0
        txn.classification_method = "MANUAL"
        txn.manual_review_required = False
    return issues


@lru_cache(maxsize=1)
def _ollama_model() -> str | None:
    """Resolve the local Ollama model once per backend process instead of once per transaction."""
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("BSA_OLLAMA_MODEL")
    if model:
        return model
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        models = tags.get("models") or []
        return models[0].get("name") if models else None
    except Exception:
        return None


@lru_cache(maxsize=512)
def _ollama_cached_classification(narration: str, debit: float, credit: float, model: str) -> tuple[str, str, float, str] | None:
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    prompt = (
        "Classify this bank transaction. Return ONLY JSON with keys "
        "level_1,category,confidence. level_1 must be CREDIT or DEBIT. "
        "Allowed categories: SALARY, EMI, UTILITY, GROCERY, REMITTANCE, P2P, "
        "BANK_CHARGES, REFUND, INVESTMENT, LOAN_DISBURSAL, OTHER. Never invent facts.\n\n"
        f"Narration: {narration}\nDebit: {debit}\nCredit: {credit}\n"
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode()
    try:
        req = urllib.request.Request(f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parsed = json.loads(data.get("response", "{}"))
        l1 = str(parsed.get("level_1", "")).upper()
        cat = str(parsed.get("category", "")).upper()
        conf = float(parsed.get("confidence", 0.0))
        allowed = {"SALARY","EMI","UTILITY","GROCERY","REMITTANCE","P2P","BANK_CHARGES","REFUND","INVESTMENT","LOAN_DISBURSAL","OTHER"}
        if l1 in {"CREDIT", "DEBIT"} and cat in allowed and 0 <= conf <= 1:
            return l1, cat, conf, "LLM"
    except Exception:
        return None
    return None


def _ollama_fallback(txn: Transaction) -> tuple[str, str, float, str] | None:
    """Use local Ollama for ambiguous transactions, with process-level model and result caching."""
    model = _ollama_model()
    if not model:
        return None
    return _ollama_cached_classification(txn.narration.strip(), round(txn.debit, 2), round(txn.credit, 2), model)


def classify_document(result, taxonomy_csv: str | Path | None = None, review_csv: str | Path | None = None):
    taxonomy = load_taxonomy(taxonomy_csv)
    issues = []
    for txn in result.transactions:
        l1, cat, conf, method = classify_transaction(txn, taxonomy)
        if cat == "OTHER" and conf < 0.70:
            # Ollama fallback is enabled by default; no feature flag is required.
            llm_result = _ollama_fallback(txn)
            if llm_result:
                l1, cat, conf, method = llm_result
        txn.classification_level_1 = l1
        txn.classification_level_2 = cat
        txn.classification_confidence = conf
        txn.classification_method = method
        if conf < 0.70:
            txn.manual_review_required = True
            issues.append(ValidationIssue(
                "REVIEW",
                "CLASSIFICATION_LOW_CONFIDENCE",
                f"Classification '{cat}' confidence={conf:.2f} below threshold=0.70; method={method}.",
                page=txn.page_number,
                transaction_id=txn.transaction_id,
                details={"confidence": conf, "threshold": 0.70, "method": method},
            ))
    apply_manual_overrides(result.transactions, review_csv)
    # Avoid duplicate issues by transaction id.
    existing = {i.transaction_id for i in result.issues if i.code == "CLASSIFICATION_LOW_CONFIDENCE"}
    result.issues.extend([i for i in issues if i.transaction_id not in existing])
    return result
