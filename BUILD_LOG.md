# Build Log

One entry per commit. Fill this in before committing and pushing.

---

## 2026-09-11

- **Time spent**: ~[fill in]
- **Rough tokens used**: ~[fill in]
- **What shipped**: Locked capstone idea (voice-first AI academic assistant for Moodle); set up repo, branch, plan.md separating MVP scope from final goals, and this BUILD_LOG.md. Opened PR #1 for review.

---

## 2026-09-16

- **Time spent**: ~[fill in]
- **Rough tokens used**: ~[fill in]
- **What shipped**: First working slice of the MVP's Moodle connection and change-detection loop, in a new `Esmerelda/` app (backend + frontend), all under `backend/`:
  - `moodle/browser.py` — Playwright connector using a persistent Chromium profile (`browser_profile/`) so login only has to happen once per machine; prompts for manual login when the session isn't already authenticated, then reads active courses by locating Moodle's "Only courses in progress" dashboard text and matching it against `/course/view.php` links.
  - `course_radar.py` — reworked active-course detection to be less dependent on that dashboard text: matches course names against a regex for the current term's course codes (`BUAN`/`DESG`/`VATS` + `2026/27S1`) instead, and moved the persistent browser profile under `moodle/browser_profile/` so all scripts share one logged-in session.
  - `content_radar.py` — for each active course, walks its sections and activity links, classifies each item by type (PDF, PowerPoint, Assignment, Quiz, Video, Forum, etc.) from the link/href, and filters out navigation/UI noise. Persists results to `state/content_snapshot.json` and diffs the new scrape against the last one to find newly-added items — this is the change-detection piece from the plan, so the same resource doesn't get reported twice.
  - `submission_radar.py` — scans Moodle's Timeline block for entries matching "is due", walks up the DOM from each link to find the owning course, deduplicates by assignment+course, and turns the result into a short spoken briefing via `pyttsx3` (e.g. "You have 2 upcoming submissions..."). This is the first text-to-speech output for the proactive digest — earlier than planned (final goals listed real voice I/O as post-MVP), but it was a natural fallback once `voice.py`'s simple `EsmereldaVoice` wrapper class was working.
  - `voice.py` — small standalone `EsmereldaVoice` class wrapping `pyttsx3` (rate/volume set, `speak()` method), used as the first voice-output test before folding TTS into `submission_radar.py`.
  - `frontend/` — scaffolded a Vite + React + TypeScript app; still the default template, not yet wired to the backend.
  - Known rough edges going into the next session: `course_radar.py`'s active-course regex is hardcoded to this term's course codes and will need updating (or a more general rule) next term; `content_radar.py`'s course list is hardcoded rather than pulled from `course_radar.py`'s output; the two active-course-detection approaches (`browser.py`'s text-based one and `course_radar.py`'s regex-based one) haven't been consolidated into one.

---

<!-- Add the next entry above this line, newest at the top or bottom — just be consistent -->
