"""Bridge to a real MCP server: mcp-server-sqlite, launched over stdio.

Verified before use (not guessed):
  - `mcp` (the official Model Context Protocol Python SDK, PyPI package
    `mcp`, version 2.2.0) is installed in backend/venv.
  - `mcp-server-sqlite` (PyPI package `mcp-server-sqlite`, version
    2025.4.25) is installed in the same venv; its console-script entry
    point is `venv/Scripts/mcp-server-sqlite.exe`, confirmed by running it
    with `--help`, which prints exactly one real option: `--db-path`.
  - Its installed source (`mcp_server_sqlite/server.py`) was read directly
    to confirm which MCP tools it actually registers: `read_query`,
    `write_query`, `create_table`, `list_tables`, `describe_table`,
    `append_insight`. Three of those are real write operations.

Read-only enforcement (two independent layers, since the server itself has
no read-only flag):
  1. `_ALLOWED_TOOLS` below is the complete allow-list handed to Gemini —
     `read_query`, `list_tables`, `describe_table` only. `list_tools()` is
     overridden to strip everything else out of what the SDK sees, so
     Gemini is never even told `write_query`/`create_table`/
     `append_insight` exist. `call_tool()` additionally refuses (raises
     `PermissionError`, not a silent no-op) any name outside that
     allow-list, as defense in depth against a bug or future SDK change.
  2. The server is never pointed at the live `backend/storage/esmerelda.db`
     that the rest of this app (and the Moodle radar scripts) read and
     write. Each session copies it to a throwaway snapshot file and marks
     that copy read-only at the OS level (`os.chmod(..., stat.S_IREAD)`)
     before the server process ever opens it — so even a call this bridge
     didn't intend to allow would fail at the sqlite3 driver itself
     (`attempt to write a readonly database`), not merely be asked nicely
     not to happen. The snapshot is remade fresh at the start of every
     session (so it reflects the live database as of that moment) and
     deleted when the session closes.

`LoggingSqliteSession` subclasses `mcp.ClientSession` directly (required —
`google-genai`'s own MCP support does an `isinstance(tool, ClientSession)`
check on whatever is passed into `GenerateContentConfig(tools=[...])`) and
records into a shared `McpCallLog` only calls that actually went through
`call_tool()` — nothing is logged for a session that was opened but never
used.
"""

import os
import shutil
import stat
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import mcp.types as mcp_types

BACKEND_DIR = Path(__file__).resolve().parent.parent
LIVE_DB_PATH = BACKEND_DIR / "storage" / "esmerelda.db"
READONLY_SNAPSHOT_PATH = BACKEND_DIR / "storage" / "_esmerelda_mcp_readonly_snapshot.db"

_ALLOWED_TOOLS = {"read_query", "list_tables", "describe_table"}


def _sqlite_server_executable() -> str:
    venv_scripts = Path(sys.executable).parent
    windows_exe = venv_scripts / "mcp-server-sqlite.exe"
    if windows_exe.is_file():
        return str(windows_exe)
    posix_script = venv_scripts / "mcp-server-sqlite"
    if posix_script.is_file():
        return str(posix_script)
    raise FileNotFoundError(
        "mcp-server-sqlite console script not found next to the current Python "
        f"interpreter ({sys.executable}). Is it installed in this venv?"
    )


def _make_readonly_snapshot() -> Path:
    if READONLY_SNAPSHOT_PATH.exists():
        os.chmod(READONLY_SNAPSHOT_PATH, stat.S_IWRITE)
        READONLY_SNAPSHOT_PATH.unlink()
    shutil.copyfile(LIVE_DB_PATH, READONLY_SNAPSHOT_PATH)
    os.chmod(READONLY_SNAPSHOT_PATH, stat.S_IREAD)
    return READONLY_SNAPSHOT_PATH


def _remove_readonly_snapshot() -> None:
    if READONLY_SNAPSHOT_PATH.exists():
        os.chmod(READONLY_SNAPSHOT_PATH, stat.S_IWRITE)
        READONLY_SNAPSHOT_PATH.unlink()


@dataclass
class McpCall:
    server: str
    tool: str
    arguments: dict[str, Any]
    is_error: bool
    result_summary: str


@dataclass
class McpCallLog:
    calls: list[McpCall] = field(default_factory=list)

    def record(self, server: str, tool: str, arguments: dict[str, Any], result: mcp_types.CallToolResult) -> None:
        text_parts = [c.text for c in result.content if isinstance(c, mcp_types.TextContent)]
        summary = " ".join(text_parts)[:500]
        self.calls.append(
            McpCall(
                server=server,
                tool=tool,
                arguments=arguments,
                is_error=bool(result.isError),
                result_summary=summary,
            )
        )


class LoggingSqliteSession(ClientSession):
    """A real mcp.ClientSession subclass — required so google-genai's own
    `isinstance(tool, ClientSession)` check accepts it as an MCP tool
    source — that filters exposed tools to a read-only allow-list and logs
    every call that actually happens."""

    def __init__(self, *args: Any, call_log: McpCallLog, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._call_log = call_log

    def __deepcopy__(self, memo: dict) -> "LoggingSqliteSession":
        # google-genai's Chat.send_message() does `config.model_copy(deep=True)`
        # on the GenerateContentConfig — and this session sits in its `tools`
        # list. A real, live async session (open streams, an anyio task group,
        # pending Futures) cannot be meaningfully deep-copied — plain
        # `copy.deepcopy()` on this object reproducibly raises
        # `TypeError: cannot pickle '_asyncio.Future' object` (confirmed with a
        # standalone, zero-API-call repro against this exact class). Worse, if
        # it didn't hard-fail, a structurally-cloned-but-disconnected copy
        # would desync the tool declarations built from one instance from the
        # dispatch table built from another, which is what produced a real
        # `KeyError: 'read_query'` during live testing — the model correctly
        # named the real tool; the mismatch was here, not in tool selection.
        # A live resource must not be duplicated by a "copy" — returning the
        # same instance is the standard idiom (mirrors how threading.Lock,
        # open file handles, etc. are conventionally handled by __deepcopy__).
        memo[id(self)] = self
        return self

    async def list_tools(self, *args: Any, **kwargs: Any) -> mcp_types.ListToolsResult:
        result = await super().list_tools(*args, **kwargs)
        result.tools = [t for t in result.tools if t.name in _ALLOWED_TOOLS]
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *args: Any, **kwargs: Any):
        if name not in _ALLOWED_TOOLS:
            raise PermissionError(f"MCP tool '{name}' is not permitted on this read-only sqlite bridge")
        result = await super().call_tool(name, arguments, *args, **kwargs)
        self._call_log.record(server="sqlite", tool=name, arguments=arguments or {}, result=result)
        return result


@asynccontextmanager
async def sqlite_mcp_session(call_log: McpCallLog) -> AsyncIterator[ClientSession]:
    """Launches the real mcp-server-sqlite subprocess over stdio, connects
    with mcp.ClientSession, and calls session.initialize(), against a
    fresh read-only snapshot of the live database. Guarantees the
    subprocess and its streams are closed and the snapshot is removed even
    if the caller raises."""
    snapshot_path = _make_readonly_snapshot()
    server_params = StdioServerParameters(
        command=_sqlite_server_executable(),
        args=["--db-path", str(snapshot_path)],
    )
    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            session = LoggingSqliteSession(read_stream, write_stream, call_log=call_log)
            async with session:
                await session.initialize()
                yield session
    finally:
        _remove_readonly_snapshot()
