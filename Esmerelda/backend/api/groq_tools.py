"""Converts Esmerelda's existing tools into the JSON-schema shape Groq's
chat-completions API requires (OpenAI-compatible `tools=[{"type":
"function", "function": {name, description, parameters}}]`).

No tool logic lives here. Two real sources are converted, not reimplemented:
  - The four Python tools from gemini_tools.build_tools() — hand-written
    schemas matching their real signatures, since plain Python callables
    don't carry a JSON Schema the way `google-genai`'s automatic function
    calling infers one from type hints.
  - The MCP server's own tools, from a real `session.list_tools()` call —
    each `mcp.types.Tool` already carries `.name`, `.description`, and
    `.inputSchema` (a real JSON Schema dict), so conversion is direct, not
    guessed, and always in sync with mcp_bridge.py's read-only allow-list.

Groq is never handed a live session object — only these plain dicts. Tool
*execution* is dispatched separately in groq_agent.py, via the
`ToolDispatch` this module also builds, which maps each tool name to
either the real Python callable or a marker that it should be routed
through the real MCP session.
"""

from dataclasses import dataclass
from typing import Any, Callable, Literal, Union

import mcp.types as mcp_types

GroqToolDef = dict[str, Any]

# Hand-written to match the real signatures in gemini_tools.build_tools().
# Kept here (not generated) so a signature change there is a visible,
# reviewable diff here too, rather than silently drifting.
_PYTHON_TOOL_SCHEMAS: dict[str, GroqToolDef] = {
    "get_upcoming_assignments": {
        "type": "function",
        "function": {
            "name": "get_upcoming_assignments",
            "description": (
                "Look up the student's real tracked assignments across every course, ranked by "
                "real urgency (overdue, due soon, or upcoming). Use this for any question about "
                "deadlines, what's due, or priorities. Takes no arguments."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "get_course_info": {
        "type": "function",
        "function": {
            "name": "get_course_info",
            "description": (
                'Look up real information about one specific course by name or course code '
                '(e.g. "Tangible Interfaces" or "DESG322"). Returns the course\'s full name, '
                "short code, description, and how many assignments/resources are tracked for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_query": {
                        "type": "string",
                        "description": "The course name or code as mentioned by the user.",
                    }
                },
                "required": ["course_query"],
            },
        },
    },
    "get_course_documents": {
        "type": "function",
        "function": {
            "name": "get_course_documents",
            "description": (
                "Look up real indexed documents (files downloaded from Moodle) for a specific "
                "course, or pass an empty string to get the most recently indexed documents "
                "across all courses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "course_query": {
                        "type": "string",
                        "description": 'The course name or code, or "" for all courses.',
                    }
                },
                "required": ["course_query"],
            },
        },
    },
    "plan_assignment_action": {
        "type": "function",
        "function": {
            "name": "plan_assignment_action",
            "description": (
                "Run the assignment-action-planner Skill on ONE specific real assignment named "
                'or described by the user (e.g. "the MVP assignment" or "Assessment 2"). Use '
                "this — not get_upcoming_assignments — when the user asks what to do about a "
                "single named assignment. Breaks the assignment into its explicit requirements "
                "and returns a status (completed/incomplete/unverified), a concrete next action, "
                "an expected deliverable, how to verify it, and real evidence for each one — plus "
                "the real submission link/Moodle IDs and anything Esmerelda couldn't determine."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "assignment_query": {
                        "type": "string",
                        "description": "The assignment name or a distinctive phrase from it, as the user mentioned it.",
                    }
                },
                "required": ["assignment_query"],
            },
        },
    },
}


def python_tool_defs(python_tools: list[Callable]) -> list[GroqToolDef]:
    """Builds Groq tool defs for exactly the callables passed in, in the
    same order, matched by function name against the hand-written schemas
    above. Raises if a tool has no matching schema, rather than silently
    omitting it — a tool Groq doesn't know about can't be called correctly."""
    defs = []
    for fn in python_tools:
        schema = _PYTHON_TOOL_SCHEMAS.get(fn.__name__)
        if schema is None:
            raise ValueError(
                f"No Groq tool schema defined for Python tool '{fn.__name__}' — "
                "add one to _PYTHON_TOOL_SCHEMAS in groq_tools.py."
            )
        defs.append(schema)
    return defs


def mcp_tool_defs(mcp_tools: list[mcp_types.Tool]) -> list[GroqToolDef]:
    """Converts real MCP tool metadata (from a real session.list_tools()
    call) into the same Groq schema shape. Nothing hand-written or
    guessed — every field comes from the live MCP server's own response."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        }
        for tool in mcp_tools
    ]


@dataclass
class ToolDispatch:
    """Maps a tool name to how it should be executed. `python_tools` holds
    the real callables (called directly, synchronously — they do no I/O
    beyond the already-open sync DB session). `mcp_tool_names` is just the
    set of names that should be routed through the real MCP session's
    `call_tool()` — the session itself is looked up separately by the
    caller, never stored here, since it's only valid for the lifetime of
    one `async with sqlite_mcp_session(...)` block."""

    python_tools: dict[str, Callable]
    mcp_tool_names: set[str]

    def kind_of(self, name: str) -> Union[Literal["python"], Literal["mcp"], Literal["unknown"]]:
        if name in self.python_tools:
            return "python"
        if name in self.mcp_tool_names:
            return "mcp"
        return "unknown"


def build_dispatch(python_tools: list[Callable], mcp_tools: list[mcp_types.Tool]) -> ToolDispatch:
    return ToolDispatch(
        python_tools={fn.__name__: fn for fn in python_tools},
        mcp_tool_names={t.name for t in mcp_tools},
    )
