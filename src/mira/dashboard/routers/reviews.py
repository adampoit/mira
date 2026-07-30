"""Live review trace routes."""

import asyncio
import logging

from fastapi import HTTPException

import mira.dashboard.runtime as runtime
from mira.dashboard.api import _app_db, router  # pyright: ignore[reportPrivateUsage]
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
    return store.list_sessions(limit=max(1, min(limit, 500)))


@router.get("/api/reviews/{session_id}")
def get_review_session(session_id: str) -> dict[str, object]:
    try:
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
    pr_title = _session_string(session, "pr_title")

    active = next(
        (
            review
            for review in store.list_sessions(limit=500)
            if review["status"] == "running"
            and review["owner"] == owner
            and review["repo"] == repo_name
            and review["pr_number"] == pr_number
        ),
        None,
    )
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

    async def run() -> None:
        from mira.platforms.handlers import run_pr_review

        try:
            await run_pr_review(
                provider=provider,
                owner=owner,
                repo=repo_name,
                number=pr_number,
                pr_url=pr_url,
                is_private=bool(repo.private),
                bot_name=bot_name,
                pr_title=pr_title,
            )
        except Exception:
            logger.exception("Dashboard re-review failed for %s", pr_url)

    _ = asyncio.create_task(run())
    return {"status": "started"}
