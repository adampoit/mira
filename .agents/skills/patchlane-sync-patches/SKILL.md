---
name: patchlane-sync-patches
description: >-
    Use when an existing Patchlane sync fails because a configured patch lane conflicts with current upstream, has an invalid base, or no longer applies. Diagnose read-only, obtain separate approvals for an isolated candidate and local projection, and leave remote refs unchanged. Do not use for setup, migration, health checks, or ordinary feature work.
---

# Patchlane Sync Repair

Repair sync through four distinct authorization phases. Never collapse or infer a later phase from an earlier one. The user's initial request to fix sync is not approval to create a candidate, change a configured ref, or push.

## 1. Diagnose without mutation

Before asking for approval:

1. Read `.patchlane.yml` or legacy workflow environment variables. Confirm the upstream source, generated base, sync branch, and ordered `patchRefs`.
2. Fetch upstream only when needed, then run or review `npx patchlane sync --dry-run` to identify the first failing lane.
3. Inspect configured lanes with read-only commands such as `git show`, `git diff`, `git log`, and `git ls-tree`.
4. Present a concise candidate-repair plan and ask for approval to create and validate that isolated candidate.

During this phase, do not run `workspace create`, `git clone`, or `git worktree`; do not create any candidate directory, check out or switch to a configured lane, edit files, commit, reset, rebase, amend, or update any configured ref. Diagnosis is read-only apart from an upstream fetch that only updates upstream tracking refs. Do not manually reproduce the conflict in a disposable clone before candidate approval. A request for a repair does not waive this boundary.

## 2. Build an isolated candidate after candidate approval

Candidate approval authorizes candidate creation and validation only. It does not authorize changing any configured lane.

1. The first candidate command after approval must be `npx patchlane workspace create --lane <first-failing-lane>` so the complete composed fork remains visible. Record its result. Do not skip directly to a clone or ordinary branch.
2. Work and commit only in the reported workspace. Never check out, reset, amend, or commit on a configured lane in the source repository.
3. If workspace creation fails because composition is broken, leave the source repository untouched. Only then build the candidate in a disposable clone whose refs cannot affect the source repository; do not substitute a branch or shared-ref worktree in the source repository.
4. Treat configured patch refs as independent lanes, not stacked branches. Recreate the failing lane directly from the resolved current upstream source and replay only that lane's fork-owned commits or intentional delta. Never cherry-pick an earlier patch lane into the failing lane candidate.
5. Resolve conflicts inside the failing lane. For a modify/delete conflict where that lane intentionally removes an obsolete upstream file, preserve the deletion in the rebased failing-lane commit; do not move the deletion into an earlier successful lane.
6. Repair only the first failing lane. Do not rewrite an earlier successful lane to make the conflict disappear. If another configured lane truly must change, stop and present a revised plan requiring separate candidate and projection approvals.
7. Preserve the fork's intended behavior, remove deltas upstream has absorbed, and keep workflow changes on patch lanes rather than the generated base.
8. Validate the candidate in isolation. For a composed workspace, run `npx patchlane workspace status --json` and `npx patchlane workspace land --dry-run`. In a disposable clone, point only the clone's `refs/remotes/origin/<failing-lane>` at the candidate, keep a neutral `candidate/*` local branch name, and run `npx patchlane sync --dry-run` against the real upstream source. Because sync refreshes `origin`, use a disposable local bare repository as the clone's `origin` when needed, seed only its temporary `main` and patch refs, and never push to the source repository's origin. Create the disposable root with a clearly recognizable temporary prefix, for example `DISPOSABLE=$(mktemp -d /tmp/patchlane-repair-XXXXXX)`, then define quoted `CLONE` and `BARE` paths beneath it and seed refs only with commands such as `git push "$BARE" ...`. Never use `git --git-dir="$BARE" update-ref` or a literal or relative path that could be confused with the real origin. Do not create or force-update `refs/heads/<configured-lane>` even inside the clone.

After validation, report the candidate commit, focused diff, test results, dry-run result, target local ref, current target SHA, and proposed new SHA. Then stop and ask for separate approval to project that candidate onto the local configured failing ref.

## 3. Project only after separate projection approval

Projection approval authorizes exactly one local configured ref update. It does not authorize a remote write.

1. Confirm the target ref still equals the previously reported SHA. If it moved, stop and revalidate instead of overwriting it.
2. Project the already reviewed candidate only onto the approved failing lane. Use `workspace land` without `--push` for a workspace candidate; for a disposable clone, transfer the candidate commit and update the target ref atomically against its expected old SHA.
3. Leave every other configured local ref unchanged. Do not check out the target lane in the original worktree.
4. Re-run `npx patchlane sync --dry-run` from the configured repository and report whether the complete stack now applies.
5. Leave the original worktree clean and the remote refs unchanged.

If validation reveals that another lane needs repair, return to the candidate phase and obtain new approvals. Do not broaden the existing approval.

## 4. Publish only after publish approval

Never treat candidate or projection approval as permission to push. Show the exact remote, refspec, and whether a force update is required, then obtain separate explicit publish approval before any push or `--push` command.

Finish by summarizing:

- the diagnosed failing lane
- the candidate and projected commit SHAs
- which local ref changed and which refs remained unchanged
- whether patch ordering changed
- whether `npx patchlane sync --dry-run` succeeds
- whether all remote refs remain unchanged
