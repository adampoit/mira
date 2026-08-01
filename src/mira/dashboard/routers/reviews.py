# pyright: reportPrivateUsage=false
"""Live review trace routes."""

import asyncio
import logging

from fastapi import HTTPException

import mira.dashboard.runtime as runtime
from mira.dashboard.api import _app_db, router
from mira.dashboard.review_traces import store
from mira.providers.github import GitHubProvider

logger = logging.getLogger(__name__)


def _session_string(session: dict[str, object], key: str) -> str:
    value = session.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=500, detail="Review session data is invalid")
    return value


def _session_int(session: dict[str, object], key: str) -> int:
    value = session.get(key)
    if not isinstance(value, int):
        raise HTTPException(status_code=500, detail="Review session data is invalid")
    return value


@router.get("/api/reviews")
def list_review_sessions(limit: int = 200) -> list[dict[str, object]]:
    _ = store.reconcile_stale()
    return store.list_sessions(limit=max(1, min(limit, 500)))


@router.get("/api/reviews/{session_id}")
def get_review_session(session_id: str) -> dict[str, object]:
    try:
        _ = store.reconcile_stale()
        return store.get(session_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Review session not found") from None


@router.post("/api/reviews/{session_id}/retrigger", status_code=202)
async def retrigger_review(session_id: str) -> dict[str, str]:
    try:
        session = store.get(session_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Review session not found") from None

    owner = _session_string(session, "owner")
    repo_name = _session_string(session, "repo")
    pr_number = _session_int(session, "pr_number")
    pr_url = _session_string(session, "pr_url")

    active = store.find_live_session(owner, repo_name, pr_number)
    if active:
        raise HTTPException(
            status_code=409,
            detail="A review is already running for this pull request",
        )

    repo = _app_db.get_repo(owner, repo_name, platform="github")
    app_auth = runtime.github_app_auth
    if repo is None or not repo.installation_id or app_auth is None:
        raise HTTPException(
            status_code=409,
            detail="The GitHub App is not available for this repository",
        )

    token = await app_auth.get_installation_token(repo.installation_id)
    provider = GitHubProvider(token)
    bot_name = runtime.bot_name
    try:
        pr_info = await provider.get_pr_info(pr_url)
    except Exception:
        logger.exception("Could not refresh pull request before re-reviewing %s", pr_url)
        raise HTTPException(
            status_code=502,
            detail="The pull request could not be refreshed",
        ) from None

    # Recheck after the network calls so concurrent retriggers cannot both reserve a run.
    if store.find_live_session(owner, repo_name, pr_number):
        raise HTTPException(
            status_code=409,
            detail="A review is already running for this pull request",
        )

    retry_parent_id = session_id
    retry_parent = session
    for _ in range(100):
        replacement_id = retry_parent.get("replacement_id")
        if not isinstance(replacement_id, str):
            break
        try:
            retry_parent = store.get(replacement_id)
        except (FileNotFoundError, ValueError):
            break
        retry_parent_id = replacement_id

    previous_attempt = retry_parent.get("attempt")
    attempt = previous_attempt + 1 if isinstance(previous_attempt, int) else 2
    retry_request: dict[str, object] = {
        "platform": "github",
        "owner": owner,
        "repo": repo_name,
        "pr_number": pr_number,
        "pr_url": pr_info.url,
        "pr_title": pr_info.title,
        "head_sha": pr_info.head_sha,
        "bot_name": bot_name,
        "visibility": "private" if repo.private else "public",
        "auth_scope": f"installation:{repo.installation_id}",
    }
    replacement = store.start_details(
        owner=owner,
        repo=repo_name,
        pr_number=pr_number,
        pr_title=pr_info.title,
        pr_url=pr_info.url,
        head_sha=pr_info.head_sha,
        status="queued",
        retry_request=retry_request,
        attempt=attempt,
        retry_of=retry_parent_id,
    )
    store.link_replacement(retry_parent_id, replacement.session_id)

    async def run() -> None:
        from mira.platforms.handlers import run_pr_review

        try:
            await run_pr_review(
                provider=provider,
                owner=owner,
                repo=repo_name,
                number=pr_number,
                pr_url=pr_info.url,
                is_private=bool(repo.private),
                bot_name=bot_name,
                pr_title=pr_info.title,
                trace_session_id=replacement.session_id,
            )
        except Exception:
            logger.exception("Dashboard re-review failed for %s", pr_url)

    _ = asyncio.create_task(run())
    return {"status": "queued", "replacement_session_id": replacement.session_id}
