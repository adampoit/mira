# pyright: reportAny=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mira.dashboard import runtime, review_recovery
from mira.dashboard.review_recovery import RecoveryPolicy
from mira.dashboard.review_traces import TraceStore
from mira.models import PRInfo


def _pr_info(head_sha: str = "head") -> PRInfo:
    return PRInfo(
        title="Recover this review",
        description="",
        base_branch="main",
        head_branch="feature",
        url="https://github.com/owner/repo/pull/42",
        number=42,
        owner="owner",
        repo="repo",
        head_sha=head_sha,
    )


def _retry_request(head_sha: str = "old-head") -> dict[str, object]:
    return {
        "platform": "github",
        "owner": "owner",
        "repo": "repo",
        "pr_number": 42,
        "pr_url": "https://github.com/owner/repo/pull/42",
        "pr_title": "Recover this review",
        "head_sha": head_sha,
        "bot_name": "mira-bot",
        "visibility": "public",
        "auth_scope": "installation:123",
    }


@pytest.mark.asyncio
async def test_restart_finalizes_provider_check_and_stays_manual_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = TraceStore(tmp_path, instance_id="previous")
    trace = previous.start_details(
        owner="owner",
        repo="repo",
        pr_number=42,
        pr_title="Recover this review",
        pr_url="https://github.com/owner/repo/pull/42",
        head_sha="old-head",
        retry_request=_retry_request(),
    )
    assert trace.set_provider_check("github", 99)
    current = TraceStore(tmp_path, instance_id="current")

    auth = SimpleNamespace(get_token=AsyncMock(return_value="token"))
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=_pr_info("old-head")),
        complete_review_check=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "github_app_auth", auth)

    _ = await review_recovery.recover_previous_reviews(
        review_store=current,
        provider_factory=lambda _platform, _token: provider,
    )

    session = current.get(trace.session_id)
    assert session["status"] == "interrupted"
    assert session["recovery_status"] == "manual_only"
    assert session["replacement_id"] is None
    check = session["provider_check"]
    assert isinstance(check, dict)
    assert check["status"] == "interrupted"
    auth.get_token.assert_awaited_once_with(123)
    provider.complete_review_check.assert_awaited_once_with(
        _pr_info("old-head"),
        99,
        succeeded=False,
        summary="Mira was interrupted before this review completed.",
    )


@pytest.mark.asyncio
async def test_automatic_recovery_refreshes_head_and_runs_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = TraceStore(tmp_path, instance_id="previous")
    original = previous.start_details(
        owner="owner",
        repo="repo",
        pr_number=42,
        pr_title="Recover this review",
        pr_url="https://github.com/owner/repo/pull/42",
        head_sha="old-head",
        retry_request=_retry_request(),
    )
    current = TraceStore(tmp_path, instance_id="current")
    auth = SimpleNamespace(get_token=AsyncMock(return_value="token"))
    provider = SimpleNamespace(get_pr_info=AsyncMock(return_value=_pr_info("new-head")))
    monkeypatch.setattr(runtime, "github_app_auth", auth)
    calls: list[dict[str, object]] = []

    async def run_review(**kwargs: object) -> None:
        calls.append(kwargs)
        session_id = kwargs["trace_session_id"]
        assert isinstance(session_id, str)
        assert current.finish(session_id, "completed", owner_id=current.instance_id)

    _ = await review_recovery.recover_previous_reviews(
        policy=RecoveryPolicy(enabled=True, max_retries=1, base_delay=0, max_delay=0),
        review_store=current,
        provider_factory=lambda _platform, _token: provider,
        review_runner=run_review,
    )
    tasks = list(current._recovery_tasks)  # pyright: ignore[reportPrivateUsage]
    if tasks:
        await tasks[0]

    source = current.get(original.session_id)
    replacement_id = source["replacement_id"]
    assert isinstance(replacement_id, str)
    replacement = current.get(replacement_id)
    assert replacement["status"] == "completed"
    assert replacement["attempt"] == 2
    assert replacement["automatic_recovery_attempts"] == 1
    assert replacement["retry_of"] == original.session_id
    assert replacement["head_sha"] == "new-head"
    assert len(calls) == 1
    assert calls[0]["auth_scope"] == 123
    assert calls[0]["pr_url"] == _pr_info("new-head").url


@pytest.mark.asyncio
async def test_legacy_trace_is_manual_only_even_when_recovery_is_enabled(
    tmp_path: Path,
) -> None:
    previous = TraceStore(tmp_path, instance_id="previous")
    trace = previous.start(_pr_info())
    current = TraceStore(tmp_path, instance_id="current")

    _ = await review_recovery.recover_previous_reviews(
        policy=RecoveryPolicy(enabled=True, max_retries=1, base_delay=0, max_delay=0),
        review_store=current,
    )

    session = current.get(trace.session_id)
    assert session["status"] == "interrupted"
    assert session["recovery_status"] == "manual_only"
    assert "incomplete" in str(session["recovery_detail"])
    assert session["replacement_id"] is None
