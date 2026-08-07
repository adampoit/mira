---
name: patchlane-workspace
description: >-
    Use for an ordinary feature or behavior change in an already configured Patchlane fork. Inspect first; after approval, create a composed workspace for one configured lane, edit and commit only there, then validate with workspace status --json and workspace land --dry-run. Do not use for initial setup, health checks, migration, or broken sync repair.
---

# Patchlane Composed Workspace

Develop against the complete composed fork while keeping raw configured lanes and the source worktree untouched.

## Candidate workflow

1. **Inspect without mutation.** Read `.patchlane.yml`, relevant files, and lane history. Select an existing configured lane appropriate for the change. Inspect another ref with `git show` or `git diff`; never check it out. Do not create a branch, worktree, workspace, or commit yet.
2. **Request candidate approval.** Present the focused change, selected lane, expected workspace, tests, and dry-run validation. Ask for approval to create, modify, and commit an isolated candidate. This approval does not authorize landing or pushing.
3. **Create the composed workspace.** The first mutating command after approval must be:

    ```bash
    npx patchlane workspace create --lane <selected-lane>
    ```

    Run it from the configured source worktree. Change into the reported path and immediately run:

    ```bash
    npx patchlane workspace status --json
    ```

4. **Work only in the reported workspace.** Inspect existing behavior across the full composition, make the focused change, run normal tests, and create linear reviewable commits. Never edit the source checkout, check out a raw configured lane, or substitute a regular Git/Jujutsu branch or worktree. If workspace creation fails, stop rather than falling back.
5. **Validate before stopping.** Ensure the workspace is clean, then run:

    ```bash
    npx patchlane workspace status --json
    npx patchlane workspace land --dry-run
    ```

    Fix stale-lane, projection-conflict, or round-trip-mismatch errors instead of bypassing them. Report candidate commits and the dry-run result. Do not land or push.

If any source mutation occurred before approval, stop and disclose it; do not hide it with reset, checkout, or branch movement.

## Local projection

After a successful dry run, show the selected lane and candidate commits and request separate approval to update that local configured lane. Only then run `npx patchlane workspace land` without `--push`. Verify that only the selected local lane changed and every remote ref stayed unchanged.

## Publication and cleanup

Local projection does not authorize publication. Show the exact remote ref update and obtain explicit approval before `npx patchlane workspace land --push`.

Workspace removal is also a mutation. Remove it only when requested or included in an approved cleanup plan, after confirming there are no unlanded changes. Use `--force` only to intentionally discard reviewed dirty or unlanded work.
