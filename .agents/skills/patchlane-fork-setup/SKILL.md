---
name: patchlane-fork-setup
description: >-
    Use when initializing Patchlane in an unconfigured fork or moving existing fork-only changes into initial patch lanes. Do not use for health checks, upgrades, broken sync repair, or ordinary feature work. Inspect first, obtain approval for the exact local and remote refs, preserve the base branch, build independent patch lanes, validate with doctor and sync --dry-run, and publish only approved patch refs.
---

# Patchlane Fork Setup

Set up a fork as independent patch lanes without changing its existing base branch. Treat setup as a fragile migration: inventory first, approve one concrete mapping, execute in isolated worktrees, then validate the complete composition.

## 1. Inspect without mutation

Before approval, use only read-only commands to determine:

- the current branch and clean/dirty state;
- `origin`, `upstream`, the upstream default branch, and their current SHAs;
- commits and files present in the fork but absent from the selected upstream source;
- existing `patch/*` refs;
- every workflow filename, its YAML `name`, and its current triggers.

Do not run `patchlane init`, create branches or worktrees, edit files, commit, push, reset, or change remotes during inspection. Do not ask the user to choose information they already supplied.

## 2. Present one complete plan

Map each fork-owned file to a focused lane and name every ref that will be created and published. For the standard initial layout:

1. `patch/sync`: `.patchlane.yml`, generated Patchlane workflows, and installed Patchlane agent skills.
2. `patch/ci`: only the existing CI workflow adjustment needed to test the generated sync branch.
3. `patch/<product>`: the existing fork customization and product behavior.

Create every lane independently from the same resolved upstream source; patch lanes are not a branch stack. Use this default configuration when tracking `upstream/main`:

```yaml
version: 1
upstream: OWNER/REPOSITORY
source: branch:main
baseBranch: main
syncBranch: sync/integration
patchRefs:
    - patch/sync
    - patch/ci
    - patch/product
ciWorkflow: CI
allowedWorkflows:
    - ci.yml
```

Use the existing CI workflow's YAML `name`, not its filename, for `ciWorkflow`. Keep the exact existing base ref unchanged. Never invent a replacement base branch.

Ask for explicit approval to create the named local refs, make the mapped commits, and publish the named patch refs to the stated remote. Publishing a generated base or sync branch is not implied. If the plan changes, request approval again.

## 3. Execute the approved mapping

After approval, follow this order:

1. Record the original base SHA, source SHA, and fork-only file list.
2. Create a temporary worktree for each approved patch lane, each based directly on the source SHA. Keep the original worktree on its original branch.
3. In the `patch/sync` worktree, run `npx patchlane init` with every important value explicit:

    ```bash
    npx patchlane init \
      --upstream=OWNER/REPOSITORY \
      --source=branch:main \
      --base-branch=main \
      --sync-branch=sync/integration \
      --patch-refs=patch/sync,patch/ci,patch/product \
      --ci-workflow=CI \
      --allowed-workflows=ci.yml
    npx patchlane agents --dir .agents/skills
    ```

    Derive `OWNER/REPOSITORY` from the real upstream repository. A filesystem-only test mirror has no GitHub identity; use the harness-provided repository identity while leaving its remote URL unchanged.

4. In the `patch/ci` worktree, restore the original CI workflow and change only its trigger. Preserve its name and jobs, and cover normal pull requests plus pushes to both `main` and `sync/integration`.
5. In the product-lane worktree, restore only the mapped fork-owned product files from the recorded original base SHA.
6. Inspect each staged diff before committing. Verify that no lane contains another lane's files and that every lane is based directly on the source SHA.
7. Publish all and only the patch refs named in the approved plan. Never push `main`, the configured base, or `sync/integration`.

Use Patchlane's generated GitHub App wiring unless the user selected an existing token source. Do not create credentials, set repository variables or secrets, or dispatch workflows unless those external mutations were explicitly included in the approved plan. Never request secret values in chat.

## 4. Validate from `patch/sync`

Validation must use the worktree whose checked-out commit contains `.patchlane.yml`, not the unchanged base worktree:

```bash
npx patchlane doctor
npx patchlane sync --dry-run
```

Run both commands after all configured patch refs exist on `origin`, because Doctor verifies those refs. Fix errors and rerun both commands until they succeed; report warnings separately. A dry run must not create or publish `sync/integration`.

Do not substitute `bootstrap`, `sync --skip-push`, `status`, or help output for the required sync dry run. Run `bootstrap` only when publishing the initial generated sync was separately requested and approved.

Finally remove temporary worktrees, return to the original worktree, and verify:

- local and remote base SHAs equal their recorded values;
- `sync/integration` is absent from the remote;
- exactly the approved patch refs are present remotely;
- `patchRefs` has the approved order;
- the composed tree preserves the original CI and fork customization;
- the original worktree is clean and remotes are unchanged.

Summarize the source SHA, lane mapping and SHAs, published refspecs, Doctor result, dry-run result, warnings, and unchanged refs.
