from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mira.platforms.handlers import _run_review_with_check


@pytest.mark.asyncio
async def test_review_check_completes_successfully() -> None:
    pr_info = SimpleNamespace(url="https://example.test/pull/1")
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=pr_info),
        create_review_check=AsyncMock(return_value=42),
        complete_review_check=AsyncMock(),
    )
    result = SimpleNamespace(comments=[])
    engine = SimpleNamespace(review_pr=AsyncMock(return_value=result))

    assert await _run_review_with_check(provider, engine, pr_info.url) is result
    provider.complete_review_check.assert_awaited_once_with(
        pr_info,
        42,
        succeeded=True,
        summary="Mira completed its review. See the pull request conversation for findings.",
    )


@pytest.mark.asyncio
async def test_review_check_fails_when_review_raises() -> None:
    pr_info = SimpleNamespace(url="https://example.test/pull/1")
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=pr_info),
        create_review_check=AsyncMock(return_value=42),
        complete_review_check=AsyncMock(),
    )
    engine = SimpleNamespace(review_pr=AsyncMock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        await _run_review_with_check(provider, engine, pr_info.url)

    provider.complete_review_check.assert_awaited_once_with(
        pr_info,
        42,
        succeeded=False,
        summary="Mira could not complete the review: boom",
    )


@pytest.mark.asyncio
async def test_cancelled_review_completes_check_as_interrupted() -> None:
    pr_info = SimpleNamespace(url="https://example.test/pull/1")
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=pr_info),
        create_review_check=AsyncMock(return_value=42),
        complete_review_check=AsyncMock(),
    )
    engine = SimpleNamespace(review_pr=AsyncMock(side_effect=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await _run_review_with_check(provider, engine, pr_info.url)

    provider.complete_review_check.assert_awaited_once_with(
        pr_info,
        42,
        succeeded=False,
        summary="Mira was interrupted before this review completed.",
    )


@pytest.mark.asyncio
async def test_review_runs_when_provider_has_no_check_capability() -> None:
    pr_info = SimpleNamespace(url="https://example.test/pull/1")
    provider = SimpleNamespace(get_pr_info=AsyncMock(return_value=pr_info))
    result = object()
    engine = SimpleNamespace(review_pr=AsyncMock(return_value=result))

    assert await _run_review_with_check(provider, engine, pr_info.url) is result


@pytest.mark.asyncio
async def test_check_api_failures_do_not_fail_review() -> None:
    pr_info = SimpleNamespace(url="https://example.test/pull/1")
    provider = SimpleNamespace(
        get_pr_info=AsyncMock(return_value=pr_info),
        create_review_check=AsyncMock(side_effect=RuntimeError("checks unavailable")),
        complete_review_check=AsyncMock(),
    )
    result = object()
    engine = SimpleNamespace(review_pr=AsyncMock(return_value=result))

    assert await _run_review_with_check(provider, engine, pr_info.url) is result
    provider.complete_review_check.assert_not_awaited()
