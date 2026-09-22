from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ValidationIssue:
    severity: str  # ERROR | WARNING | REVIEW
    code: str
    message: str
    page: int | None = None
    transaction_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageRecord:
    page_index: int
    page_number: int | None
    text: str
    extraction_method: str
    metadata: dict[str, Any] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class BatchRecord:
    batch_number: int
    page_indices: list[int]
    markdown_path: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Transaction:
    transaction_id: str
    page_number: int
    date: str
    value_date: str | None
    narration: str
    debit: float
    credit: float
    running_balance: float | None
    account_number: str | None
    month: str
    counterparty: str
    payment_channel: str
    currency: str
    special_attributes: dict[str, Any] = field(default_factory=dict)
    account_type: str | None = None
    extraction_confidence: float = 0.0
    counterparty_confidence: float = 0.0
    counterparty_method: str | None = None  # RULE | LLM | MANUAL
    manual_review_required: bool = False
    classification_level_1: str | None = None
    classification_level_2: str | None = None
    classification_confidence: float | None = None
    classification_method: str | None = None
    source_batch: int | None = None


@dataclass
class DocumentResult:
    source_file: str
    extraction_method: str
    page_count: int
    pages: list[PageRecord]
    batches: list[BatchRecord]
    transactions: list[Transaction]
    issues: list[ValidationIssue]
    resolved_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
