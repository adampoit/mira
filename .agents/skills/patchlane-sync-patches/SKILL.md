---
name: patchlane-sync-patches
description: Update patch branches in a Patchlane-managed fork so `npx patchlane sync` applies cleanly again. Use when upstream changes break patch application, a sync run reports conflicts or missing patch refs, or the agent needs to restack patch branches while preserving Patchlane workflow structure.
---

# Patchlane Patch Refresh

Start by reproducing the problem instead of guessing. Read `.patchlane.yml`, resolve its explicit upstream `source` and ordered `patchRefs`, and run or review `npx patchlane sync --dry-run` so the first failing patch branch is explicit without resetting the local sync branch.

Use this workflow:

1. Read `.patchlane.yml` or legacy workflow environment variables to confirm the upstream repository, explicit source, generated base, sync branch, and ordered patch refs.
2. Identify the first failing patch branch or missing ref from the sync output.
3. Update only the failing branch first. Rebase, restack, or recreate it on the current upstream base so its change is intentional and minimal.
4. If later patch branches depend on earlier ones, restack them in order after fixing the first conflict.
5. Keep workflow and CI adjustments on patch branches rather than the generated base branch.
6. Re-run `npx patchlane sync --dry-run` after each meaningful fix until the full stack applies cleanly.

Use these heuristics:

- Prefer the smallest patch update that preserves the fork's intended behavior.
- Split accidental unrelated edits out of a patch branch while repairing it.
- If upstream absorbed part of a patch, drop the redundant delta instead of preserving it mechanically.
- If a patch branch has grown broad or unclear, recreate it from a clean base with a tighter scope.
- Preserve patch ordering unless there is a concrete reason to change it.

Watch for common failure modes:

- missing patch branches on `origin`
- workflow files living on the promoted base branch instead of a patch branch
- patches created from the wrong upstream tag or branch
- later patch branches silently depending on files introduced by an earlier patch

Finish by summarizing:

- which patch branch or branches changed
- whether patch ordering changed
- whether `npx patchlane sync --dry-run` now succeeds cleanly
- any remaining follow-up work before automation is re-enabled
