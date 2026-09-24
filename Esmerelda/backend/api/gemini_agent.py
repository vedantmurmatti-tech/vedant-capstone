"""Esmerelda's primary chat agent: Gemini function-calling over real data,
plus one real MCP server (mcp-server-sqlite) for cross-cutting SQL.

perceive -> reason -> act -> observe -> iterate, implemented via the
`google-genai` SDK's automatic function-calling loop:
  perceive/reason — Gemini reads the user's message, the four Python tool
                     descriptions (see gemini_tools.py), and the real
                     tool list the MCP session reports (see mcp_bridge.py),
                     and decides which tool(s), if any, answer it.
  act              — the SDK calls the real Python tool function directly,
                      OR — for the SQL tool — calls
                      `LoggingSqliteSession.call_tool()`, which forwards a
                      real MCP `tools/call` request to the
                      `mcp-server-sqlite` subprocess over stdio.
  observe          — the tool's real return value (or the real MCP
                      `CallToolResult`) is fed back to the model as a
                      function_response turn.
  iterate          — the model can call another tool before producing a
                      final answer (the SDK's built-in agentic loop, same
                      as before MCP was added).

MCP tool calls require the async client (`client.aio`) — `ClientSession`'s
`call_tool` is `async def` — so this module, `chat_agent.py`, and the
`/api/chat` route are all async now. The four existing Python tools and the
assignment-action-planner Skill are untouched; they're still passed into
the same `tools=[...]` list, just alongside the MCP session this time.

The system instruction is the only thing standing between this and a model
that free-associates about assignments — it explicitly forbids stating any
assignment/course/document fact that didn't come from a real tool or MCP
call.
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from .followups import generate_followups
from .gemini_tools import ToolCollector, build_tools
from .mcp_bridge import McpCallLog, sqlite_mcp_session
from .response_mode import RESPONSE_MODE_SYSTEM_PROMPT_ADDITION, build_visual_context, extract_response_mode
from .schemas import ChatResponseOut, McpCallOut

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

MODEL = "gemini-3.6-flash"

SYSTEM_INSTRUCTION = """You are Esmerelda, a personal academic assistant for a university student.

You have four tools that read real data synced from the student's Moodle courses:
- get_upcoming_assignments — real tracked assignments across every course, ranked by real urgency
- get_course_info — real details about one course
- get_course_documents — real indexed files for one course, or all courses
- plan_assignment_action — runs the assignment-action-planner Skill on ONE specific real
  assignment the user named, returning a source-grounded action plan (urgency,
  recommendation, and the real submission link/Moodle IDs it came from)

You also have real, read-only SQL access to the same underlying database, via three
MCP tools: read_query, list_tables, describe_table. Use these ONLY for cross-cutting or
aggregate questions the four tools above cannot answer — e.g. "how many total documents
are indexed across every course", "which course has the most tracked resources", "how
many assignments have no due date recorded". Do NOT use read_query for anything the four
tools above already answer directly (a single course, a single assignment, a simple list
of assignments/documents) — prefer those, since their answers are already formatted and
grounded. When you do use read_query, only ever run SELECT statements.

Tool choice:
- A list-style question ("what's due", "what are my priorities") -> get_upcoming_assignments.
- A question about one specific named assignment ("what should I do about the MVP",
  "help me plan Assessment 2") -> plan_assignment_action, not get_upcoming_assignments.
- A question about a course itself -> get_course_info. A question about files/materials
  for a course -> get_course_documents.
- A cross-course aggregate/count/"which course has the most X" question -> read_query
  (optionally list_tables/describe_table first if you need to check column names).

Rules you must never break:
1. Never state a specific assignment name, due date, course name, document filename, or
   number unless a tool or SQL call in this conversation actually returned it. Do not
   guess or use outside knowledge about "typical" coursework.
2. If a tool or query returns no results or an error, say so plainly instead of inventing
   an answer.
3. Prefer calling a tool over answering from general knowledge whenever the question
   concerns the student's actual courses, deadlines, or documents.
4. Only call the tool(s) that directly answer what was asked. Do not call
   get_upcoming_assignments "just in case" when the user asked about a specific course or
   assignment that another tool already reported as not found — report the not-found
   result instead of searching elsewhere.
5. Keep answers concise and factual.
6. If the question is unrelated to courses/assignments/documents, you may answer briefly
   without a tool call, but stay focused — you are an academic assistant, not a
   general-purpose chatbot.""" + RESPONSE_MODE_SYSTEM_PROMPT_ADDITION


class GeminiUnavailableError(Exception):
    """Raised when Gemini can't be reached or isn't configured. Never include the API key in this message."""


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiUnavailableError("GEMINI_API_KEY is not configured in backend/.env")
    return genai.Client(api_key=api_key)


# Same defensive cap as groq_agent.py's _MAX_HISTORY_MESSAGES — independent
# of whatever the client already trimmed to.
_MAX_HISTORY_MESSAGES = 12


async def handle_chat_message_gemini(
    db: Session, message: str, user_id: int, history: list[dict[str, str]] | None = None
) -> ChatResponseOut:
    try:
        client = _get_client()
    except GeminiUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover - defensive, e.g. malformed key
        raise GeminiUnavailableError(f"could not initialize the Gemini client ({type(exc).__name__})") from exc

    collector = ToolCollector()
    call_log = McpCallLog()
    python_tools = build_tools(db, collector, user_id)

    async def run_chat(tools: list) -> str:
        try:
            # Gemini's own history shape uses "model" (not "assistant") for
            # the AI's turns — translated here, at the boundary, from the
            # shared {"role": "user"|"assistant", "content": ...} shape
            # chat_agent.py passes to every tier alike.
            gemini_history = [
                {"role": "model" if turn["role"] == "assistant" else "user", "parts": [{"text": turn["content"]}]}
                for turn in (history or [])[-_MAX_HISTORY_MESSAGES:]
            ]
            chat = client.aio.chats.create(
                model=MODEL,
                config=types.GenerateContentConfig(tools=tools, system_instruction=SYSTEM_INSTRUCTION),
                history=gemini_history or None,
            )
            response = await chat.send_message(message)
        except Exception as exc:
            raise GeminiUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        return response.text or "I don't have anything to say about that."

    try:
        async with sqlite_mcp_session(call_log, user_id) as sqlite_session:
            reply_text = await run_chat([*python_tools, sqlite_session])
    except GeminiUnavailableError:
        raise  # a real Gemini failure — let the orchestrator fall back to the deterministic agent
    except Exception:
        # The MCP subprocess/session itself failed to start or connect — degrade to the
        # four Python tools alone for this turn rather than failing the whole request.
        # mcpCalls will correctly stay empty since no real MCP call happened.
        reply_text = await run_chat(python_tools)

    assignments = list(collector.assignments.values())
    courses = list(collector.courses.values())
    documents = list(collector.documents.values())

    reply_text, response_mode = extract_response_mode(reply_text)
    visual_context = build_visual_context(assignments, courses, documents) if response_mode == "visual" else None

    return ChatResponseOut(
        reply=reply_text,
        assignments=assignments,
        courses=courses,
        documents=documents,
        sources=collector.sources,
        followUps=generate_followups(
            user_message=message,
            reply_text=reply_text,
            assignments=assignments,
            courses=courses,
            documents=documents,
            used_mcp=bool(call_log.calls),
        ),
        mcpCalls=[
            McpCallOut(server=c.server, tool=c.tool, arguments=c.arguments, isError=c.is_error)
            for c in call_log.calls
        ],
        responseMode=response_mode,
        visualContext=visual_context,
    )
