"""Presentation-only helpers that derive display fields the database doesn't
store directly, instead of inventing new columns or fake data.

`Course.short_name` and `Document.file_type` exist as columns but are never
populated by course_radar.py / document_downloader.py — both are always
None in practice. Rather than leaving the frontend without them, this module
derives readable values from data that *is* real (the course name string,
the document filename).
"""

import re

# Matches a leading course code (e.g. "BUAN301") and a term token like
# "2026/27S1" anywhere in the raw Moodle course name.
_COURSE_CODE_RE = re.compile(r"^([A-Z]{2,6}\d{2,4})")
_TERM_RE = re.compile(r"(\d{4}/\d{2}S\d)")


def derive_short_name(name: str) -> str | None:
    code_match = _COURSE_CODE_RE.match(name)
    term_match = _TERM_RE.search(name)

    if code_match and term_match:
        return f"{code_match.group(1)} · {term_match.group(1)}"
    if code_match:
        return code_match.group(1)
    return None


def derive_file_type(filename: str) -> str | None:
    if "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1].lower() or None
