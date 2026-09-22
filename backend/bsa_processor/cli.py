from __future__ import annotations

import argparse
import json
from pathlib import Path

from .processor import process_statement
from .analysis import analyze_statement
from .sample_data import generate_samples
from .report import write_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Lightweight BSA statement processing PoC")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-samples")
    gen.add_argument("--out", default="samples")

    ana = sub.add_parser("analyze")
    ana.add_argument("input")
    ana.add_argument("--out", default="runs/latest")
    ana.add_argument("--taxonomy", default=None)
    ana.add_argument("--review-csv", default=None)

    proc = sub.add_parser("process")
    proc.add_argument("input")
    proc.add_argument("--out", default="runs/latest")

    args = parser.parse_args()
    if args.command == "generate-samples":
        generated = generate_samples(Path(args.out))
        print(json.dumps(generated, indent=2))
        return

    if args.command == "analyze":
        result, risk, payload = analyze_statement(args.input, args.out, args.taxonomy, args.review_csv)
        write_report(result, Path(args.out) / "report.html", risk=risk)
        summary = {
            "mode": "ANALYZE",
            "source_file": result.source_file,
            "pages": result.page_count,
            "batches": len(result.batches),
            "transactions": len(result.transactions),
            "errors": sum(i.severity == "ERROR" for i in result.issues),
            "warnings": sum(i.severity == "WARNING" for i in result.issues),
            "review": sum(i.severity == "REVIEW" for i in result.issues),
            "classification_methods": sorted({t.classification_method for t in result.transactions}),
            "risk_score": risk.composite_score,
            "rating": risk.rating,
            "recommendation": risk.recommendation,
            "output": str(Path(args.out).resolve()),
        }
        print(json.dumps(summary, indent=2))
        return

    result = process_statement(args.input, args.out)
    write_report(result, Path(args.out) / "report.html")
    summary = {
        "source_file": result.source_file,
        "pages": result.page_count,
        "batches": len(result.batches),
        "transactions": len(result.transactions),
        "errors": sum(i.severity == "ERROR" for i in result.issues),
        "warnings": sum(i.severity == "WARNING" for i in result.issues),
        "manual_review": sum(i.severity == "REVIEW" for i in result.issues),
        "output": str(Path(args.out).resolve()),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
