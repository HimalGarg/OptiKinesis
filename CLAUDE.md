# CLAUDE.md

Claude Code reads this file, not `AGENTS.md`, so the shared instructions are
imported here:

@AGENTS.md

## Claude Code specifics

- `PROJECT_CONTEXT.md` and `TASKS.md` are not auto-loaded. Read them at the
  start of each session, as the operating rules in `AGENTS.md` require.
- This machine is Windows: prefer the PowerShell tool; `.venv\Scripts\python.exe`
  is the interpreter.
- Personal auto-memory under `~/.claude/` is not visible to other agents.
  Anything another agent needs goes in `PROJECT_CONTEXT.md` or `TASKS.md`.
