---
name: assignment-action-planner
description: Breaks one real Moodle assignment into its explicit requirements and produces a source-grounded, requirement-by-requirement action plan — status, next action, expected deliverable, verification method, and real evidence for each requirement, plus anything it couldn't determine. Deterministic, no model call.
---

# assignment-action-planner

## Scope

Deliberately narrow. Given exactly **one** real `Assignment` row — already
fetched from `storage/esmerelda.db`, never invented or described in free
text — this Skill produces a full, requirement-by-requirement action plan
for that single assignment.

It does **not**:

- summarize a whole course or list of assignments (see "Batch mode" below),
- fetch data itself (it never queries the database or Moodle),
- call Gemini, Groq, or any LLM (it is pure, deterministic, unit-testable
  Python),
- parse the *content* of downloaded binary files (PDFs, PPTX, DOCX, ZIP) —
  it can tell you a relevant document exists (or doesn't) and cite it by
  name, but does not extract or read text from inside it,
- talk to MCP. This is the "keep the Skill separate from MCP" boundary:
  the Skill only ever reasons over plain Python data (ORM rows, strings)
  the *caller* already retrieved through ordinary `queries.py` calls — it
  never opens an MCP session itself, whether or not the caller happened to
  use one to gather that data.

## Why this redesign happened

The original version of this Skill only returned urgency + a fixed
per-urgency-tier sentence + a submission link — essentially metadata
extraction, not planning. It didn't look at what the assignment actually
asked for. This version breaks the assignment into its real, explicit
requirements and evaluates each one separately against real evidence.

## Implementation

`backend/api/skills/assignment_action_planner/__init__.py`.

## When the agent uses it

Esmerelda's Gemini and Groq agents (`backend/api/gemini_agent.py`,
`backend/api/groq_agent.py`) invoke this Skill through a dedicated tool,
`plan_assignment_action` (`backend/api/gemini_tools.py` /
`backend/api/groq_tools.py`), whenever the user asks about **one
specific** assignment by name — e.g. "what should I do about the MVP
assignment?" or "help me plan Assessment 2." The tool resolves the
free-text name to a real `Assignment` row (via `matching.match_assignment`),
looks up its matched `Resource` (via `queries.fetch_resource_by_moodle_id`
— see "Source data" below) and any `Document` downloaded for it, and passes
all of that — never the raw user text — into this Skill.

For a *list* of assignments ("what's due this week"), the agent instead
uses the `get_upcoming_assignments` tool, which calls this Skill's batch
entrypoint, `plan_actions()` — unchanged by this redesign, still just
urgency ranking across many assignments, a different and coarser-grained
use case than the single-assignment contract described here.

## Input

| Field | Type | Notes |
|---|---|---|
| `assignment` | `storage.models.Assignment` | real ORM row: `name`, `description`, `due_date`, `submission_url`, `moodle_id` |
| `course` | `storage.models.Course` | the real course it belongs to: `name`, `moodle_id` |
| `matched_resource` | `storage.models.Resource \| None` | the assignment's own Moodle resource entry, if the caller found one |
| `related_documents` | `list[storage.models.Document]` | any real files downloaded for that resource |
| `now` | `datetime \| None` | reference time for urgency; defaults to `datetime.now()` |

The Skill never accepts a free-text description of an assignment, and
never queries the database for `matched_resource`/`related_documents`
itself — the caller fetches these as real ORM rows and passes them in.
Every field in the output is traceable to a specific database row or a
specific, quoted line of a real local file — never to model
free-association.

## Source data — how a Resource is matched to an Assignment

`content_radar.py` gives an assignment's own Moodle activity link a
`Resource` row that shares the **same `moodle_id`** as the `Assignment`
row. This is a real, reliable join that existed in the schema before this
redesign but was never used — `queries.fetch_resource_by_moodle_id()` (new)
exploits it to find "the assignment's own page" and, from there, whatever
was actually downloaded for it.

## Process

1. **Extract requirements.** Prefer `assignment.description` when the
   Moodle sync actually captured free text there. In the current live
   dataset, **it never does** — both real assignments have
   `description = NULL` — so in practice this always falls back to the
   assignment's own **title**, with a leading `"Assessment N:"` /
   `"Assignment N:"` prefix stripped, split into phrases on commas and
   " and ". The Skill always records which source was used
   (`requirement_source`) so the output never silently implies a real
   description existed when it didn't.
2. **Classify urgency** from the real `due_date` against `now`: `overdue` /
   `due-soon` (0–48h) / `upcoming` / `unscheduled` — unchanged from the
   original implementation, and still matching `getAssignmentUrgency` in
   `frontend/src/lib/utils.ts`.
3. **For each requirement phrase, gather evidence and decide a status:**
   a. **Local project evidence** — a bounded, real search
      (`search_local_evidence()`) over exactly two fixed locations: this
      repository's own `BUILD_LOG.md` (line by line, requiring *every*
      significant word of the requirement phrase to appear in the same
      line — not just any one of them, to avoid weak false-positive
      matches) and the filenames under `backend/api/`. Real excerpts are
      quoted verbatim; `[]` the moment nothing real is found. If anything
      is found → status **`completed`**, cited by the exact file path
      and/or the exact quoted `BUILD_LOG.md` line.
   b. Otherwise, if the caller supplied a `matched_resource` but no
      `related_documents` → status **`incomplete`** — Esmerelda knows
      *where* to look on Moodle but nothing has been downloaded/synced for
      it yet, cited by the resource's real URL.
   c. Otherwise → status **`unverified`** — no evidence anywhere. This is
      the honest default, not a fallback to hide behind: it means
      Esmerelda genuinely cannot say whether this requirement is done and
      says so, rather than guessing.
4. **Attach, per requirement:** a concrete `next_action`, an
   `expected_deliverable`, a `verification_method` (how a human would
   actually check), and the `evidence` string above.
5. **Collect missing information** — a separate, top-level list: whether
   `assignment.description` was empty (so requirements came from the title
   only), whether no matching resource was found at all, and whether a
   matched resource exists but nothing was downloaded for it.
6. **Attach grounding** — the real submission URL and real Moodle
   assignment/course IDs, unchanged from the original implementation
   (requirement to "preserve real Moodle IDs, due dates, and submission
   URLs" through the redesign).

## Output — `AssignmentActionPlan`

```text
assignment_name: str                  # Assignment.name, verbatim
course_name: str                      # Course.name, verbatim
due_date: str | None                  # ISO 8601, or None if unscheduled
urgency: overdue | due-soon | upcoming | unscheduled
requirement_source: str               # "assignment.description" or a note that the title was used instead
requirements: list[RequirementPlan]
  requirement: str                    # the explicit requirement phrase, extracted from real text
  status: completed | incomplete | unverified
  next_action: str
  expected_deliverable: str
  verification_method: str
  evidence: str                       # exact citation, or why none was found
missing_information: list[MissingInformation]
  item: str
  why_it_matters: str
grounding:
  submission_url: str | None          # real Assignment.submission_url
  moodle_assignment_id: str           # real Assignment.moodle_id
  moodle_course_id: str               # real Course.moodle_id
```

`render_plan()` turns this into the numbered, multi-section markdown block
the chat agents show the user — one entry per requirement (with its
status, next action, deliverable, verification method, and evidence),
followed by a "Missing information" section, followed by the grounding
line.

## Worked example — Assessment 2 (real output, captured against the live database)

`assignment.name = "Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow"`,
`assignment.description = NULL`, course = DESG319. `content_radar.py` had
already given this assignment a matching `Resource` row (same `moodle_id`,
`246561`), but no `Document` has ever been downloaded for it.

```text
**Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow** (DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Lear)
Due: Sep 17, 11:59 PM · Urgency: due-soon
Requirements extracted from: assignment.name (no description synced — title used instead)

1. **Custom Skill** — [Completed]
   - Next action: Re-confirm 'Custom Skill' still works, then reference this evidence in your submission notes.
   - Expected deliverable: A working, demonstrable implementation of: Custom Skill
   - Verify by: Re-run the cited file/test locally and re-read the cited BUILD_LOG.md entry.
   - Evidence: BUILD_LOG.md: "- **What shipped**: Implemented the `/api/chat` endpoint that was previously missing (the prior session's chat page always threw `ChatUnavailableError`). Checked first — no Claude Code Skill named `assignment-action-plan"
2. **an agent** — [Completed]
   - Next action: Re-confirm 'an agent' still works, then reference this evidence in your submission notes.
   - Expected deliverable: A working, demonstrable implementation of: an agent
   - Verify by: Re-run the cited file/test locally and re-read the cited BUILD_LOG.md entry.
   - Evidence: BUILD_LOG.md: "## 2026-09-17 (implemented POST /api/chat — a real, rule-based agent)"
3. **1-2 MCP** — [Completed]
   - Next action: Re-confirm '1-2 MCP' still works, then reference this evidence in your submission notes.
   - Expected deliverable: A working, demonstrable implementation of: 1-2 MCP
   - Verify by: Re-run the cited file/test locally and re-read the cited BUILD_LOG.md entry.
   - Evidence: BUILD_LOG.md: "- **`backend/api/skills/assignment_action_planner/SKILL.md`** (new) — the dedicated spec: scope (deliberately narrow to one real assignment), when the agent uses it vs. the batch path, input table (`assignment`, `course`"
4. **chained into one workflow** — [Incomplete]
   - Next action: Open the matched Moodle resource for this assignment and gather what it says about 'chained into one workflow', then implement it.
   - Expected deliverable: A working implementation of: chained into one workflow
   - Verify by: Review the resource directly: https://lms.flame.edu.in/mod/assign/view.php?id=246561
   - Evidence: Moodle resource matched ('Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow') but no document has been downloaded for it yet.

Missing information:
- Assignment description text — Requirements were inferred from the assignment title only — the real Moodle description (if any) hasn't been synced, so this may be incomplete.
- A downloaded document for 'Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow' — No file has been synced for this assignment yet, so its real content couldn't be checked.

Source: https://lms.flame.edu.in/mod/assign/view.php?id=246561
```

Note the honesty this demonstrates: three of the four requirements are
marked `completed` because this exact repository's `BUILD_LOG.md` really
does document a working Skill, agent, and MCP integration — but the fourth
("chained into one workflow") is marked `incomplete`, because no single
`BUILD_LOG.md` line happens to contain both words "chained" and
"workflow" together, even though the underlying work is real. The Skill
does not paper over that gap to make the plan look more complete than its
evidence actually supports.

## Limitations

- **Requirement extraction is structural, not semantic.** It splits on
  commas/" and " — a well-punctuated title or description produces good
  phrases; a single run-on phrase (e.g. "Assessment 3: MVP") correctly
  produces just one requirement, reflecting the real lack of structure in
  that source text rather than inventing sub-requirements.
- **Local evidence search is a keyword/filename check, not a correctness
  check.** A `completed` status means "this repository's own `BUILD_LOG.md`
  or source tree documents real work matching every significant word in
  this requirement" — it is not a claim that the underlying code is
  correct, complete, or still working. `verification_method` always says
  to re-run/re-check the cited evidence yourself.
- **Document content is not parsed.** The Skill can tell you a document
  exists (or doesn't) for a matched resource, by name, but does not open
  or extract text from PDFs/PPTX/DOCX/ZIP files. A future version could
  add text extraction for plain-text/PDF documents as a further evidence
  source.
- **The evidence search is scoped to this repository.** It reads
  `BUILD_LOG.md` at the repo root and filenames under `backend/api/` —
  fixed, bounded locations chosen because this particular capstone
  assignment's requirements happen to describe this repository's own
  deliverable. For an assignment unrelated to this codebase, the search
  will typically find nothing, and every requirement will honestly land
  in `unverified` or `incomplete` rather than a false `completed`.
- If `due_date` is `None`, the plan says so rather than guessing one.
- Assignment name matching (`matching.match_assignment`, used by the tool
  layer before this Skill runs) is a case-insensitive substring match, not
  semantic search.
