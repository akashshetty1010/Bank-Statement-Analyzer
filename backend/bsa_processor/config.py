from pathlib import Path
import os
import shutil

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
BATCH_SIZE = 3
MIN_COUNTERPARTY_CONFIDENCE = 0.70


def tesseract_command() -> str | None:
    configured = os.getenv("TESSERACT_CMD")
    return configured or shutil.which("tesseract")
