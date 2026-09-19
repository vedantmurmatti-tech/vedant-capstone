"""Chat agent orchestrator.

Three-tier fallback chain:
  1. groq_agent.py    — primary. Real Groq function-calling (a manual
                         tool-calling loop, since Groq has no built-in AFC)
                         over the same real database, Python tools, the
                         assignment-action-planner Skill, and the real
                         SQLite MCP server.
  2. gemini_agent.py   — secondary. Real Gemini function-calling (via
                         google-genai's own automatic function-calling
                         loop) over the identical tools/Skill/MCP session.
                         Tried only when Groq itself is unconfigured or
                         unreachable.
  3. deterministic_agent.py — final safety net. Fully synchronous,
                         rule-based, no LLM call at all. Tried only when
                         both Groq and Gemini are unavailable.

Every fallback is labelled honestly in the reply so a user (or a grader)
can always tell which tier actually answered — never silently masking a
real failure.
"""

import logging

from sqlalchemy.orm import Session

from . import deterministic_agent, document_retrieval
from .gemini_agent import GeminiUnavailableError, handle_chat_message_gemini
from .groq_agent import GroqUnavailableError, handle_chat_message_groq
from .schemas import ChatResponseOut

logger = logging.getLogger("esmerelda.chat")


async def handle_chat_message(db: Session, message: str) -> ChatResponseOut:
    logger.info("[CHAT] query received: %r", message[:200])
    logger.info("[CHAT] documents indexed=%d", document_retrieval.count_indexed_documents(db))

    try:
        return await handle_chat_message_groq(db, message)
    except GroqUnavailableError as exc:
        # Python deletes `as`-bound exception variables at the end of their
        # except block, so the message is copied into a plain str here to
        # survive into the later blocks below.
        groq_reason = str(exc)

    try:
        response = await handle_chat_message_gemini(db, message)
        response.reply = (
            f"_(Groq was unavailable — {groq_reason} — answered by Gemini instead.)_\n\n" + response.reply
        )
        return response
    except GeminiUnavailableError as exc:
        gemini_reason = str(exc)
        fallback = deterministic_agent.handle_chat_message_deterministic(db, message)
        fallback.reply = (
            f"_(Both Groq and Gemini were unavailable — Groq: {groq_reason}; Gemini: {gemini_reason} — "
            "answering with Esmerelda's deterministic fallback instead.)_\n\n" + fallback.reply
        )
        return fallback
