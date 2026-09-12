# Capstone Plan — Voice-First Academic Assistant

## The idea
A voice-first AI academic assistant that connects to a student's Moodle account, monitors their active courses for deadlines and new learning material, understands and summarizes what matters, and proactively tells them what they need to know or do — instead of the student having to go check Moodle themselves.

## Problem framing
Moodle is a pull system: a student only sees a deadline or a new file if they remember to log in and look. The cost isn't "the info isn't there," it's that surfacing it takes active effort, and that effort competes with everything else a student is juggling. The assistant's job is to shift this from pull to push, and to compress "here's a 4-page assignment brief" into "here's what you actually need to do and by when."

## MVP scope (what ships for this capstone)
The MVP proves the core loop end-to-end for 1–2 real courses, using text as the delivery channel instead of full voice (voice I/O is its own hard problem — see Final Goals).

- **Moodle connection**: Pull course list, assignment due dates, and newly added resources via browser automation (Playwright) driving a real logged-in session, for the courses I'm actually enrolled in — chosen over the Moodle Web Services API because that API requires an admin-granted token I likely can't get as a student; automating my own logged-in session needs no institutional permission.
- **Change detection**: Track what's already been surfaced so the same deadline/file isn't reported twice.
- **Summarization**: Feed raw assignment descriptions / uploaded material through an LLM to produce a short, plain-language summary — what it is, what's due, what "done" looks like.
- **Proactive digest**: A scheduled job (daily) that assembles a short digest of "what's due soon" + "what's new" and delivers it as text (console output, or a simple message to something like Telegram/Discord as a stand-in for a real notification channel).
- **On-demand query**: A basic way to ask "what's due this week" and get an answer back from the same data.

**Explicitly out of MVP**: actual speech input/output, cross-course prioritization logic, any kind of personalization/learning from behavior.

## Final goals (where this is aimed, beyond the capstone deadline)
- **True voice-first interaction**: speech-to-text for queries, text-to-speech for the proactive briefing — this is the actual product vision, not just a chat app that happens to summarize Moodle.
- **Priority modeling, not just chronology**: weigh deadlines by estimated effort + time remaining + how the student has historically handled similar tasks, instead of a flat due-date sort.
- **Cross-course synthesis**: a weekly spoken briefing that looks across all active courses at once and flags overload / conflicting deadlines, not one course at a time.
- **Calibrated transparency**: the assistant should say how confident it is in a summary rather than presenting every summary with equal authority — directly ties to my interest in AI transparency in agentic UX.
- **Ambient delivery**: eventually less "open an app," more "it just tells you," e.g. a phone widget/companion or smart-speaker-style delivery.

## AI-involvement level
**Target: high for implementation scaffolding, low–medium for product decisions.**

Why: my gap on this project is backend/API plumbing (Moodle API auth, scheduling, STT/TTS wiring) — not judgment about what the assistant should say, when it should interrupt, or how it should be designed. I want to use AI heavily to close the technical-implementation gap fast (auth boilerplate, API integration code, prompt scaffolding) so more of my own time goes into the parts that are actually mine: what "proactive" should feel like without being annoying, how a summary should be worded, what the interaction should prioritize. Every product/UX decision in this doc is mine; a meaningful share of the code that implements it won't be, and I'll say so per commit in BUILD_LOG.md.

## Tech stack (tentative, will firm up in code)
- Playwright (headless Chromium) driving a logged-in Moodle session for data access
- Claude API for summarization / query answering
- Speech-to-text / text-to-speech provider — TBD, deferred to post-MVP
- Simple scheduler (cron or equivalent) for the daily digest job

## Open risks
- **Selector fragility**: scraping depends on my institution's specific Moodle theme/page structure; a theme change or plugin update can break parsing with no warning. Mitigate with a small test suite against known pages + graceful failure (flag "couldn't read this page" rather than silently missing a deadline).
- **Session/credential handling**: unattended daily checks need something that can log back in on its own, which means storing my Moodle login or a persisted session somewhere. Needs an explicit answer for where that lives and how it's protected before this runs unattended — not deferred to "later."
- **Rate/load on Moodle**: repeated automated page loads should stay well within normal single-user browsing behavior — no aggressive polling.
- "Proactive" notifications can easily become noise — MVP should err toward fewer, higher-signal digests rather than frequent pings.
