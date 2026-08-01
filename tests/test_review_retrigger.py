from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import mira.dashboard.routers.reviews as reviews_router
import mira.dashboard.runtime as dashboard_runtime
from mira.dashboard.review_traces import TraceStore
from mira.models import PRInfo


def _pr_info(head_sha: str = "new-head") -> PRInfo:
    return PRInfo(
        owner="miracodeai",
        repo="mira",
        number=42,
        title="Reliable retries",
        description="",
        base_branch="main",
        head_branch="retries",
        url="https://github.com/miracodeai/mira/pull/42",
        head_sha=head_sha,
    )


@pytest.mark.asyncio
async def test_live_session_blocks_retrigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_store = TraceStore(tmp_path, instance_id="current", lease_timeout=60)
    trace = trace_store.start(_pr_info())
    monkeypatch.setattr(reviews_router, "store", trace_store)

    with pytest.raises(HTTPException) as exc_info:
        _ = await reviews_router.retrigger_review(trace.session_id)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_retrigger_creates_linked_attempt_with_refreshed_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_store = TraceStore(tmp_path, instance_id="current", lease_timeout=0.1)
    original = trace_store.start(_pr_info("old-head"))
    original_path = tmp_path / f"{original.session_id}.json"
    original_session = cast(
        dict[str, object], json.loads(original_path.read_text(encoding="utf-8"))
    )
    original_session["heartbeat_at"] = time.time() - 1
    _ = original_path.write_text(json.dumps(original_session), encoding="utf-8")
    monkeypatch.setattr(reviews_router, "store", trace_store)

    repo = SimpleNamespace(installation_id=123, private=False)

    def get_repo(*_args: object, **_kwargs: object) -> object:
        return repo

    app_db = SimpleNamespace(get_repo=get_repo)
    app_auth = SimpleNamespace(get_installation_token=AsyncMock(return_value="token"))
    provider = SimpleNamespace(get_pr_info=AsyncMock(return_value=_pr_info("new-head")))

    def provider_factory(_token: str) -> object:
        return provider

    monkeypatch.setattr(reviews_router, "_app_db", app_db)
    monkeypatch.setattr(dashboard_runtime, "github_app_auth", app_auth)
    monkeypatch.setattr(dashboard_runtime, "bot_name", "mira-bot")
    monkeypatch.setattr(reviews_router, "GitHubProvider", provider_factory)

    def discard_task(coroutine: Coroutine[object, object, None]) -> None:
        coroutine.close()

    monkeypatch.setattr(asyncio, "create_task", discard_task)

    response = await reviews_router.retrigger_review(original.session_id)

    replacement_id = response["replacement_session_id"]
    replacement = trace_store.get(replacement_id)
    source = trace_store.get(original.session_id)
    assert response["status"] == "queued"
    assert replacement["status"] == "queued"
    assert replacement["attempt"] == 2
    assert replacement["head_sha"] == "new-head"
    assert replacement["retry_of"] == original.session_id
    assert source["status"] == "interrupted"
    assert source["replacement_id"] == replacement_id
    retry_request = replacement["retry_request"]
    assert isinstance(retry_request, dict)
    assert retry_request["head_sha"] == "new-head"
    assert "token" not in retry_request
