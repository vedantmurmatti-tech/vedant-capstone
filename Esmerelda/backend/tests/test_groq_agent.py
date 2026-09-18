"""Local tests for the Groq agent (backend/api/groq_agent.py,
backend/api/groq_tools.py). No real Groq or Gemini API calls are made —
Groq responses are faked with `SimpleNamespace` objects matching the real
SDK's shape (`response.choices[0].message.{content,tool_calls}`, each
tool_call having `.id`, `.function.name`, `.function.arguments`), and the
Gemini/Groq fallback test raises client-side before any network request
would be constructed. The MCP tests use the real `mcp-server-sqlite`
subprocess and the real local database — that's free, local, and not
subject to any external quota, unlike the LLM providers.

Not a pytest suite (pytest isn't installed in this project) — plain
asyncio + assert-style checks, matching the existing convention in
backend/storage/test_storage.py.

Run with:
    venv/Scripts/python.exe tests/test_groq_agent.py
(from the backend/ directory, or any cwd — this script adds backend/ to
sys.path itself.)
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.gemini_tools import ToolCollector, build_tools
from api.groq_agent import run_tool_loop
from api.groq_tools import build_dispatch, mcp_tool_defs, python_tool_defs
from api.mcp_bridge import McpCallLog, sqlite_mcp_session
from storage.database import SessionLocal

results: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    results.append((name, status))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def make_response(content: str | None = None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


# --- 1. Groq tool schema conversion -----------------------------------------


async def test_schema_conversion() -> None:
    db = SessionLocal()
    collector = ToolCollector()
    python_tools = build_tools(db, collector)

    defs = python_tool_defs(python_tools)
    names = {d["function"]["name"] for d in defs}
    check(
        "1. Groq tool schema conversion — Python tools",
        names
        == {"get_upcoming_assignments", "get_course_info", "get_course_documents", "plan_assignment_action"}
        and all(d["type"] == "function" and "parameters" in d["function"] for d in defs),
        f"names={sorted(names)}",
    )

    async with sqlite_mcp_session(McpCallLog()) as session:
        mcp_tools = (await session.list_tools()).tools
        mdefs = mcp_tool_defs(mcp_tools)
        mnames = {d["function"]["name"] for d in mdefs}
        check(
            "1b. Groq tool schema conversion — MCP tools",
            mnames == {"read_query", "list_tables", "describe_table"}
            and all(isinstance(d["function"]["parameters"], dict) for d in mdefs),
            f"names={sorted(mnames)}",
        )
    db.close()


# --- 2. Tool-call parsing, 5. Max iteration protection ----------------------


async def test_tool_call_parsing_and_max_iterations() -> None:
    db = SessionLocal()
    collector = ToolCollector()
    python_tools = build_tools(db, collector)
    dispatch = build_dispatch(python_tools, [])

    call_count = {"n": 0}

    async def always_calls_tool(messages):
        call_count["n"] += 1
        return make_response(tool_calls=[make_tool_call(f"call_{call_count['n']}", "get_upcoming_assignments", "{}")])

    reply = await run_tool_loop(
        always_calls_tool,
        [{"role": "system", "content": "t"}, {"role": "user", "content": "hi"}],
        dispatch,
        None,
        max_iterations=3,
    )
    check(
        "5. Maximum iteration protection",
        call_count["n"] == 3 and "safe number of steps" in reply,
        f"calls made={call_count['n']}, reply={reply!r}",
    )

    async def one_call_then_done(messages):
        if not any(m.get("role") == "tool" for m in messages):
            return make_response(tool_calls=[make_tool_call("call_1", "get_upcoming_assignments", "{}")])
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        ok = len(tool_msgs) == 1 and "assignments" in tool_msgs[0]["content"]
        return make_response(content=f"parsed_ok={ok}")

    reply2 = await run_tool_loop(
        one_call_then_done,
        [{"role": "system", "content": "t"}, {"role": "user", "content": "what's due"}],
        dispatch,
        None,
    )
    check("2. Tool-call parsing — real tool executed, result appended", reply2 == "parsed_ok=True", f"reply={reply2!r}")

    async def malformed_args_then_done(messages):
        if not any(m.get("role") == "tool" for m in messages):
            return make_response(tool_calls=[make_tool_call("call_1", "get_course_info", "{not valid json")])
        tool_msg = [m for m in messages if m.get("role") == "tool"][-1]
        return make_response(content=f"handled={'error' in tool_msg['content']}")

    reply3 = await run_tool_loop(
        malformed_args_then_done,
        [{"role": "system", "content": "t"}, {"role": "user", "content": "tell me about X"}],
        dispatch,
        None,
    )
    check(
        "2b. Tool-call parsing — malformed JSON arguments handled, not a crash",
        reply3 == "handled=True",
        f"reply={reply3!r}",
    )
    db.close()


# --- 3. MCP tool routing, 4. write operations blocked -----------------------


async def test_mcp_routing_and_write_block() -> None:
    db = SessionLocal()
    collector = ToolCollector()
    python_tools = build_tools(db, collector)
    call_log = McpCallLog()

    async with sqlite_mcp_session(call_log) as session:
        mcp_tools = (await session.list_tools()).tools
        dispatch = build_dispatch(python_tools, mcp_tools)

        async def calls_read_query(messages):
            if not any(m.get("role") == "tool" for m in messages):
                return make_response(
                    tool_calls=[
                        make_tool_call("call_1", "read_query", '{"query": "SELECT COUNT(*) AS n FROM documents"}')
                    ]
                )
            tool_msg = [m for m in messages if m.get("role") == "tool"][-1]
            return make_response(content=tool_msg["content"])

        reply = await run_tool_loop(
            calls_read_query,
            [{"role": "system", "content": "t"}, {"role": "user", "content": "how many docs"}],
            dispatch,
            session,
        )
        check(
            "3. MCP tool routing — real call_tool() invoked through the real session",
            "34" in reply and len(call_log.calls) == 1 and call_log.calls[0].tool == "read_query",
            f"reply={reply!r}, calls={[c.tool for c in call_log.calls]}",
        )

        check(
            "4. Write operations blocked — write_query never even offered as a tool",
            dispatch.kind_of("write_query") == "unknown",
        )

        pre = len(call_log.calls)
        blocked = False
        try:
            await session.call_tool("write_query", {"query": "DELETE FROM assignments"})
        except PermissionError:
            blocked = True
        check(
            "4b. Write operations blocked — call_tool() itself refuses (defense in depth)",
            blocked and len(call_log.calls) == pre,
        )
    db.close()


# --- 6. assignment-action-planner Skill -------------------------------------


async def test_skill_still_works() -> None:
    from api.skills.assignment_action_planner import plan_for_assignment
    from storage.models import Assignment, Course

    db = SessionLocal()
    assignment, course = db.query(Assignment, Course).join(Course, Assignment.course_id == Course.id).first()
    plan = plan_for_assignment(assignment, course)
    check(
        "6. assignment-action-planner Skill still works, still source-grounded",
        plan.assignment_name == assignment.name and plan.grounding.submission_url == assignment.submission_url,
        f"urgency={plan.urgency}",
    )
    db.close()


# --- 7. Fallback behavior (Groq -> Gemini -> deterministic), zero network calls --


async def test_fallback_chain() -> None:
    from api import chat_agent, gemini_agent, groq_agent

    def fake_groq_client():
        raise groq_agent.GroqUnavailableError("simulated for test — no network call made")

    def fake_gemini_client():
        raise gemini_agent.GeminiUnavailableError("simulated for test — no network call made")

    groq_agent._get_client = fake_groq_client
    gemini_agent._get_client = fake_gemini_client

    db = SessionLocal()
    response = await chat_agent.handle_chat_message(db, "what is due this week?")
    db.close()

    check(
        "7. Fallback chain — Groq and Gemini both unavailable, deterministic agent answers",
        "Both Groq and Gemini were unavailable" in response.reply
        and "simulated for test" in response.reply
        and response.mcpCalls == []
        and len(response.assignments) == 2,
        f"reply starts: {response.reply[:120]!r}",
    )


async def main() -> None:
    await test_schema_conversion()
    await test_tool_call_parsing_and_max_iterations()
    await test_mcp_routing_and_write_block()
    await test_skill_still_works()
    await test_fallback_chain()

    print("\n--- Summary ---")
    failures = [r for r in results if r[1] == "FAIL"]
    print(f"{len(results) - len(failures)}/{len(results)} passed")
    if failures:
        print("FAILURES:", failures)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
