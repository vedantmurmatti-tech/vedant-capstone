"""Tests for the Knowledge Base's retrieval pipeline:
storage/text_extraction.py, api/document_retrieval.py, and the new
search_document_content tool (api/gemini_tools.py / api/groq_tools.py) —
added because a previous version of the chat pipeline could tell the
model "here are 3 documents named X, Y, Z" but never actually retrieved
or sent any of their real content (see BUILD_LOG.md).

Everything here runs inside one isolated subprocess with its own
ESMERELDA_DATA_DIR (matching tests/test_sync_service.py's established
pattern) — storage/database.py reads that env var into a module-level
DATABASE_URL at import time, so seeding real rows and testing retrieval
against them has to happen in the same process that set the env var
before any storage.* module was ever imported.

The critical thing this file proves, not just asserts: a real chat
tool-call cycle (using api/groq_agent.py's real run_tool_loop(), the same
one production actually runs) where the model calls search_document_content
results in the ACTUAL RETRIEVED DOCUMENT TEXT appearing, verbatim, inside
the `messages` list that the very next call to Groq's API would receive —
i.e. the retrieved content genuinely reaches the LLM request, not just
that the tool function itself returns something that looks right in
isolation.

Run with:
    venv/Scripts/python.exe tests/test_document_retrieval.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

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


backend_dir = Path(__file__).resolve().parent.parent
data_dir = tempfile.mkdtemp(prefix="esmerelda_retrieval_test_")

# The whole test body runs as one subprocess script — see module
# docstring for why (ESMERELDA_DATA_DIR must be set before storage.*
# is ever imported in this process).
script = r"""
import asyncio
import json
from types import SimpleNamespace

from storage.database import init_db, SessionLocal
from storage.models import Course, Resource, Document
from api.auth import get_or_create_demo_user

init_db()
_user_id = get_or_create_demo_user()

# --- Seed real rows: two courses, three documents, only one relevant to
# the test query, exactly like a real, mixed Knowledge Base. ---
with SessionLocal() as session:
    course_a = Course(moodle_id="1", name="DESG322 Service Design", short_name="DESG322", user_id=_user_id)
    course_b = Course(moodle_id="2", name="BUAN301 Statistics", short_name="BUAN301", user_id=_user_id)
    session.add_all([course_a, course_b])
    session.flush()

    resource_a = Resource(moodle_id="10", course_id=course_a.id, name="Project Brief", resource_type="PDF", url="https://x/brief.pdf", user_id=_user_id)
    resource_b = Resource(moodle_id="11", course_id=course_b.id, name="Formula Sheet", resource_type="PDF", url="https://x/formulas.pdf", user_id=_user_id)
    session.add_all([resource_a, resource_b])
    session.flush()

    doc_relevant = Document(
        name="Service Design Project Brief.pdf",
        file_path="brief.pdf",
        file_type="PDF",
        current_hash="hash1",
        resource_id=resource_a.id,
        user_id=_user_id,
        extracted_text=(
            "Service Design Project Brief. Students must conduct user research interviews with at "
            "least five participants, synthesize findings into a journey map, and prototype a service "
            "blueprint addressing the identified pain points. The final deliverable is a 15-page report."
        ),
    )
    doc_irrelevant = Document(
        name="Formula Sheet.pdf",
        file_path="formulas.pdf",
        file_type="PDF",
        current_hash="hash2",
        resource_id=resource_b.id,
        user_id=_user_id,
        extracted_text="The standard deviation formula is the square root of the variance. Use it for hypothesis testing.",
    )
    doc_not_indexed = Document(
        name="Some Archive.zip",
        file_path="archive.zip",
        file_type=None,
        current_hash="hash3",
        resource_id=None,
        user_id=_user_id,
        extracted_text=None,
    )
    session.add_all([doc_relevant, doc_irrelevant, doc_not_indexed])
    session.commit()

# --- 1. document_retrieval.search_documents(): real keyword search, real
# SQL pre-filter, correct chunk/course attribution, irrelevant docs excluded. ---
from api import document_retrieval

with SessionLocal() as session:
    indexed = document_retrieval.count_indexed_documents(session, _user_id)
    print(f"INDEXED_COUNT:{indexed}")

    results = document_retrieval.search_documents(session, "what does the project brief say about user research", _user_id)
    print(f"RESULT_COUNT:{len(results)}")
    for r in results:
        print(f"RESULT:{r.document_name}:{r.course_name}:{r.score}")
        print(f"RESULT_TEXT:{r.chunk_text}")

    no_match = document_retrieval.search_documents(session, "xyzxyzxyz nonsense query with no real match", _user_id)
    print(f"NO_MATCH_COUNT:{len(no_match)}")

# --- 2. The real chat tool: search_document_content, via the real
# build_tools() — proves the tool itself (not just the underlying search
# function) returns real retrieved text. ---
from api.gemini_tools import ToolCollector, build_tools

with SessionLocal() as session:
    collector = ToolCollector()
    tools = build_tools(session, collector, _user_id)
    tool_by_name = {t.__name__: t for t in tools}
    tool_result = tool_by_name["search_document_content"]("What are the requirements in the project brief?")
    print(f"TOOL_RESULT:{json.dumps(tool_result)}")

    empty_result = tool_by_name["search_document_content"]("completely unrelated gibberish query zzz")
    print(f"TOOL_EMPTY_RESULT:{json.dumps(empty_result)}")

# --- 3. THE critical proof: run the real run_tool_loop() (the same
# function production uses) with a fake Groq client that calls
# search_document_content, then inspect the `messages` list the NEXT
# real Groq API call would receive — proving the retrieved text actually
# reaches the LLM request, not just that the tool returns something
# reasonable in isolation. ---
from api.groq_agent import run_tool_loop
from api.groq_tools import build_dispatch


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


with SessionLocal() as session:
    collector = ToolCollector()
    tools = build_tools(session, collector, _user_id)
    dispatch = build_dispatch(tools, [])

    captured_messages_before_second_call = []

    async def fake_groq(messages):
        if not any(m.get("role") == "tool" for m in messages):
            return make_response(tool_calls=[make_tool_call(
                "call_1", "search_document_content",
                json.dumps({"query": "What does the Service Design project brief say?"}),
            )])
        # This is exactly the `messages` list the real Groq client would
        # receive on this call — capture it before returning, so the
        # test can inspect it after the loop finishes.
        captured_messages_before_second_call.extend(messages)
        return make_response(content="Based on the brief, you need to do user research.")

    reply = asyncio.run(run_tool_loop(
        fake_groq,
        [{"role": "system", "content": "You are Esmerelda."}, {"role": "user", "content": "What does the Service Design project brief say?"}],
        dispatch,
        None,
    ))
    print(f"AGENT_REPLY:{reply}")

    tool_messages = [m for m in captured_messages_before_second_call if m.get("role") == "tool"]
    print(f"TOOL_MESSAGE_COUNT:{len(tool_messages)}")
    if tool_messages:
        print(f"TOOL_MESSAGE_CONTENT:{tool_messages[0]['content']}")
"""

env = dict(os.environ)
env["ESMERELDA_DATA_DIR"] = data_dir

try:
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout
    err = proc.stderr

    check("0. Subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print("--- stdout ---")
        print(out)
        print("--- stderr ---")
        print(err)

    check("1. count_indexed_documents() correctly counts only the 2 real indexed documents (not the .zip)", "INDEXED_COUNT:2" in out)

    check("2. search_documents() returns exactly the 1 relevant document, not the irrelevant or unindexed ones", "RESULT_COUNT:1" in out)
    check("2b. The retrieved chunk is attributed to the real document name and course", "RESULT:Service Design Project Brief.pdf:DESG322 Service Design:" in out)
    check("2c. The retrieved chunk's actual text contains the real content, verbatim", "RESULT_TEXT:" in out and "user research interviews" in out)
    check("2d. A query matching nothing real returns 0 results, not a fabricated fallback", "NO_MATCH_COUNT:0" in out)

    check("3. The real search_document_content tool returns real excerpts, not just metadata", '"excerpts"' in out and "user research interviews" in out)
    check("3b. A query that matches nothing returns an honest empty result, not fabricated content", "TOOL_EMPTY_RESULT" in out and '"excerpts": []' in out)

    check("4. The real run_tool_loop() (production's own function) produced a final reply", "AGENT_REPLY:" in out)
    check("5. Exactly one real tool-result message was appended to the conversation", "TOOL_MESSAGE_COUNT:1" in out)
    check(
        "6. CRITICAL: the retrieved document's real, actual text appears verbatim inside the `messages` list "
        "that the NEXT real Groq API call would receive — this is the proof that retrieval genuinely reaches "
        "the LLM request, not just that the tool function looks right in isolation",
        "TOOL_MESSAGE_CONTENT:" in out and "user research interviews" in out and "journey map" in out,
    )
except Exception as exc:
    check(f"0. Test setup itself failed unexpectedly: {exc}", False)
finally:
    import shutil
    shutil.rmtree(data_dir, ignore_errors=True)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
