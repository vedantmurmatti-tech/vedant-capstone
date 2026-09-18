"""Esmerelda's primary chat agent: Groq function-calling over real data,
via a manual multi-step tool-calling loop.

Unlike gemini_agent.py (which uses google-genai's built-in automatic
function-calling loop), Groq's chat-completions API is OpenAI-compatible
and has no automatic tool-execution feature — the perceive/act/observe
loop below is hand-rolled, per the task's explicit requirement.

perceive -> reason -> act -> observe -> iterate:
  perceive/reason — Groq reads the message plus the same four Python tool
                     descriptions (converted via groq_tools.py, reusing
                     the real callables from gemini_tools.build_tools())
                     and the real MCP tool descriptions (from a real
                     session.list_tools() call) and decides what to call.
  act              — run_tool_loop() dispatches each tool_call: Python
                      tools are the exact same closures Gemini already
                      uses, so they populate the same ToolCollector as a
                      side effect; MCP tool names are routed through the
                      real mcp_bridge.LoggingSqliteSession — its read-only
                      allow-list enforcement and call logging are reused
                      as-is, not reimplemented here.
  observe          — each tool's real return value is appended back into
                      the message list as a "tool" role message.
  iterate          — the loop repeats, capped at MAX_ITERATIONS, until
                      Groq returns a message with no tool_calls (a real
                      final answer) or the cap is hit (a safe, honest
                      reply — never an infinite loop or a crash).

Groq is never handed the live MCP ClientSession object — only the plain
JSON-schema tool definitions groq_tools.py builds from it.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

import mcp.types as mcp_types
from dotenv import load_dotenv
from groq import AsyncGroq
from sqlalchemy.orm import Session

from .followups import generate_followups
from .gemini_tools import ToolCollector, build_tools
from .groq_tools import ToolDispatch, build_dispatch, mcp_tool_defs, python_tool_defs
from .mcp_bridge import McpCallLog, sqlite_mcp_session
from .schemas import ChatResponseOut, McpCallOut

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

MAX_ITERATIONS = 5

SYSTEM_PROMPT = """You are Esmerelda, a personal academic assistant for a university student.

You have four tools that read real data synced from the student's Moodle courses:
- get_upcoming_assignments — real tracked assignments across every course, ranked by real urgency
- get_course_info — real details about one course
- get_course_documents — real indexed files for one course, or all courses
- plan_assignment_action — runs the assignment-action-planner Skill on ONE specific real
  assignment the user named, returning a source-grounded action plan (urgency,
  recommendation, and the real submission link/Moodle IDs it came from)

You also have real, read-only SQL access to the same underlying database, via MCP tools
such as read_query, list_tables, and describe_table. Use these ONLY for cross-cutting or
aggregate questions the four tools above cannot answer — e.g. "how many total documents
are indexed across every course", "which course has the most tracked resources". Do NOT
use the SQL tool for anything the four tools above already answer directly. When you do
use it, only ever run SELECT statements.

Tool choice:
- A list-style question ("what's due", "what are my priorities") -> get_upcoming_assignments.
- A question about one specific named assignment -> plan_assignment_action, not
  get_upcoming_assignments.
- A question about a course itself -> get_course_info. About files/materials -> get_course_documents.
- A cross-course aggregate/count question -> the SQL tool.

Rules you must never break:
1. Never state a specific assignment name, due date, course name, document filename, or
   number unless a tool call in this conversation actually returned it. Do not guess or
   use outside knowledge about "typical" coursework.
2. If a tool returns no results or an error, say so plainly instead of inventing an answer.
3. Prefer calling a tool over answering from general knowledge whenever the question
   concerns the student's actual courses, deadlines, or documents.
4. Only call the tool(s) that directly answer what was asked.
5. Keep answers concise and factual.
6. If the question is unrelated to courses/assignments/documents, you may answer briefly
   without a tool call, but stay focused — you are an academic assistant, not a
   general-purpose chatbot."""


class GroqUnavailableError(Exception):
    """Raised when Groq can't be reached or isn't configured. Never include the API key in this message."""


@lru_cache(maxsize=1)
def _get_client() -> AsyncGroq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise GroqUnavailableError("GROQ_API_KEY is not configured in backend/.env")
    return AsyncGroq(api_key=api_key)


def _get_model() -> str:
    model = os.environ.get("GROQ_MODEL")
    if not model:
        raise GroqUnavailableError("GROQ_MODEL is not configured in backend/.env")
    return model


async def run_tool_loop(
    create_completion: Callable[[list[dict[str, Any]]], Awaitable[Any]],
    messages: list[dict[str, Any]],
    dispatch: ToolDispatch,
    mcp_session: Any | None,
    max_iterations: int = MAX_ITERATIONS,
) -> str:
    """The manual multi-step loop, deliberately isolated from client/model
    setup so it can be exercised directly in tests with a fake
    `create_completion` and no real Groq/MCP calls at all (or a real MCP
    session with a fake `create_completion`, for the MCP-routing tests)."""
    for _ in range(max_iterations):
        response = await create_completion(messages)
        choice = response.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None)

        if not tool_calls:
            return choice.content or "I don't have anything to say about that."

        messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                result: Any = {"error": f"Malformed tool arguments from model: {exc}"}
            else:
                kind = dispatch.kind_of(name)
                if kind == "python":
                    try:
                        result = dispatch.python_tools[name](**args)
                    except Exception as exc:  # a real tool bug shouldn't crash the whole turn
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                elif kind == "mcp":
                    if mcp_session is None:
                        result = {"error": "The SQL tool is not available this turn."}
                    else:
                        try:
                            mcp_result = await mcp_session.call_tool(name, args)
                            text = " ".join(
                                c.text for c in mcp_result.content if isinstance(c, mcp_types.TextContent)
                            )
                            result = {"result": text, "isError": bool(mcp_result.isError)}
                        except PermissionError as exc:
                            result = {"error": str(exc)}
                else:
                    result = {"error": f"Unknown tool '{name}'"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return "I wasn't able to finish that within a safe number of steps — try rephrasing your question."


async def handle_chat_message_groq(db: Session, message: str) -> ChatResponseOut:
    try:
        client = _get_client()
        model = _get_model()
    except GroqUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover - defensive, e.g. malformed key
        raise GroqUnavailableError(f"could not initialize the Groq client ({type(exc).__name__})") from exc

    collector = ToolCollector()
    call_log = McpCallLog()
    python_tools = build_tools(db, collector)

    async def create_completion(messages: list[dict[str, Any]], tool_defs: list[dict[str, Any]]):
        try:
            return await client.chat.completions.create(
                model=model, messages=messages, tools=tool_defs, tool_choice="auto"
            )
        except Exception as exc:
            raise GroqUnavailableError(f"{type(exc).__name__}: {exc}") from exc

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]

    try:
        async with sqlite_mcp_session(call_log) as session:
            mcp_tools = (await session.list_tools()).tools
            dispatch = build_dispatch(python_tools, mcp_tools)
            tool_defs = python_tool_defs(python_tools) + mcp_tool_defs(mcp_tools)
            reply_text = await run_tool_loop(
                lambda msgs: create_completion(msgs, tool_defs), messages, dispatch, session
            )
    except GroqUnavailableError:
        raise  # a real Groq failure — let the orchestrator try Gemini next
    except Exception:
        # The MCP subprocess/session itself failed to start or connect — degrade to the
        # four Python tools alone for this turn. mcpCalls correctly stays empty.
        dispatch = build_dispatch(python_tools, [])
        tool_defs = python_tool_defs(python_tools)
        reply_text = await run_tool_loop(
            lambda msgs: create_completion(msgs, tool_defs), messages, dispatch, None
        )

    assignments = list(collector.assignments.values())
    courses = list(collector.courses.values())
    documents = list(collector.documents.values())

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
    )
