# pyright: reportAny=false, reportExplicitAny=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false
# Provider and authentication adapters are intentionally duck-typed here.
"""Restart recovery for interrupted review attempts."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from mira.dashboard import runtime
from mira.dashboard.review_traces import (
    ReviewSession,
    TraceStore,
    retry_request_is_complete,
    store,
)
from mira.models import PRInfo
from mira.providers import create_provider

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[str, str], Any]
ReviewRunner = Callable[..., Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RecoveryPolicy:
    """Limits for automatic recovery after a process restart.

    Automatic recovery is deliberately disabled by default. When enabled,
    ``max_retries`` counts replacement attempts, not the original review.
    """

    enabled: bool = False
    max_retries: int = 1
    base_delay: float = 30.0
    max_delay: float = 300.0

    @classmethod
    def from_environment(cls) -> RecoveryPolicy:
        enabled = _env_bool("MIRA_REVIEW_AUTO_RECOVERY", False)
        max_retries = _env_int("MIRA_REVIEW_MAX_AUTO_RETRIES", 1, minimum=0)
        base_delay = _env_float("MIRA_REVIEW_RECOVERY_BACKOFF_SECONDS", 30.0)
        max_delay = max(base_delay, _env_float("MIRA_REVIEW_RECOVERY_MAX_BACKOFF_SECONDS", 300.0))
        return cls(
            enabled=enabled,
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return default


def _request(session: ReviewSession) -> Mapping[str, object] | None:
    request = session.get("retry_request")
    return request if isinstance(request, Mapping) else None


def _request_string(request: Mapping[str, object], key: str) -> str:
    value = request.get(key)
    return value if isinstance(value, str) else ""


def _request_number(request: Mapping[str, object], key: str) -> int:
    value = request.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _scope_for_token(request: Mapping[str, object], platform: str) -> str | int | None:
    scope = request.get("auth_scope")
    if not isinstance(scope, str):
        return scope if isinstance(scope, int) else None
    if platform == "github" and scope.startswith("installation:"):
        value = scope.removeprefix("installation:")
        try:
            return int(value)
        except ValueError:
            return None
    return scope


async def _provider_for_request(
    request: Mapping[str, object], provider_factory: ProviderFactory
) -> Any:
    platform = _request_string(request, "platform")
    auth = {
        "github": runtime.github_app_auth,
        "gitlab": runtime.gitlab_auth,
        "forgejo": runtime.forgejo_auth,
    }.get(platform)
    if auth is None:
        raise RuntimeError(f"No {platform} authentication is configured for review recovery")

    scope = _scope_for_token(request, platform)
    get_token = getattr(auth, "get_token", None)
    if get_token is not None:
        if platform == "github":
            token = await get_token(scope)
        else:
            token = await get_token()
    else:
        get_installation_token = getattr(auth, "get_installation_token", None)
        if get_installation_token is None or not isinstance(scope, int):
            raise RuntimeError(f"Authentication for {platform} cannot reacquire a review token")
        token = await get_installation_token(scope)
    return provider_factory(platform, token)


async def _finalize_provider_check(
    session: ReviewSession,
    *,
    review_store: TraceStore,
    provider_factory: ProviderFactory,
) -> None:
    check = session.get("provider_check")
    if not isinstance(check, Mapping):
        return
    if check.get("status") not in {"creating", "in_progress"}:
        return
    check_id = check.get("check_id")
    request = _request(session)
    if request is None or not isinstance(check_id, int | str):
        _ = review_store.update_provider_check(
            cast(str, session.get("id")),
            status="unresolved",
            error="The interrupted provider check could not be identified.",
        )
        return

    session_id = session.get("id")
    if not isinstance(session_id, str):
        return
    try:
        provider = await _provider_for_request(request, provider_factory)
        pr_url = _request_string(request, "pr_url")
        pr_info = await provider.get_pr_info(pr_url)
        complete_check = getattr(provider, "complete_review_check", None)
        if complete_check is None:
            raise RuntimeError("Provider does not support completing review checks")
        await complete_check(
            pr_info,
            check_id,
            succeeded=False,
            summary="Mira was interrupted before this review completed.",
        )
    except Exception as exc:
        logger.warning("Could not finalize interrupted review check %s: %s", check_id, exc)
        _ = review_store.update_provider_check(
            session_id,
            status="finalization_failed",
            error=str(exc) or type(exc).__name__,
        )
        return
    _ = review_store.update_provider_check(session_id, status="interrupted")


def _should_schedule(session: ReviewSession, policy: RecoveryPolicy) -> bool:
    request = _request(session)
    if request is None or not retry_request_is_complete(request):
        return False
    if session.get("replacement_id"):
        return False
    count = session.get("automatic_recovery_attempts", 0)
    return isinstance(count, int) and count < policy.max_retries


def _delay(session: ReviewSession, policy: RecoveryPolicy) -> float:
    count = session.get("automatic_recovery_attempts", 0)
    retry_number = count if isinstance(count, int) else 0
    return min(policy.max_delay, policy.base_delay * (2**retry_number))


async def _recover_session(
    session: ReviewSession,
    *,
    policy: RecoveryPolicy,
    review_store: TraceStore,
    provider_factory: ProviderFactory,
    review_runner: ReviewRunner | None,
    sleep: Sleep,
) -> None:
    session_id = session.get("id")
    request = _request(session)
    if not isinstance(session_id, str) or request is None:
        return
    await sleep(_delay(session, policy))

    attempt = session.get("attempt", 1)
    attempt_number = attempt + 1 if isinstance(attempt, int) else 2
    recovery_count = session.get("automatic_recovery_attempts", 0)
    recovery_number = recovery_count + 1 if isinstance(recovery_count, int) else 1
    replacement = review_store.start_details(
        owner=_request_string(request, "owner"),
        repo=_request_string(request, "repo"),
        pr_number=_request_number(request, "pr_number"),
        pr_title=_request_string(request, "pr_title"),
        pr_url=_request_string(request, "pr_url"),
        head_sha=_request_string(request, "head_sha"),
        status="queued",
        retry_request=request,
        attempt=attempt_number,
        retry_of=session_id,
        automatic_recovery_attempts=recovery_number,
    )
    review_store.link_replacement(session_id, replacement.session_id)
    _ = review_store.record_recovery(session_id, "queued", "Automatic review recovery scheduled.")

    try:
        provider = await _provider_for_request(request, provider_factory)
        pr_info = await provider.get_pr_info(_request_string(request, "pr_url"))
        if isinstance(pr_info, PRInfo):
            replacement.update_context(pr_info)
        else:
            raise RuntimeError("Provider returned invalid pull request metadata")

        if review_runner is None:
            from mira.platforms.handlers import run_pr_review

            review_runner = run_pr_review

        await review_runner(
            provider=provider,
            owner=pr_info.owner,
            repo=pr_info.repo,
            number=pr_info.number,
            pr_url=pr_info.url,
            is_private=_request_string(request, "visibility") == "private",
            bot_name=_request_string(request, "bot_name"),
            platform=_request_string(request, "platform"),
            pr_title=pr_info.title,
            trace_session_id=replacement.session_id,
            auth_scope=_scope_for_token(request, _request_string(request, "platform")),
        )
        if review_store.get(replacement.session_id).get("status") == "queued":
            _ = review_store.finish(
                replacement.session_id,
                "interrupted",
                "Automatic recovery could not claim the review slot.",
                owner_id=review_store.instance_id,
            )
    except asyncio.CancelledError:
        _ = review_store.finish(
            replacement.session_id,
            "interrupted",
            "Automatic review recovery was cancelled during shutdown.",
            owner_id=review_store.instance_id,
        )
        raise
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        _ = review_store.finish(
            replacement.session_id,
            "failed",
            f"Automatic review recovery failed: {detail}",
            owner_id=review_store.instance_id,
        )
        _ = review_store.record_recovery(session_id, "failed", detail)
        logger.exception("Automatic recovery failed for review session %s", session_id)


async def recover_previous_reviews(
    *,
    policy: RecoveryPolicy | None = None,
    review_store: TraceStore | None = None,
    provider_factory: ProviderFactory = create_provider,
    review_runner: ReviewRunner | None = None,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Reconcile old sessions and schedule bounded automatic replacements."""
    active_store = review_store or store
    effective_policy = policy or RecoveryPolicy.from_environment()
    sessions = active_store.interrupt_previous_instances()
    for session in sessions:
        await _finalize_provider_check(
            session,
            review_store=active_store,
            provider_factory=provider_factory,
        )

    if not effective_policy.enabled:
        for session in sessions:
            session_id = session.get("id")
            if isinstance(session_id, str):
                _ = active_store.record_recovery(
                    session_id,
                    "manual_only",
                    "Automatic recovery is disabled; manual retrigger is available.",
                )
        return len(sessions)

    scheduled = 0
    for session in sessions:
        session_id = session.get("id")
        if not isinstance(session_id, str):
            continue
        if not _should_schedule(session, effective_policy):
            detail = (
                "Automatic recovery skipped because retry metadata is incomplete."
                if not retry_request_is_complete(session.get("retry_request"))
                else "Automatic recovery attempt limit reached."
            )
            _ = active_store.record_recovery(session_id, "manual_only", detail)
            continue
        task = asyncio.create_task(
            _recover_session(
                session,
                policy=effective_policy,
                review_store=active_store,
                provider_factory=provider_factory,
                review_runner=review_runner,
                sleep=sleep,
            )
        )
        active_store.register_recovery_task(task)
        scheduled += 1

    if scheduled:
        logger.info("Scheduled %d interrupted review replacement(s)", scheduled)
    return len(sessions)
