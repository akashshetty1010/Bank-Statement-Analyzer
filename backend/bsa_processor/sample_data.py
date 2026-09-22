from __future__ import annotations

from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from PIL import Image
import fitz
import json

ROWS = [
    ("05/01/2025", "05/01/2025", "SALARY CREDIT / ACME TECHNOLOGIES", "", "85000.00", "120000.00"),
    ("06/01/2025", "06/01/2025", "UPI/PAYTM/RAHUL SHARMA", "5000.00", "", "115000.00"),
    ("10/01/2025", "10/01/2025", "NACH/ABC BANK/EMI HOME LOAN 123456789", "18500.00", "", "96500.00"),
    ("12/01/2025", "12/01/2025", "POS/DMART GROCERY", "4250.00", "", "92250.00"),
    ("18/02/2025", "18/02/2025", "UPI/RAHUL SHARMA", "", "7500.00", "99750.00"),
    ("20/02/2025", "20/02/2025", "NEFT/ACME TECHNOLOGIES/SALARY", "", "85000.00", "184750.00"),
    ("25/02/2025", "25/02/2025", "ATM CASH WITHDRAWAL", "10000.00", "", "174750.00"),
    ("03/03/2025", "03/03/2025", "AMAZON PAY REFUND", "", "2300.00", "177050.00"),
    ("07/03/2025", "07/03/2025", "UPI/PHONEPE/RELIANCE FRESH", "2800.00", "", "174250.00"),
    ("10/03/2025", "10/03/2025", "NACH/ABC BANK/EMI HOME LOAN 123456789", "18500.00", "", "155750.00"),
    ("15/03/2025", "15/03/2025", "BANK SERVICE CHARGE", "250.00", "", "155500.00"),
    ("20/03/2025", "20/03/2025", "NEFT/RENT PAYMENT/ANIL KUMAR", "22000.00", "", "133500.00"),
]


# Page groups intentionally exercise metadata inheritance and multiple accounts.
PAGE_CONFIG = [
    {"account": "12345", "bank": "HDFC Bank (Synthetic)", "currency": "INR", "period": "01/01/2025 - 31/03/2025", "rows": ROWS[0:4]},
    {"account": None, "bank": None, "currency": None, "period": None, "rows": ROWS[4:7]},
    {"account": None, "bank": None, "currency": None, "period": None, "rows": ROWS[7:10]},
    {"account": "678901", "bank": "ICICI Bank (Synthetic)", "currency": "INR", "period": "01/01/2025 - 31/03/2025", "rows": ROWS[2:5]},
    {"account": None, "bank": None, "currency": None, "period": None, "rows": ROWS[5:8]},
    {"account": None, "bank": None, "currency": None, "period": None, "rows": ROWS[9:12]},
]


def _draw_page(c: canvas.Canvas, page_no: int, total: int, cfg: dict) -> None:
    c.setFont("Helvetica-Bold", 12)
    c.drawString(36, 810, "BANK STATEMENT - SYNTHETIC TEST DATA / NOT A REAL BANK STATEMENT")
    c.setFont("Helvetica", 9)
    y = 790
    if cfg["bank"]:
        c.drawString(36, y, f"Bank Name: {cfg['bank']}"); y -= 14
    if cfg["account"]:
        c.drawString(36, y, f"Account Number: {cfg['account']}"); y -= 14
    if cfg["currency"]:
        c.drawString(36, y, f"Currency: {cfg['currency']}"); y -= 14
    if cfg["period"]:
        c.drawString(36, y, f"Statement Period: {cfg['period']}"); y -= 18
    c.drawRightString(555, 790, f"Page {page_no} of {total}")

    cols = [36, 100, 172, 400, 465, 520, 555]
    headers = ["Date", "Value Date", "Narration", "Debit", "Credit", "Balance"]
    y -= 4
    c.line(32, y, 560, y)
    for i, h in enumerate(headers):
        c.drawString(cols[i], y - 14, h)
    y -= 20
    c.line(32, y, 560, y)
    for row in cfg["rows"]:
        values = list(row)
        for i, v in enumerate(values):
            c.drawString(cols[i], y - 14, v)
        y -= 22
        c.line(32, y, 560, y)
    c.saveState()
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(36, 28, "Synthetic Indian-bank-format fixture generated for BSA PoC testing.")
    c.restoreState()


def generate_samples(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    digital = out_dir / "indian_bank_digital.pdf"
    c = canvas.Canvas(str(digital), pagesize=A4)
    total = len(PAGE_CONFIG)
    for i, cfg in enumerate(PAGE_CONFIG, start=1):
        _draw_page(c, i, total, cfg)
        c.showPage()
    c.save()

    # Create scanned version by rasterizing the digital PDF and rebuilding a PDF from images.
    scanned = out_dir / "indian_bank_scanned.pdf"
    doc = fitz.open(digital)
    images = []
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(image)
    first, *rest = images
    first.save(scanned, save_all=True, append_images=rest, resolution=144)

    # Jumbled page variant for validation.
    jumbled = out_dir / "indian_bank_jumbled.pdf"
    source = fitz.open(digital)
    order = [0, 1, 3, 2, 4, 5]
    out = fitz.open()
    for idx in order:
        out.insert_pdf(source, from_page=idx, to_page=idx)
    out.save(jumbled)

    ground_truth = {
        "digital": {
            "expected_pages": 6,
            "expected_batches": 2,
            "expected_transactions_min": 10,
            "currencies": ["INR"],
            "accounts": ["12345", "678901"],
            "expected_channels": ["UPI", "NACH/ECS", "POS", "ATM", "NEFT"],
        },
        "jumbled": {"expected_error": "PAGE_ORDER_INVALID"},
    }
    (out_dir / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")
    return {"digital": str(digital), "scanned": str(scanned), "jumbled": str(jumbled), "ground_truth": str(out_dir / "ground_truth.json")}
