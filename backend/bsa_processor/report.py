from __future__ import annotations

from html import escape
from pathlib import Path
from .models import DocumentResult


def write_report(result: DocumentResult, path: str | Path, risk=None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    review = [i for i in result.issues if i.severity == "REVIEW"]
    errors = [i for i in result.issues if i.severity == "ERROR"]
    warnings = [i for i in result.issues if i.severity == "WARNING"]
    rows = []
    issue_by_txn = {}
    for i in result.issues:
        if i.transaction_id:
            issue_by_txn.setdefault(i.transaction_id, []).append(i)
    for t in result.transactions:
        txn_issues = issue_by_txn.get(t.transaction_id, [])
        status = "REVIEW" if t.manual_review_required or txn_issues else "OK"
        rows.append(
            "<tr>"
            f"<td>{escape(t.date)}</td>"
            f"<td>{escape(t.account_number or '')}</td>"
            f"<td>{escape(t.narration)}</td>"
            f"<td>{escape(t.counterparty)}</td>"
            f"<td>{t.counterparty_confidence:.2f} ({escape(t.counterparty_method or '-')})</td>"
            f"<td>{escape(t.classification_level_1 or '-')}</td>"
            f"<td>{escape(t.classification_level_2 or '-')}</td>"
            f"<td>{t.classification_confidence:.2f} ({escape(t.classification_method or '-')})</td>" if t.classification_confidence is not None else
            "<td>-</td><td>-</td>"
        )
        rows[-1] += (
            f"<td>{t.debit:,.2f}</td>"
            f"<td>{t.credit:,.2f}</td>"
            f"<td>{escape(t.payment_channel)}</td>"
            f"<td>{escape(t.currency)}</td>"
            f"<td class='{status.lower()}'>{status}</td>"
            "</tr>"
        )
    issue_items = "".join(
        f"<li><strong>{escape(i.severity)}</strong> {escape(i.code)} - {escape(i.message)}"
        + (f" (page {i.page})" if i.page else "")
        + "</li>" for i in result.issues
    ) or "<li>No issues.</li>"

    risk_html = "<p>Risk scoring is not included in processing-only mode.</p>"
    if risk:
        component_rows = "".join(
            f"<tr><td>{escape(name.replace('_',' ').title())}</td><td>{comp.score:.1f}</td><td>{comp.weight:.0%}</td><td>{comp.contribution:.1f}</td><td>{escape(comp.explanation)}</td></tr>"
            for name, comp in risk.components.items()
        )
        flags = "".join(f"<li>{escape(x)}</li>" for x in risk.flags) or "<li>No review/fraud flags.</li>"
        risk_html = f"""
<section><h2>Credit Risk Summary</h2>
<div class='riskbox'><div><div class='k'>Score</div><div class='score'>{risk.composite_score}/1000</div></div><div><div class='k'>Rating</div><div class='rating'>{escape(risk.rating)}</div></div><div><div class='k'>Recommendation</div><div class='rating'>{escape(risk.recommendation)}</div></div></div>
<table><thead><tr><th>Component</th><th>Score</th><th>Weight</th><th>Contribution</th><th>Explanation</th></tr></thead><tbody>{component_rows}</tbody></table>
<h3>Flags</h3><ul>{flags}</ul>
<h3>Scoring assumptions</h3><ul>{''.join(f'<li>{escape(a)}</li>' for a in risk.assumptions)}</ul>
</section>"""

    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>BSA Analysis Report</title>
<style>
body{{font-family:Inter,Segoe UI,Arial,sans-serif;margin:32px;background:#f7f8fa;color:#17191d}}
.wrap{{max-width:1500px;margin:auto}} h1{{margin-bottom:4px}} .sub{{color:#667085}}
.grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:24px 0}}
.card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:16px}} .k{{font-size:12px;color:#667085}} .v{{font-size:24px;font-weight:700;margin-top:4px}}
section{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:20px;margin-top:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{padding:9px;border-bottom:1px solid #eef0f3;text-align:left;vertical-align:top}} th{{color:#667085;font-size:12px}}
.ok{{color:#067647;font-weight:600}} .review{{color:#b54708;font-weight:600}} ul{{line-height:1.6}}
.riskbox{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}} .score,.rating{{font-size:30px;font-weight:700;margin-top:6px}}
</style></head><body><div class='wrap'>
<h1>Bank Statement Analyzer</h1>
<div class='sub'>{escape(result.source_file)}</div>
<div class='grid'>
<div class='card'><div class='k'>Pages</div><div class='v'>{result.page_count}</div></div>
<div class='card'><div class='k'>3-page batches</div><div class='v'>{len(result.batches)}</div></div>
<div class='card'><div class='k'>Transactions</div><div class='v'>{len(result.transactions)}</div></div>
<div class='card'><div class='k'>Review items</div><div class='v'>{len(review)}</div></div>
<div class='card'><div class='k'>Warnings</div><div class='v'>{len(warnings)}</div></div>
<div class='card'><div class='k'>Errors</div><div class='v'>{len(errors)}</div></div>
</div>
<section><h2>Resolved metadata</h2><pre>{escape(str(result.resolved_metadata))}</pre></section>
{risk_html}
<section><h2>Validation / review issues</h2><ul>{issue_items}</ul></section>
<section><h2>Transactions</h2><p class='sub'>Classification fields show the required classification_confidence and classification_method. Counterparty confidence is tracked separately.</p><table><thead><tr><th>Date</th><th>Account</th><th>Narration</th><th>Counterparty</th><th>CP confidence / method</th><th>L1</th><th>L2</th><th>Classification confidence / method</th><th>Debit</th><th>Credit</th><th>Channel</th><th>Currency</th><th>Status</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
</div></body></html>"""
    path.write_text(html, encoding="utf-8")
