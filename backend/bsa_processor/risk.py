from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

WEIGHTS = {
    "income_stability": 0.25,
    "debt_service": 0.20,
    "liquidity": 0.15,
    "banking_behaviour": 0.10,
    "fraud_indicators": 0.15,
    "expense_management": 0.15,
}

RATING_BANDS = [
    (900, "Excellent"),
    (750, "Good"),
    (600, "Fair"),
    (450, "Below Average"),
    (0, "Poor"),
]


@dataclass
class RiskComponent:
    score: float
    weight: float
    contribution: float
    metrics: dict[str, Any]
    explanation: str


@dataclass
class RiskSummary:
    components: dict[str, RiskComponent]
    composite_score: int
    rating: str
    recommendation: str
    flags: list[str]
    assumptions: list[str]

    def to_dict(self):
        return asdict(self)


def _months(txns):
    return sorted({t.month for t in txns})


def _safe_score(value: float) -> float:
    return max(0.0, min(100.0, round(value, 2)))


def _monthly_totals(txns, l1=None, categories=None):
    out = defaultdict(float)
    for t in txns:
        if l1 and t.classification_level_1 != l1:
            continue
        if categories and t.classification_level_2 not in categories:
            continue
        out[t.month] += t.credit if l1 == "CREDIT" else t.debit
    return dict(out)


def _income_stability(txns):
    months = _months(txns)
    salary_by_month = _monthly_totals(txns, "CREDIT", {"SALARY"})
    income_sources = {t.counterparty for t in txns if t.credit > 0 and t.classification_level_2 in {"SALARY", "OTHER", "P2P", "INTEREST", "INVESTMENT_REDEMPTION"} and t.counterparty not in {"UNKNOWN", "SELF"}}
    monthly_values = list(salary_by_month.values())
    regularity = (len(monthly_values) / len(months) * 100) if months else 0
    consistency = 100.0
    if len(monthly_values) >= 2 and statistics.mean(monthly_values) > 0:
        cv = statistics.pstdev(monthly_values) / statistics.mean(monthly_values)
        consistency = max(0.0, 100 - cv * 100)
    diversity = min(100.0, len(income_sources) * 25)
    growth = 50.0
    if len(monthly_values) >= 2:
        first, last = monthly_values[0], monthly_values[-1]
        growth = max(0.0, min(100.0, 50 + ((last - first) / max(abs(first), 1)) * 100))
    score = 0.35 * regularity + 0.30 * consistency + 0.20 * diversity + 0.15 * growth
    explanation = f"Salary regularity {regularity:.0f}%, consistency {consistency:.0f}%, {len(income_sources)} identifiable income source(s)."
    return RiskComponent(_safe_score(score), WEIGHTS["income_stability"], score * WEIGHTS["income_stability"], {
        "monthly_salary": salary_by_month,
        "salary_months": len(monthly_values),
        "income_source_count": len(income_sources),
        "regularity_pct": round(regularity, 2),
        "consistency_pct": round(consistency, 2),
        "growth_score": round(growth, 2),
    }, explanation)


def _debt_service(txns):
    emi_by_month = _monthly_totals(txns, "DEBIT", {"EMI"})
    salary_by_month = _monthly_totals(txns, "CREDIT", {"SALARY"})
    total_emi = sum(emi_by_month.values())
    avg_income = statistics.mean(list(salary_by_month.values())) if salary_by_month else 0
    foir = (total_emi / max(avg_income * max(len(emi_by_month), 1), 1)) if avg_income else None
    bounce_terms = ["bounce", "returned", "return", "failed", "dishonour", "dishonored"]
    bounce_count = sum(any(k in t.narration.lower() for k in bounce_terms) for t in txns)
    foir_score = 50.0 if foir is None else max(0.0, min(100.0, 100 - foir * 180))
    bounce_score = max(0.0, 100 - bounce_count * 20)
    score = 0.70 * foir_score + 0.30 * bounce_score
    return RiskComponent(_safe_score(score), WEIGHTS["debt_service"], score * WEIGHTS["debt_service"], {
        "emi_by_month": emi_by_month,
        "total_emi": round(total_emi, 2),
        "foir": round(foir, 4) if foir is not None else None,
        "bounce_count": bounce_count,
    }, f"FOIR {foir:.1%} if determinable; {bounce_count} bounce/return indicator(s)." if foir is not None else f"FOIR unavailable from observed salary data; {bounce_count} bounce/return indicator(s).")


def _liquidity(txns):
    daily = {}
    for t in txns:
        if t.running_balance is not None:
            daily[t.date] = t.running_balance
    balances = list(daily.values())
    avg_bal = statistics.mean(balances) if balances else 0
    min_bal = min(balances) if balances else 0
    negative_days = sum(v < 0 for v in balances)
    # Simple scale: >= 100k avg gets 90, zero gets 40, negative lowers further.
    base = 40 + min(60, max(0, avg_bal) / 100000 * 60)
    score = base - negative_days * 10
    return RiskComponent(_safe_score(score), WEIGHTS["liquidity"], score * WEIGHTS["liquidity"], {
        "average_eod_balance": round(avg_bal, 2),
        "minimum_eod_balance": round(min_bal, 2),
        "negative_balance_days": negative_days,
    }, f"Average EOD balance {avg_bal:,.2f}; minimum {min_bal:,.2f}; {negative_days} negative-balance day(s).")


def _banking_behaviour(txns):
    charge_categories = {"BANK_CHARGES"}
    charge_total = sum(t.debit for t in txns if t.classification_level_2 in charge_categories)
    bounce_terms = ["bounce", "returned", "dishonour", "dishonored", "failed"]
    bounce_count = sum(any(k in t.narration.lower() for k in bounce_terms) for t in txns)
    overdraft_count = sum("overdraft" in t.narration.lower() for t in txns)
    score = 100 - bounce_count * 20 - overdraft_count * 20 - min(charge_total / 500, 30)
    return RiskComponent(_safe_score(score), WEIGHTS["banking_behaviour"], score * WEIGHTS["banking_behaviour"], {
        "bounce_count": bounce_count,
        "bank_charge_total": round(charge_total, 2),
        "overdraft_count": overdraft_count,
    }, f"{bounce_count} bounce indicator(s), {charge_total:,.2f} bank charges, {overdraft_count} overdraft indicator(s).")


def _fraud_indicators(txns, document_result):
    mismatches = 0
    previous_by_account = {}
    for t in sorted(txns, key=lambda x: (x.account_number or "", x.date, x.transaction_id)):
        if t.running_balance is None:
            continue
        key = t.account_number or "__UNKNOWN__"
        prev = previous_by_account.get(key)
        if prev is not None:
            expected = round(prev + t.credit - t.debit, 2)
            if abs(expected - t.running_balance) > 0.02:
                mismatches += 1
        previous_by_account[key] = t.running_balance
    circular = 0
    transfers = [t for t in txns if t.classification_level_2 == "P2P"]
    for i, a in enumerate(transfers):
        for b in transfers[i + 1:]:
            if a.counterparty != "UNKNOWN" and a.counterparty == b.counterparty and a.date != b.date and abs(a.debit - b.credit) <= 1:
                circular += 1
    structuring = 0
    by_date = defaultdict(list)
    for t in txns:
        if t.debit > 0:
            by_date[t.date].append(t.debit)
    for amounts in by_date.values():
        rounded = [round(x, -2) for x in amounts if x >= 9000]
        if len(rounded) >= 3 and len(set(rounded)) == 1:
            structuring += 1
    score = max(0.0, 100 - mismatches * 15 - circular * 10 - structuring * 10)
    flags = []
    if mismatches:
        flags.append(f"{mismatches} balance arithmetic mismatch(es)")
    if circular:
        flags.append(f"{circular} possible circular transfer pattern(s)")
    if structuring:
        flags.append(f"{structuring} possible structuring pattern(s)")
    return RiskComponent(_safe_score(score), WEIGHTS["fraud_indicators"], score * WEIGHTS["fraud_indicators"], {
        "balance_mismatches": mismatches,
        "possible_circular_patterns": circular,
        "possible_structuring_patterns": structuring,
    }, "; ".join(flags) if flags else "No heuristic integrity/fraud flags detected in the available data."), flags


def _expense_management(txns):
    essential = {"EMI", "UTILITY", "GROCERY", "EDUCATION", "RENT", "BANK_CHARGES"}
    debit = [t for t in txns if t.debit > 0]
    essential_total = sum(t.debit for t in debit if t.classification_level_2 in essential)
    discretionary_total = sum(t.debit for t in debit if t.classification_level_2 not in essential)
    total = essential_total + discretionary_total
    discretionary_ratio = discretionary_total / total if total else 0
    score = max(0.0, min(100.0, 100 - discretionary_ratio * 100))
    return RiskComponent(_safe_score(score), WEIGHTS["expense_management"], score * WEIGHTS["expense_management"], {
        "essential_expenses": round(essential_total, 2),
        "discretionary_expenses": round(discretionary_total, 2),
        "discretionary_ratio": round(discretionary_ratio, 4),
    }, f"Essential expenses {essential_total:,.2f}; discretionary/other debits {discretionary_total:,.2f}.")


def calculate_risk(result) -> RiskSummary:
    components = {}
    flags = []
    income = _income_stability(result.transactions)
    debt = _debt_service(result.transactions)
    liquidity = _liquidity(result.transactions)
    banking = _banking_behaviour(result.transactions)
    fraud, fraud_flags = _fraud_indicators(result.transactions, result)
    expense = _expense_management(result.transactions)
    for name, component in [
        ("income_stability", income),
        ("debt_service", debt),
        ("liquidity", liquidity),
        ("banking_behaviour", banking),
        ("fraud_indicators", fraud),
        ("expense_management", expense),
    ]:
        components[name] = component
    flags.extend(fraud_flags)
    review_count = sum(1 for t in result.transactions if t.manual_review_required)
    if review_count:
        flags.append(f"{review_count} transaction(s) require manual review")
    weighted = sum(c.score * c.weight for c in components.values())
    score = int(round(weighted * 10))
    rating = next(label for threshold, label in RATING_BANDS if score >= threshold)
    if review_count or fraud_flags:
        recommendation = "REFER"
    elif score >= 750:
        recommendation = "APPROVE"
    elif score >= 600:
        recommendation = "APPROVE WITH CONDITIONS"
    else:
        recommendation = "DECLINE"
    assumptions = [
        "Salary is based on transactions classified as SALARY; P2P receipts are not treated as salary.",
        "FOIR uses observed EMI and salary transactions; missing due dates are not inferred.",
        "Fraud indicators are heuristics and are not definitive fraud determinations.",
        "If timestamps are unavailable, overnight activity is not scored.",
    ]
    return RiskSummary(components, score, rating, recommendation, flags, assumptions)
