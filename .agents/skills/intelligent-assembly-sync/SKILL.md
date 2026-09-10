---
name: intelligent-assembly-sync
description: Synchronize the intelligent assembly competition repository and its durable task context when the user asks to switch computers, pull the latest project, save progress, hand off work, commit, or push. Do not use for ordinary work that does not request synchronization.
---

# Intelligent Assembly Sync

Keep the repository and its durable context consistent across two computers without copying machine-local Codex state.

## Before any sync

Resolve the repository root with `git rev-parse --show-toplevel`. Read `AGENTS.md`, `context/PROJECT_STATE.md`, `context/DECISIONS.md`, and the newest entry in `context/TASK_LOG.md`.

Run `git status --short --branch`. Never use `reset`, `clean`, automatic stash, force push, or commands that discard changes.

## Pull latest work

- If the worktree is clean, run `git pull --ff-only`.
- If the worktree is dirty, do not pull. Report the exact modified paths and ask whether the user wants to finish, commit, or manually reconcile them.
- If the branch diverged or a fast-forward pull fails, stop and report the branch state. Do not resolve cross-computer conflicts without the user's direction.

## Save and push work

- Use this mode only when the user explicitly asks to sync, save to GitHub, commit, upload, or push.
- Run tests appropriate to the changed code and record the actual result.
- Update `context/PROJECT_STATE.md` only for current facts and unresolved hardware items. Append a concise dated entry to `context/TASK_LOG.md`.
- Inspect `git diff`, `git status`, and new files. Do not commit `.codex`, `.env`, credentials, API keys, personal information, raw conversations, desktop screenshots, virtual environments, caches, or machine-specific state.
- Create one focused commit. Run `git pull --ff-only` before pushing; if the remote advanced, stop instead of rebasing automatically.
- Push normally. Never force push.

After synchronization, report the branch, commit SHA, tests run, push result, and any unresolved conflicts.
