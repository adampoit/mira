---
name: patchlane-fork-setup
description: Set up or migrate a GitHub fork to use Patchlane upstream sync automation. Use when a repository is adopting Patchlane, upgrading legacy workflow configuration, choosing an upstream source, creating patch branches, adding workflows, or bootstrapping the first tested sync.
---

# Patchlane Fork Setup

Inspect the fork before changing anything. Confirm the default branch, remotes, existing workflows, fork-only commits, and existing `patch/*` branches.

Treat the promoted base branch as generated output. Keep fork-owned product changes, Patchlane configuration, agent skills, and workflows on focused patch branches.

## Confirm the plan

Ask the user which upstream source to track. Do not infer this from version files or from whichever branch is currently checked out.

- `release:latest` for the latest stable GitHub release
- `release:prerelease` for the latest prerelease
- `release:<regex>` for matching release tags
- `branch:<ref>` for an upstream development branch

Resolve and show the source tag or branch and commit SHA. Before pushing or rewriting branches, show the complete plan and get confirmation. Include the source, base branch, sync branch, ordered patch refs, existing CI workflow name, and any force-pushes required.

## Configure the fork

1. Default the generated base to `main` and the integration branch to `sync/integration` unless the repository uses different conventions.
2. Create each patch branch independently from the resolved upstream source. Never create `patch/sync` from `patch/product`, or another patch branch, unless that dependency is intentional and explicitly allowed.
3. Prefer the order `patch/sync`, `patch/ci`, then product-specific patches. Foundational changes must precede patches that depend on them.
4. Put `.patchlane.yml`, Patchlane workflows, and installed `.agents/skills` on `patch/sync`.
5. Put only the existing CI trigger adjustment on `patch/ci`. Preserve the existing workflow's `name`; configure `ciWorkflow` and the promotion workflow to reference that exact name.
6. Use `npx patchlane init` to generate `.patchlane.yml` and pinned workflow files when practical, then adapt rather than replace existing repository conventions.
7. Ensure fork CI covers normal pull requests plus pushes to both the generated base and sync branches.

Use the bundled assets as invariants when adapting workflows:

- `assets/sync-upstream.yml` exposes safe workflow-dispatch overrides and runs sync with write permission.
- `assets/fork-ci.yml` demonstrates the required branch triggers.
- `assets/promote-tested-sync.yml` promotes only a successful sync-branch `workflow_run` and passes its exact `head_sha`.

## Migrate an existing Patchlane fork

If Patchlane workflows or patch branches already exist, migrate incrementally instead of treating the repository as a new installation.

1. Read the existing workflow environment and map `UPSTREAM_OWNER`, `UPSTREAM_REPO`, `RELEASE_SELECTOR` or `UPSTREAM_REF`, `BASE_BRANCH`, `SYNC_BRANCH`, and `PATCH_REFS` into `.patchlane.yml`.
2. Preserve the configured source behavior, branch names, patch order, CI workflow name, schedule, and repository-specific workflow changes unless the user approves changing them.
3. Add the config and adapted workflows to the existing `patch/sync` branch. Do not use `patchlane init --force` unless replacing those workflows is intentional.
4. Run `doctor` and `sync --dry-run`, then show the migration plan before pushing rewritten patch branches.
5. If sync and promotion workflows are already active on the generated base, roll the migration forward through the existing tested sync flow. Use initial bootstrap only when the promotion workflow is absent from the base.
6. Follow the [Patchlane 0.4 migration guide](https://github.com/adampoit/patchlane/blob/v0.4.0/docs/migrating-to-0.4.md) for the full rollout sequence.

## Validate and bootstrap

Run `npx patchlane doctor` after creating and pushing the patch branches. Fix all errors and review warnings.

Use `npx patchlane sync --dry-run` for local validation. Do not use local `--no-push` as a substitute: no-push creates or resets the local sync branch, while dry-run leaves the working tree alone.

The workflows do not exist on the default branch before the first promotion. Bootstrap explicitly:

1. Run `npx patchlane bootstrap` to validate without publishing.
2. After user approval, run `npx patchlane bootstrap --publish` and wait for the configured CI workflow.
3. Promote the exact successful SHA printed by bootstrap, or use `npx patchlane bootstrap --wait` to wait and promote automatically.
4. Confirm the generated base is rooted at the selected source and that future workflows are active.

After bootstrap, a remote no-push test can be dispatched safely from the default branch.

## Finish

Summarize:

- selected source and resolved tag/branch SHA
- base and sync branches
- ordered patch refs and their bases
- files and workflows added or updated
- doctor and dry-run results
- bootstrap CI and promotion results
