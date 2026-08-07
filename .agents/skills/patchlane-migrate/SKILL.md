---
name: patchlane-migrate
description: >-
    Use when upgrading an already configured Patchlane fork, migrating legacy Patchlane workflows or environment variables, or adopting a newer Patchlane configuration schema. Preserve existing branch names and behavior unless approved. Do not use for first-time setup, health checks, sync conflict repair, or feature work.
---

# Patchlane Migration

Migrate an existing installation incrementally. Do not treat it as a new fork setup.

## Procedure

1. Inspect `.patchlane.yml`, legacy workflow variables, configured refs, workflow names and triggers, schedules, token wiring, and repository-specific workflow changes without mutation.
2. Fetch and read the migration guide for the target version:

    ```text
    https://raw.githubusercontent.com/adampoit/patchlane/main/docs/migrations.md
    ```

    Use `vNext` for an unreleased target. Do not rely on remembered migration steps.

3. Present the required config and workflow changes, exact local refs to update, exact remote refs to publish, and validation commands. Obtain approval before mutation and separate external credential changes from repository changes.
4. Update the existing `patch/sync` lane through a composed workspace when composition is healthy. Preserve source behavior, base and sync branch names, patch order, CI workflow name, schedule, and authentication source unless the approved migration requires changing them.
5. Avoid `patchlane init --force` unless replacement of generated workflows is intentional and approved. Prefer focused edits that preserve local customization.
6. Run `npx patchlane doctor` and `npx patchlane sync --dry-run`. Fix errors and review warnings before proposing publication.
7. Show the exact remote refspec and obtain separate publication approval if it was not part of the approved migration plan. Roll the migration forward through the configured tested sync flow.

Never force-update the generated base or publish generated integration output merely because patch configuration changed.
