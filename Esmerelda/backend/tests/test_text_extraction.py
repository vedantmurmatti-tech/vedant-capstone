"""Tests for storage/text_extraction.py, specifically the real production
warning this was added to investigate:

    WARNING pypdf._cmap: fontTools is required to fully parse the
    encoding of a CFF Type1 font in font dictionary ..., but is not
    installed. Consider installing fontTools if you encounter encoding
    problems.

Confirmed by reading pypdf's own source (venv/Lib/site-packages/pypdf/
_cmap.py's _parse_to_unicode(), venv/Lib/site-packages/pypdf/_font.py's
HAS_FONTTOOLS flag) before writing anything here — not assumed. The
warning fires specifically for a simple /Type1 font with no /ToUnicode
entry whose /FontDescriptor has an embedded /FontFile3 stream marked
/Subtype /Type1C (a CFF-flavored Type1 font), when the `fontTools`
package isn't importable. `pypdf`, `python-docx`, and `python-pptx` (and
their own dependencies — lxml, Pillow, XlsxWriter, typing-extensions) do
NOT require fontTools themselves — confirmed directly via `pip show` —
so it was genuinely absent from any environment built strictly from
backend/requirements.txt before this fix, matching the real, repeated
production warning exactly, not a one-off fluke.

Reproduced here by constructing the exact minimal object shape in memory
(no fake/invalid PDF file round-trip needed — pypdf's `_parse_to_unicode`
is called directly, with real pypdf generic objects, exactly the shape
its own source code expects) and toggling `pypdf._font.HAS_FONTTOOLS`
(note: NOT `pypdf._cmap.HAS_FONTTOOLS` — `_parse_to_unicode` does a
fresh `from ._font import HAS_FONTTOOLS` on every call, confirmed by
reading the source; patching the wrong module's attribute silently never
triggers the code path at all, which is exactly the mistake this test
avoids making again).

Run with:
    venv/Scripts/python.exe tests/test_text_extraction.py
"""

import logging
import sys
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf._font as pypdf_font_module
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from storage.text_extraction import extract_text, guess_extractable_type, is_extractable

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        print(f"[PASS] {label}")
        passed += 1
    else:
        print(f"[FAIL] {label}")
        failed += 1


def _build_type1c_font_dict() -> DictionaryObject:
    """A real pypdf DictionaryObject with exactly the shape that fires
    the fontTools warning: a simple /Type1 font, no /ToUnicode, whose
    /FontDescriptor has an embedded /FontFile3 stream marked
    /Subtype /Type1C. The embedded font bytes themselves don't need to
    be real, valid CFF data — pypdf's HAS_FONTTOOLS check happens before
    it ever tries to actually parse the font program (confirmed by
    reading the source, and directly by this test: the "with fontTools"
    branch below doesn't raise on this fake data either — pypdf's own
    CFF parser must itself tolerate/short-circuit on unparseable data
    rather than crash, which is exactly the "does not crash the sync"
    property this test is checking for)."""
    font_file_stream = StreamObject()
    font_file_stream.set_data(b"FAKE-CFF-DATA-NOT-A-REAL-FONT-PROGRAM")
    font_file_stream[NameObject("/Subtype")] = NameObject("/Type1C")

    font_descriptor = DictionaryObject()
    font_descriptor[NameObject("/FontFile3")] = font_file_stream

    ft = DictionaryObject()
    ft[NameObject("/Subtype")] = NameObject("/Type1")
    ft[NameObject("/FontDescriptor")] = font_descriptor
    return ft


def _capture_cmap_warnings(fn):
    """Runs fn() with pypdf's _cmap logger's output captured, returns
    (result, captured_log_text)."""
    logger = logging.getLogger("pypdf._cmap")
    original_level = logger.level
    original_propagate = logger.propagate
    handler = logging.StreamHandler(StringIO())
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        result = fn()
        handler.flush()
        return result, handler.stream.getvalue()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        logger.propagate = original_propagate


# 1. Without fontTools: the exact real warning fires, and pypdf still
# returns an empty (not crashed) character map — proving this degrades
# extraction quality for glyphs that need it, rather than failing outright.
import pypdf._cmap as pypdf_cmap_module  # noqa: E402

original_has_fonttools = pypdf_font_module.HAS_FONTTOOLS
try:
    pypdf_font_module.HAS_FONTTOOLS = False
    ft = _build_type1c_font_dict()
    (map_dict, int_entry), log_text = _capture_cmap_warnings(lambda: pypdf_cmap_module._parse_to_unicode(ft))
    check(
        "1. Without fontTools, the exact real production warning fires (word-for-word) "
        "when parsing a CFF Type1 font's encoding",
        "fontTools is required to fully parse the encoding of a CFF Type1 font" in log_text,
    )
    check(
        "1b. Without fontTools, pypdf returns an empty character map rather than raising — "
        "confirms this degrades extraction quality, it does not crash",
        map_dict == {} and int_entry == [],
    )
finally:
    pypdf_font_module.HAS_FONTTOOLS = original_has_fonttools

# 2. With fontTools (as it now genuinely is, once installed per
# backend/requirements.txt): no warning, and no crash either, even
# against fake/invalid font-program bytes.
try:
    pypdf_font_module.HAS_FONTTOOLS = True
    ft2 = _build_type1c_font_dict()
    (map_dict2, int_entry2), log_text2 = _capture_cmap_warnings(lambda: pypdf_cmap_module._parse_to_unicode(ft2))
    check("2. With fontTools installed, the warning does not fire for the same font", log_text2 == "")
    check(
        "2b. With fontTools installed, parsing fake/invalid CFF font-program bytes still does not "
        "crash extraction (pypdf's own CFF parser tolerates it)",
        isinstance(map_dict2, dict) and isinstance(int_entry2, list),
    )
finally:
    pypdf_font_module.HAS_FONTTOOLS = original_has_fonttools


# 3. End-to-end, through this project's own real extract_text() function
# (not just pypdf internals) — a PDF containing this exact font shape,
# read from a real, real-world-shaped file (not just in-memory objects),
# neither crashes storage/text_extraction.extract_text() nor prevents it
# from returning whatever real text WAS extractable.
from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import ArrayObject, IndirectObject, NumberObject  # noqa: E402

import tempfile  # noqa: E402


def _build_type1c_pdf(tmp_path: Path) -> Path:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)

    font_file_stream = StreamObject()
    font_file_stream.set_data(b"FAKE-CFF-DATA-NOT-A-REAL-FONT-PROGRAM")
    font_file_stream[NameObject("/Subtype")] = NameObject("/Type1C")
    font_file_ref = writer._add_object(font_file_stream)

    font_descriptor = DictionaryObject()
    font_descriptor[NameObject("/Type")] = NameObject("/FontDescriptor")
    font_descriptor[NameObject("/FontFile3")] = font_file_ref
    descriptor_ref = writer._add_object(font_descriptor)

    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/FakeType1CFont")
    font_dict[NameObject("/FontDescriptor")] = descriptor_ref
    font_dict[NameObject("/FirstChar")] = NumberObject(32)
    font_dict[NameObject("/LastChar")] = NumberObject(255)
    font_dict[NameObject("/Widths")] = ArrayObject([NumberObject(500)] * (255 - 32 + 1))
    font_ref = writer._add_object(font_dict)

    resources = DictionaryObject()
    font_resources = DictionaryObject()
    font_resources[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = font_resources
    page[NameObject("/Resources")] = resources

    content = b"BT /F1 12 Tf 10 100 Td (Course Outline Deliverables) Tj ET"
    content_stream = StreamObject()
    content_stream.set_data(content)
    content_ref = writer._add_object(content_stream)
    page[NameObject("/Contents")] = content_ref

    pdf_path = tmp_path / "type1c_test.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)
    return pdf_path


with tempfile.TemporaryDirectory(prefix="esmerelda_fonttools_test_") as tmp_dir:
    pdf_path = _build_type1c_pdf(Path(tmp_dir))

    try:
        pypdf_font_module.HAS_FONTTOOLS = False
        (extracted, log_text3) = _capture_cmap_warnings(lambda: extract_text(pdf_path, "PDF"))
        check(
            "3. The real project extract_text() function does not crash on a Type1C-embedded PDF, "
            "with or without fontTools, and the exact real warning is reproduced through it",
            extracted is not None and "fontTools is required" in log_text3,
        )
        check(
            "3b. Text that doesn't actually need the CFF-specific encoding (plain ASCII here) "
            "still extracts correctly even when the warning fires",
            extracted is not None and "Course Outline Deliverables" in extracted,
        )
    finally:
        pypdf_font_module.HAS_FONTTOOLS = original_has_fonttools

    try:
        pypdf_font_module.HAS_FONTTOOLS = True
        (extracted2, log_text4) = _capture_cmap_warnings(lambda: extract_text(pdf_path, "PDF"))
        check("4. With fontTools genuinely present, extraction still succeeds and the warning does not fire", extracted2 is not None and log_text4 == "")
    finally:
        pypdf_font_module.HAS_FONTTOOLS = original_has_fonttools


# 5. Regression: ordinary, non-Type1C-embedded extraction (the common
# case) is completely unaffected — reusing the real base64-embedded PDF
# fixture from tests/test_sync_service.py so this doesn't duplicate a
# second copy of real PDF bytes in the repo.
import base64  # noqa: E402

_ORDINARY_PDF_BYTES = base64.b64decode(
    "JVBERi0xLjMKJenr8b8KMSAwIG9iago8PAovQ291bnQgMQovS2lkcyBbMyAwIFJdCi9NZWRpYUJveCBb"
    "MCAwIDU5NS4yOCA4NDEuODldCi9UeXBlIC9QYWdlcwo+PgplbmRvYmoKMiAwIG9iago8PAovT3BlbkFj"
    "dGlvbiBbMyAwIFIgL0ZpdEggbnVsbF0KL1BhZ2VMYXlvdXQgL09uZUNvbHVtbgovUGFnZXMgMSAwIFIK"
    "L1R5cGUgL0NhdGFsb2cKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRlbnRzIDQgMCBSCi9QYXJlbnQg"
    "MSAwIFIKL1Jlc291cmNlcyA2IDAgUgovVHlwZSAvUGFnZQo+PgplbmRvYmoKNCAwIG9iago8PAovRmls"
    "dGVyIC9GbGF0ZURlY29kZQovTGVuZ3RoIDEzMwo+PgpzdHJlYW0KeJwdzbEKwjAUBdC9X3FHhRKbSok6"
    "Kjq4uOQHIr3SSEnqy6vi30tdz3JaXKvGdA6f6uixuVjY1jQN/ANnv9DWGruD23fGOfgeKz8QpzxLIW6z"
    "jjERwtcchQU6CImeY3xTwn1kOSBAOGXRGgGTZM36nVgjpP4PLEwaNOZk1vDPZf0B76ItSAplbmRzdHJl"
    "YW0KZW5kb2JqCjUgMCBvYmoKPDwKL0Jhc2VGb250IC9IZWx2ZXRpY2EKL0VuY29kaW5nIC9XaW5BbnNp"
    "RW5jb2RpbmcKL1N1YnR5cGUgL1R5cGUxCi9UeXBlIC9Gb250Cj4+CmVuZG9iago2IDAgb2JqCjw8Ci9G"
    "b250IDw8L0YxIDUgMCBSPj4KL1Byb2NTZXQgWy9QREYgL1RleHQgL0ltYWdlQiAvSW1hZ2VDIC9JbWFn"
    "ZUldCj4+CmVuZG9iago3IDAgb2JqCjw8Ci9DcmVhdGlvbkRhdGUgKEQ6MjAyNjA5MTkxMTU3MjRaKQo+"
    "PgplbmRvYmoKeHJlZgowIDgKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDE1IDAwMDAwIG4gCjAw"
    "MDAwMDAxMDIgMDAwMDAgbiAKMDAwMDAwMDIwNSAwMDAwMCBuIAowMDAwMDAwMjg1IDAwMDAwIG4gCjAw"
    "MDAwMDA0OTAgMDAwMDAgbiAKMDAwMDAwMDU4NyAwMDAwMCBuIAowMDAwMDAwNjc0IDAwMDAwIG4gCnRy"
    "YWlsZXIKPDwKL1NpemUgOAovUm9vdCAyIDAgUgovSW5mbyA3IDAgUgovSUQgWzwzNDNBNzg4NjEyMkM3"
    "QTg1ODRCRUIzOUE4MDQwNUMyMD48MzQzQTc4ODYxMjJDN0E4NTg0QkVCMzlBODA0MDVDMjA+XQo+Pgpz"
    "dGFydHhyZWYKNzI5CiUlRU9GCg=="
)

with tempfile.TemporaryDirectory(prefix="esmerelda_fonttools_ordinary_") as tmp_dir:
    ordinary_pdf_path = Path(tmp_dir) / "ordinary.pdf"
    ordinary_pdf_path.write_bytes(_ORDINARY_PDF_BYTES)
    (ordinary_extracted, ordinary_log) = _capture_cmap_warnings(lambda: extract_text(ordinary_pdf_path, "PDF"))
    check(
        "5. An ordinary PDF using a standard (non-embedded) font extracts correctly with no "
        "fontTools warning at all — the fix doesn't change behavior for the common case",
        ordinary_extracted == "The Course Outline requires three deliverables: a report, a prototype, and a presentation."
        and ordinary_log == "",
    )

# 6. guess_extractable_type()/is_extractable() — unchanged behavior, quick
# regression check since this module is directly exercised above.
check("6. .pdf is recognized as extractable", guess_extractable_type("Lecture Notes.pdf") == "PDF" and is_extractable("PDF"))
check("6b. .zip is not recognized as extractable", guess_extractable_type("Archive.zip") is None)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
