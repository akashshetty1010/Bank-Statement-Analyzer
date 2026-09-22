from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / "backend"
if str(PROCESSOR) not in sys.path:
    sys.path.insert(0, str(PROCESSOR))

from bsa_processor.analysis import analyze_statement
from bsa_processor.report import write_report

UPLOAD_DIR = ROOT / "data" / "uploads"
OUTPUT_DIR = ROOT / "data" / "outputs"
ERROR_LOG = ROOT / "data" / "error_history.jsonl"
FRONTEND_DIR = ROOT / "frontend"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # upload must be strictly under 10 MB

app = FastAPI(title="Bank Statement Analyzer", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _log_error(*, document_id: str, filename: str | None, suffix: str, size_bytes: int, content_type: str | None,
               started_at: str, stage: str, user_message: str, technical_error: str, tb: str | None = None):
    """Append one local, machine-readable historical error record. Never blocks the user response."""
    try:
        failed = datetime.now(timezone.utc)
        started = datetime.fromisoformat(started_at)
        record = {
            "timestamp_utc": failed.isoformat(),
            "started_at_utc": started_at,
            "failed_at_utc": failed.isoformat(),
            "duration_ms": max(0, round((failed - started).total_seconds() * 1000, 2)),
            "document_id": document_id,
            "file": {
                "original_filename": filename,
                "extension": suffix,
                "size_bytes": size_bytes,
                "content_type": content_type,
            },
            "stage": stage,
            "user_message": user_message,
            "technical_error": technical_error,
        }
        if tb:
            record["traceback"] = tb
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load(document_id: str):
    path = OUTPUT_DIR / document_id / "analysis.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/api/v1/documents/upload")
def upload_document(file: UploadFile = File(...)):
    started_at = datetime.now(timezone.utc).isoformat()
    suffix = Path(file.filename or "").suffix.lower()
    document_id = uuid.uuid4().hex[:12]
    work = OUTPUT_DIR / document_id
    work.mkdir(parents=True, exist_ok=True)
    source = UPLOAD_DIR / f"{document_id}{suffix}"
    total = 0

    if suffix not in ALLOWED:
        msg = "This file type isn’t supported. Please upload a PDF or an image (PNG, JPG, or JPEG)."
        _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=0, content_type=file.content_type,
                   started_at=started_at, stage="file_validation", user_message=msg, technical_error=f"Unsupported extension: {suffix}")
        raise HTTPException(status_code=400, detail=msg)
    try:
        with source.open("wb") as dst:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total >= MAX_FILE_BYTES:
                    msg = "File is too large. Please upload a file strictly under 10 MB."
                    _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=total, content_type=file.content_type,
                               started_at=started_at, stage="file_validation", user_message=msg, technical_error=f"File size {total} bytes reached the 10 MB boundary")
                    raise HTTPException(status_code=413, detail=msg)
                dst.write(chunk)
        if total == 0:
            msg = "The uploaded file is empty. Please select a valid bank statement."
            _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=0, content_type=file.content_type,
                       started_at=started_at, stage="file_validation", user_message=msg, technical_error="Uploaded file contained zero bytes")
            raise HTTPException(status_code=400, detail=msg)

        try:
            result, risk, payload = analyze_statement(source, work)
        except ValueError as exc:
            message = str(exc)
            if "pages in this statement appear to be out of order" in message:
                user_msg = message
                stage = "page_order_validation"
            else:
                user_msg = "We couldn’t find a readable bank statement in this file. Please upload a valid bank statement and try again."
                stage = "statement_extraction"
            _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=total, content_type=file.content_type,
                       started_at=started_at, stage=stage, user_message=user_msg, technical_error=message, tb=traceback.format_exc())
            raise HTTPException(status_code=422, detail=user_msg) from exc
        except Exception as exc:
            user_msg = "We couldn’t process this statement. Please check that it is a readable bank statement and try again."
            _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=total, content_type=file.content_type,
                       started_at=started_at, stage="analysis", user_message=user_msg, technical_error=str(exc), tb=traceback.format_exc())
            raise HTTPException(status_code=422, detail=user_msg) from exc

        if not result.transactions:
            msg = "No statements found in the uploaded file, please check."
            _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=total, content_type=file.content_type,
                       started_at=started_at, stage="statement_extraction", user_message=msg, technical_error="Extraction completed but produced zero transactions", tb=None)
            raise HTTPException(status_code=422, detail=msg)

        payload["document_id"] = document_id
        payload["original_filename"] = file.filename
        (work / "analysis.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        write_report(result, work / "report.html", risk=risk)
        return {"document_id": document_id, "status": "completed", "analysis": payload}
    except HTTPException:
        raise
    except Exception as exc:
        msg = "We couldn’t process this statement. Please check the file and try again."
        _log_error(document_id=document_id, filename=file.filename, suffix=suffix, size_bytes=total, content_type=file.content_type,
                   started_at=started_at, stage="upload_or_processing", user_message=msg, technical_error=str(exc), tb=traceback.format_exc())
        raise HTTPException(status_code=422, detail=msg) from exc


@app.get("/api/v1/documents/{document_id}/analysis")
def get_analysis(document_id: str):
    return _load(document_id)


@app.get("/api/v1/documents/{document_id}/export/json")
def export_json(document_id: str):
    path = OUTPUT_DIR / document_id / "analysis.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(path, media_type="application/json", filename=f"{document_id}_analysis.json")


@app.get("/api/v1/documents/{document_id}/report")
def report(document_id: str):
    path = OUTPUT_DIR / document_id / "report.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path, media_type="text/html", filename=f"{document_id}_report.html")


@app.get("/results/{document_id}")
def result_page(document_id: str):
    path = FRONTEND_DIR / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(path, media_type="text/html")

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
