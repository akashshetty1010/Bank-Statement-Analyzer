from __future__ import annotations

import csv
from pathlib import Path

from .classifier import classify_document
from .processor import process_statement
from .risk import calculate_risk


def analyze_statement(input_path: str | Path, out_dir: str | Path, taxonomy_csv: str | Path | None = None, review_csv: str | Path | None = None):
    out = Path(out_dir)
    result = process_statement(input_path, out)
    classify_document(result, taxonomy_csv=taxonomy_csv, review_csv=review_csv)
    risk = calculate_risk(result)
    payload = result.to_dict()
    payload["risk_summary"] = risk.to_dict()
    import json
    (out / "analysis.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return result, risk, payload
