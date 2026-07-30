from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from mira.core.review_queue import _review_queues, queue_pr_commands, queue_pr_reviews


@pytest.fixture(autouse=True)
def clear_review_queues() -> Generator[None, None, None]:
    _review_queues.clear()
    yield
    _review_queues.clear()


@pytest.mark.asyncio
async def test_reviews_for_same_pr_run_one_at_a_time() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    starts: list[int] = []
    active = 0
    max_active = 0

    async def review(
        provider: Any,
        owner: str,
        repo: str,
        number: int,
        pr_url: str,
        is_private: bool,
        bot_name: str,
        platform: str = "github",
        pr_title: str = "",
    ) -> None:
        nonlocal active, max_active
        starts.append(len(starts) + 1)
        active += 1
        max_active = max(max_active, active)
        try:
            if len(starts) == 1:
                first_started.set()
                await release_first.wait()
        finally:
            active -= 1

    queued_review = queue_pr_reviews(review)
    args = (None, "owner", "repo", 1, "https://example.test/pull/1", False, "mira")

    first = asyncio.create_task(queued_review(*args))
    await first_started.wait()
    second = asyncio.create_task(queued_review(*args))
    await asyncio.sleep(0)

    assert starts == [1]
    assert next(iter(_review_queues.values())).users == 2

    release_first.set()
    await asyncio.gather(first, second)

    assert starts == [1, 2]
    assert max_active == 1
    assert _review_queues == {}


@pytest.mark.asyncio
async def test_reviews_for_different_prs_run_concurrently() -> None:
    both_started = asyncio.Event()
    release_reviews = asyncio.Event()
    active = 0

    async def review(
        provider: Any,
        owner: str,
        repo: str,
        number: int,
        pr_url: str,
        is_private: bool,
        bot_name: str,
        platform: str = "github",
        pr_title: str = "",
    ) -> None:
        nonlocal active
        active += 1
        if active == 2:
            both_started.set()
        await release_reviews.wait()
        active -= 1

    queued_review = queue_pr_reviews(review)
    common = (None, "owner", "repo")
    first = asyncio.create_task(
        queued_review(*common, 1, "https://example.test/pull/1", False, "mira")
    )
    second = asyncio.create_task(
        queued_review(*common, 2, "https://example.test/pull/2", False, "mira")
    )

    await asyncio.wait_for(both_started.wait(), timeout=1)
    assert active == 2

    release_reviews.set()
    await asyncio.gather(first, second)
    assert _review_queues == {}


@pytest.mark.asyncio
async def test_review_commands_queue_but_questions_do_not() -> None:
    review_started = asyncio.Event()
    question_started = asyncio.Event()
    release_review = asyncio.Event()

    async def command(
        provider: Any,
        owner: str,
        repo: str,
        number: int,
        pr_url: str,
        question: str,
        actor: str,
        bot_name: str,
        platform: str = "github",
        pr_title: str = "",
    ) -> None:
        if question == "review":
            review_started.set()
            await release_review.wait()
        else:
            question_started.set()

    queued_command = queue_pr_commands(command, {"review", "review-rest"})
    common = (None, "owner", "repo", 1, "https://example.test/pull/1")
    review = asyncio.create_task(queued_command(*common, "review", "alice", "mira"))
    await review_started.wait()

    question = asyncio.create_task(queued_command(*common, "why is this slow?", "alice", "mira"))
    await asyncio.wait_for(question_started.wait(), timeout=1)
    await question

    release_review.set()
    await review
    assert _review_queues == {}


@pytest.mark.asyncio
async def test_cancelling_a_waiting_review_removes_it_from_queue() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def review(
        provider: Any,
        owner: str,
        repo: str,
        number: int,
        pr_url: str,
        is_private: bool,
        bot_name: str,
        platform: str = "github",
        pr_title: str = "",
    ) -> None:
        first_started.set()
        await release_first.wait()

    queued_review = queue_pr_reviews(review)
    args = (None, "owner", "repo", 1, "https://example.test/pull/1", False, "mira")
    first = asyncio.create_task(queued_review(*args))
    await first_started.wait()
    waiting = asyncio.create_task(queued_review(*args))
    await asyncio.sleep(0)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert next(iter(_review_queues.values())).users == 1

    release_first.set()
    await first
    assert _review_queues == {}
