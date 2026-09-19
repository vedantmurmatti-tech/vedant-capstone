"""Extracts plain, searchable text from a downloaded document file —
the Knowledge Base's indexing step. Called once per document, immediately
after moodle/document_downloader.py finishes downloading it (see
process_resource()); the result is stored in Document.extracted_text and
never re-read from disk at chat time — api/document_retrieval.py searches
that column directly.

Supports PDF (pypdf), DOCX (python-docx), and PPTX (python-pptx) — the
three real, common Moodle resource file types with a well-established,
lightweight, pure-Python-ish extraction library. XLSX/ZIP and any other
type return None (not an error) — a resource being ineligible for text
extraction doesn't make it any less real a downloaded Document; it's just
not searchable content the same way.

One file at a time, always — this module is only ever called from
moodle/document_downloader.py's per-resource loop (already-established,
already-verified-memory-safe: see BUILD_LOG.md's memory-regression entry),
never batched across multiple documents.
"""

import logging
from pathlib import Path

logger = logging.getLogger("esmerelda.moodle_sync")

# Caps how much text is stored per document — a few hundred KB of plain
# text is already far more than any reasonable chat-context chunk will
# ever need (see api/document_retrieval.py), and this bounds both the
# database row size and this module's own peak memory (extraction
# libraries build their output as one in-memory string regardless; this
# cap limits what's kept afterward, not what's parsed).
_MAX_EXTRACTED_CHARS = 300_000

_EXTRACTABLE_FILE_TYPES = {"PDF", "Word Document", "PowerPoint"}

# Maps a real downloaded file's own extension to an extractable type.
# Deliberately NOT the same thing as moodle/sync_service.py's
# resource_type classification: a generic /mod/resource/view.php wrapper
# page (Moodle activity type "Resource" — the single most common shape a
# real Moodle PDF/DOCX/PPTX is actually linked as) resolves, once
# downloaded, to a real file with a real extension that has nothing to do
# with that Moodle-side label. Extraction eligibility is decided from
# the real, downloaded file's own extension — the one thing that's
# actually true about what's on disk — not from Moodle's activity-type
# label, which is "Resource" for the overwhelming majority of real
# documents regardless of what kind of file they actually are.
_EXTENSION_TO_TYPE = {
    ".pdf": "PDF",
    ".doc": "Word Document",
    ".docx": "Word Document",
    ".ppt": "PowerPoint",
    ".pptx": "PowerPoint",
}


def is_extractable(file_type: str | None) -> bool:
    return file_type in _EXTRACTABLE_FILE_TYPES


def guess_extractable_type(filename: str) -> str | None:
    """Derives an extractable type from a real downloaded file's own
    extension — see the module-level note above for why this, not
    Moodle's resource_type label, is what extract_text() should be
    driven by."""
    suffix = Path(filename).suffix.lower()
    return _EXTENSION_TO_TYPE.get(suffix)


def extract_text(file_path: Path, file_type: str | None) -> str | None:
    """Returns the extracted plain text, or None if the type isn't
    supported or extraction failed for any reason (logged, never raised —
    a document that can't be indexed is still a real, valid download).
    `file_type` should be derived from the real downloaded filename via
    guess_extractable_type() — see that function's docstring."""
    if not is_extractable(file_type):
        return None
    try:
        if file_type == "PDF":
            text = _extract_pdf(file_path)
        elif file_type == "Word Document":
            text = _extract_docx(file_path)
        elif file_type == "PowerPoint":
            text = _extract_pptx(file_path)
        else:
            return None
    except Exception as exc:
        logger.warning("[SYNC DEBUG] text extraction failed for %s (%s): %s: %s", file_path.name, file_type, type(exc).__name__, exc)
        return None

    text = (text or "").strip()
    if not text:
        return None
    return text[:_MAX_EXTRACTED_CHARS]


def _extract_pdf(file_path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(file_path))
    parts = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text:
            parts.append(page_text)
    return "\n".join(parts)


def _extract_docx(file_path: Path) -> str:
    import docx

    document = docx.Document(str(file_path))
    parts = [p.text for p in document.paragraphs if p.text]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text:
                    parts.append(cell.text)
    return "\n".join(parts)


def _extract_pptx(file_path: Path) -> str:
    from pptx import Presentation

    presentation = Presentation(str(file_path))
    parts = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in paragraph.runs)
                    if text:
                        parts.append(text)
    return "\n".join(parts)
