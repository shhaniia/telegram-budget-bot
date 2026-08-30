"""OCR text extraction + best-effort parsing for receipt photos and
screenshots of Apple Pay / Apple Wallet purchase notifications.

OCR on real-world photos is never fully reliable, so this module only ever
produces a *suggestion* — the calling handler always shows the parsed
amount/description to the user for confirmation (and lets them edit it)
before anything is saved to the database.
"""
from __future__ import annotations

import io
import re

import pytesseract
from PIL import Image, ImageOps

_AMOUNT_RE = re.compile(
    r"(?:[$€£S]\s?)?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}(?!\d)"
)
_TOTAL_LINE_RE = re.compile(r"\b(total|amount|paid|charged)\b", re.IGNORECASE)
_SUBTOTAL_TAX_RE = re.compile(r"\b(sub ?total|tax|change|cash)\b", re.IGNORECASE)


def extract_text(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)  # respect phone photo orientation
    # Upscale small screenshots a bit and convert to grayscale — both tend to
    # improve tesseract's accuracy on receipts/screenshots noticeably.
    if image.width < 1000:
        scale = 1000 / image.width
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    image = image.convert("L")
    return pytesseract.image_to_string(image)


def _parse_amount(token: str) -> float | None:
    cleaned = re.sub(r"[^\d.,]", "", token)
    if not cleaned:
        return None
    # normalize "1.234,56" or "1,234.56" style thousands/decimal separators
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # ambiguous: "12,34" (decimal) vs "1,234" (thousands) — treat 2-digit
        # trailing group as decimal, which is the common receipt case
        parts = cleaned.split(",")
        cleaned = cleaned.replace(",", "") if len(parts[-1]) != 2 else cleaned.replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def guess_amount_and_description(text: str) -> tuple[float | None, str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    best_total = None
    for line in lines:
        if _SUBTOTAL_TAX_RE.search(line) and not _TOTAL_LINE_RE.search(line):
            continue
        if _TOTAL_LINE_RE.search(line):
            match = _AMOUNT_RE.search(line)
            if match:
                amt = _parse_amount(match.group())
                if amt is not None:
                    best_total = amt
                    break  # an explicit "total"/"paid" line wins outright

    if best_total is None:
        # Fall back to the largest plausible amount anywhere in the text —
        # works well for Apple Pay notification screenshots, which usually
        # show just one amount.
        amounts = []
        for line in lines:
            for match in _AMOUNT_RE.finditer(line):
                amt = _parse_amount(match.group())
                if amt is not None and 0 < amt < 100000:
                    amounts.append(amt)
        if amounts:
            best_total = max(amounts)

    # Description guess: prefer a line that looks like a merchant name — not
    # purely numeric/symbols, reasonably short, not a date/time stamp.
    description = ""
    for line in lines:
        if _AMOUNT_RE.fullmatch(line.strip()):
            continue
        if re.search(r"\d{1,2}[:/]\d{2}", line):  # looks like a time or date
            continue
        letters = re.sub(r"[^A-Za-z]", "", line)
        if len(letters) >= 3:
            description = line.strip()[:60]
            break

    return best_total, description
