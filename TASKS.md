# TASKS.md — OptiKinesis

Temporary progress and handoff notes. Update before finishing or handing off.
Durable knowledge belongs in `PROJECT_CONTEXT.md`.

_Last updated: 2026-10-13 by Claude Code (handoff)._

## Handoff status

- Branch `main`. The Web panel and shared context files were **committed and
  pushed to `origin/main`** at the user's request on 2026-10-13 (the commit
  that adds this file; see `git log`).
- The only change left out was `__pycache__/desktop_overlay.cpython-310.pyc`,
  a generated file tracked by mistake. It remains modified locally; keep it
  out of commits.
- No feature work is in progress mid-edit; the Web panel is code-complete.

## Current objective

TinyFish integration, phase 1: **answer cards** and the **clean reader** in a
new Web panel. Built, verified offline, committed and pushed.

## Completed

- **Web panel with answer cards and clean reader.**
  - New: `web_assist.py`, `tests/test_web_assist.py`.
  - Changed: `desktop_overlay.py` (`WebPanel`, `WebButton`, `AnswerCard`,
    `WebTaskBridge`, `request_answers`, `request_read_page`, Web pill button,
    new icons, `web_client` option on `configure_overlay`), `main.py`
    (`WEB_CLIENT` from `TINYFISH_API_KEY`, `reader_font_px` setting),
    `voice_commands.py` (`READ_PAGE` intent; "find" / "look up" / question
    phrasings; `set_web_handlers`), `tests/test_voice_commands.py`,
    `.env.example`, `requirements.txt` (pins `requests`, `pyperclip`, already
    installed), `README.md`, `OVERLAY_UI_MAP.md`, `AGENTS.md`,
    `PROJECT_CONTEXT.md`.
  - Decisions: REST via `requests` instead of the SDK (SDK needs Python
    3.11+); the reader gets the page URL from the browser's address bar only
    when a known browser is in front; voice searches fall back to Google when
    no key or overlay is available.
- **Shared context files** (`AGENTS.md`, `PROJECT_CONTEXT.md`, `TASKS.md`,
  `CLAUDE.md`) from the previous task. Committed with the Web panel.
- Earlier work: commit `2997fdd` on `main`, pushed (calibration, pill, cursor
  speed).

## Verification results

| Date | Command / check | Result |
|---|---|---|
| 2026-10-13 | `$env:PYTHONDONTWRITEBYTECODE="1"; .\.venv\Scripts\python.exe -m unittest discover` (in repo, at handoff) | Ran 55 tests, OK |
| 2026-10-13 | Scratch harness `live_web.py`: Web panel on screen with a fake TinyFish client and blink clicks through `GazeClickBridge` | 17/17 checks passed |
| 2026-10-13 | Scratch harness `smoke_app.py`: real `run_application` with camera stubbed | 13/13 passed; clean exit; foreground detection returned `code.exe` |
| 2026-10-13 | Unauthenticated `requests` probe of Search and Fetch endpoints | Both return `401 MISSING_API_KEY` (documented format) |

The harnesses (`live_web.py`, `smoke_app.py`, `live_pill.py`,
`live_calibration.py`) lived in the Claude session scratchpad, not the repo,
and are not available to other agents. They used fake clients, a stubbed
`capture_browser_url`, and no real OS clicks.

**Not verified (assumptions):**
- Real TinyFish responses match the documented shapes. Parsing follows the
  docs, but no key was available.
- "Read this page" works against a live browser. Only the fake keystroke
  path and foreground detection (`code.exe`) were exercised.
- The Web panel works under real head tracking and blinks.
- The voice dialog to Web panel hand-off when triggered by real speech.

## Blockers

- No `TINYFISH_API_KEY` in this environment, so live API testing needs the user.

## Next steps (prioritized)

1. With a key in `.env`, run `.\.venv\Scripts\python.exe main.py`. Use
   Web → Find answers, open a card, and Web → Read this page with Chrome or
   Edge in front. Record the results here and fix any parsing differences in
   `web_assist.py`.
2. Remove tracked `__pycache__/*.pyc` from Git with `git rm --cached` (needs
   commit authorization).
3. Manual test of calibration and pill with a real user. After that, the
   backlog in `PROJECT_CONTEXT.md` → Known issues: blink default, SOS reporting.
