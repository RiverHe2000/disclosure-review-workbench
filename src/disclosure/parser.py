"""Native PDF text candidates with physical-page coordinates and page-local context.

No OCR, model, gold labels, or bank-specific page map is used. Candidates are rows,
not verified financial facts. Wide multi-column pages can still need human review.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

PARSER_VERSION = "native-rows-v3"
METRIC_PATTERNS = {
    "cet1_ratio": re.compile(r"common\s+equity\s+tier\s*1|\bCET\s*1\b", re.I),
    "total_capital_ratio": re.compile(r"total\s+(?:regulatory\s+)?capital\s+(?:adequacy\s+)?ratio|capital\s+adequacy\s*[–—-]\s*total", re.I),
    "rwa": re.compile(r"risk[\s-]*weighted\s+assets|\bRWA\b|\bRWAs\b", re.I),
    "lcr": re.compile(r"liquidity\s+coverage\s+ratio|\bLCR\b", re.I),
    "nsfr": re.compile(r"net\s+stable\s+funding\s+ratio|\bNSFR\b", re.I),
}
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z\d])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z\d])")


def metric_ids(text: str) -> list[str]:
    return [key for key, pattern in METRIC_PATTERNS.items() if pattern.search(text)]


def _rows(words: list[dict]) -> list[dict]:
    """Group nearby text baselines while preserving geometry from pdfplumber."""
    groups: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        # Superscripts should not create detached footnote rows.
        matching = next((group for group in reversed(groups[-3:])
                         if abs(float(group[0]["top"]) - float(word["top"])) <= 3.0), None)
        if matching is None:
            groups.append([word])
        else:
            matching.append(word)
    result = []
    for group in groups:
        group.sort(key=lambda item: item["x0"])
        segments: list[list[dict]] = [[]]
        for word_index, word in enumerate(group):
            previous = segments[-1]
            # A large gap followed by prose signals a new text column. Keep
            # numeric table columns joined to their left-hand metric label.
            prose_follows = sum(bool(re.match(r"[A-Za-z]", item["text"])) for item in group[word_index:word_index + 4]) >= 2
            if previous and word["x0"] - previous[-1]["x1"] > 18 and (re.match(r"[A-Za-z]", word["text"]) or prose_follows):
                segments.append([])
            segments[-1].append(word)
        for segment in segments:
            result.append({
                "text": re.sub(r"(?<=\d)\.\s+(?=\d)", ".", " ".join(word["text"] for word in segment)),
                "bbox": [min(word["x0"] for word in segment), min(word["top"] for word in segment),
                         max(word["x1"] for word in segment), max(word["bottom"] for word in segment)],
            })
    return result


def parse_pdf(path: Path, document_id: str) -> list[dict]:
    """Return relevant native-text rows; IDs stay stable for identical PDF bytes.

    Page numbers are physical, one-based PDF pages, not printed page labels.
    Header and neighboring context never come from another page. Scanned-only
    documents return no candidates rather than fabricated OCR results.
    """
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    candidates = []
    relevant_pages = []
    # PDFium's text pass avoids expensive geometry on hundreds of irrelevant
    # pages. It does not identify values, use labels, or select banks/years.
    with pdfium.PdfDocument(path) as quick_pdf:
        for index in range(len(quick_pdf)):
            quick_page = quick_pdf[index]
            text_page = quick_page.get_textpage()
            try:
                plain = re.sub(r"\s+", " ", text_page.get_text_range())
                if metric_ids(plain):
                    relevant_pages.append(index)
            finally:
                text_page.close()
                quick_page.close()
    with pdfplumber.open(path) as pdf:
        for index in relevant_pages:
            page_index = index + 1
            page = pdf.pages[index]
            # Some publisher print PDFs retain the adjacent spread outside the
            # visible page and duplicate overprinted glyphs. Keep visible glyphs
            # only; otherwise evidence coordinates can lie outside the viewer.
            visible = page.crop(page.bbox).dedupe_chars(tolerance=1, extra_attrs=())
            words = visible.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
            rows = _rows(words)
            for index, row in enumerate(rows):
                matches = metric_ids(row["text"])
                if not matches:
                    continue
                # Keep continuation lines for line-broken metric headings. The bbox
                # still highlights only this candidate row, never a model-made box.
                context_indices = sorted(set(range(min(9, len(rows)))) |
                                         set(range(max(0, index - 12), min(len(rows), index + 4))))
                context = "\n".join(rows[i]["text"] for i in context_indices)
                bbox = [round(float(value), 2) for value in row["bbox"]]
                identity = f"{fingerprint}|{page_index}|{bbox}|{row['text']}"
                candidates.append({
                    "id": "c_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                    "document_id": document_id, "page": page_index, "bbox": bbox,
                    "page_width": float(page.width), "page_height": float(page.height),
                    "text": row["text"], "context": context, "metric_ids": matches,
                    "numbers": NUMBER_PATTERN.findall(row["text"]),
                })
                # Wrapped prose: make a separate, explicitly bounded block when a
                # numeric statement follows a metric heading. No cross-page joins.
                following = []
                for later in rows[index + 1:index + 9]:
                    if later["bbox"][1] - row["bbox"][3] > 35:
                        break
                    if abs(later["bbox"][0] - row["bbox"][0]) > 30:
                        continue
                    if metric_ids(later["text"]):
                        break
                    following.append(later)
                    if len(following) >= 2:
                        break
                tail_start = max(METRIC_PATTERNS[metric].search(row["text"]).end() for metric in matches)
                tail = row["text"][tail_start:]
                numeric_row = bool(re.search(r"\d+(?:\.\d+)?%|\d{1,3},\d{3}|\d+\.\d+", tail))
                if following and not numeric_row:
                    block_rows = [row, *following]
                    block_text = " ".join(item["text"] for item in block_rows)
                    if len(NUMBER_PATTERN.findall(block_text)) > len(NUMBER_PATTERN.findall(row["text"])):
                        block_bbox = [round(min(item["bbox"][0] for item in block_rows), 2), bbox[1],
                                      round(max(item["bbox"][2] for item in block_rows), 2),
                                      round(max(item["bbox"][3] for item in block_rows), 2)]
                        block_identity = f"{fingerprint}|{page_index}|{block_bbox}|{block_text}"
                        candidates.append({**candidates[-1],
                                           "id": "c_" + hashlib.sha256(block_identity.encode()).hexdigest()[:24],
                                           "bbox": block_bbox, "text": block_text,
                                           "numbers": NUMBER_PATTERN.findall(block_text)})
            # pdfplumber otherwise retains every page's character/object caches.
            page.close()
    return candidates
