---
name: patchlane-health-check
description: >-
    Use when the user asks whether an existing Patchlane configuration, patch stack, or upstream sync is healthy, valid, or ready. This is a strictly read-only diagnostic. Do not use for initial setup, migration, sync conflict repair, or feature work.
---

# Patchlane Health Check

Check the configured composition without changing files, refs, worktrees, workspace metadata, Git configuration, or remotes.

## Procedure

1. Record the current branch, `git status --porcelain`, configured local and remote patch ref SHAs, worktree list, and remote URLs.
2. Read `.patchlane.yml`. Report the source, base branch, sync branch, ordered patch refs, CI workflow, and allowed workflows.
3. From the worktree containing that config, run both required checks exactly:

    ```bash
    npx patchlane doctor
    npx patchlane sync --dry-run
    ```

4. Explain each error and warning in terms of the affected config, lane, workflow, or source. Do not repair it unless the user later requests a separately approved repair workflow.
5. Recheck status, refs, worktrees, and remotes. Confirm that the diagnostic left them unchanged.

Do not substitute `patchlane status`, workspace inspection, `--help`, `bootstrap`, or `sync --skip-push` for either required command. Do not fetch unless the user explicitly authorizes updating tracking refs; the dry run performs the source reads it needs.
