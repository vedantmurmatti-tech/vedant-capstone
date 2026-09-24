"""The Knowledge Base's retrieval step — chat-time search over already-
extracted document text (storage/text_extraction.py, populated once at
download time by moodle/document_downloader.py).

No vector database, no embeddings model — the existing SQLite database
already holds the indexed representation (Document.extracted_text), and a
plain keyword-overlap search over it is the smallest thing that actually
works for this project's real scale (a few dozen documents, a few hundred
KB of text each at most). Matches this project's own existing precedent:
api/skills/assignment_action_planner's search_local_evidence() already
does real, bounded, keyword-based text search over local sources instead
of anything more elaborate — this module follows the same approach for
documents specifically.

Two things this module is deliberately careful about, per the real
constraint that drove the Render memory fix elsewhere in this project
(see BUILD_LOG.md):
  1. Candidate documents are pre-filtered in SQL (a `LIKE` per
     significant query word) before any text is pulled into Python at
     all — a query that matches nothing does not load anything.
  2. Even when there are matches, only the matching subset's
     `extracted_text` is loaded (bounded further by MAX_CANDIDATE_DOCS)
     — never every indexed document's full text on every chat turn,
     regardless of how many documents exist.
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from storage.models import Course, Document, Resource

logger = logging.getLogger("esmerelda.chat")

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "into", "one", "two",
    "to", "in", "on", "your", "this", "that", "it", "is", "are", "what",
    "does", "do", "say", "about", "me", "tell", "can", "you", "please",
}

_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100
_MAX_CANDIDATE_DOCS = 15  # SQL-filtered candidates pulled into Python for one query
_MAX_CHUNKS_RETURNED = 5


def _significant_words(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _chunk_text(text: str) -> list[str]:
    if len(text) <= _CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + _CHUNK_SIZE])
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


@dataclass
class RetrievedChunk:
    document_id: int
    document_name: str
    course_name: str | None
    chunk_text: str
    score: int


def count_indexed_documents(db: Session, user_id: int) -> int:
    return (
        db.query(Document)
        .filter(Document.extracted_text.isnot(None), Document.user_id == user_id)
        .count()
    )


def search_documents(
    db: Session, query: str, user_id: int, max_chunks: int = _MAX_CHUNKS_RETURNED
) -> list[RetrievedChunk]:
    """Real keyword-overlap search over already-extracted document text.
    Returns [] (not an error) when nothing is indexed yet or nothing
    matches — the caller (api/gemini_tools.py's search_document_content)
    is responsible for saying so honestly rather than fabricating.
    user_id-scoped so one user's chat can never retrieve excerpts from
    another user's documents."""
    words = _significant_words(query)
    if not words:
        return []

    # SQL pre-filter — only documents whose extracted_text contains at
    # least one significant query word are ever loaded into Python.
    candidates = (
        db.query(Document, Resource, Course)
        .filter(Document.extracted_text.isnot(None), Document.user_id == user_id)
        .outerjoin(Resource, Document.resource_id == Resource.id)
        .outerjoin(Course, Resource.course_id == Course.id)
        .filter(or_(*[Document.extracted_text.ilike(f"%{w}%") for w in words]))
        .limit(_MAX_CANDIDATE_DOCS)
        .all()
    )

    scored_chunks: list[RetrievedChunk] = []
    for document, _resource, course in candidates:
        text = document.extracted_text or ""
        for chunk in _chunk_text(text):
            lowered = chunk.lower()
            score = sum(1 for w in words if w in lowered)
            if score > 0:
                scored_chunks.append(
                    RetrievedChunk(
                        document_id=document.id,
                        document_name=document.name,
                        course_name=course.name if course else None,
                        chunk_text=chunk.strip(),
                        score=score,
                    )
                )

    scored_chunks.sort(key=lambda c: c.score, reverse=True)
    return scored_chunks[:max_chunks]
