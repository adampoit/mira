"""Process-local serialization for review requests targeting the same PR."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator, Callable, Collection, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from mira.core.review_status import tracker as review_tracker

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class _ReviewQueueEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_review_queues: dict[tuple[str, str, str, int], _ReviewQueueEntry] = {}


@asynccontextmanager
async def _pr_review_slot(
    platform: str, owner: str, repo: str, number: int
) -> AsyncGenerator[None, None]:
    key = (platform, owner, repo, number)
    entry = _review_queues.setdefault(key, _ReviewQueueEntry())
    reviews_ahead = entry.users
    entry.users += 1

    if reviews_ahead:
        logger.info(
            "Queued review for %s/%s#%d behind %d review(s)",
            owner,
            repo,
            number,
            reviews_ahead,
        )

    try:
        async with entry.lock:
            if reviews_ahead:
                logger.info("Starting queued review for %s/%s#%d", owner, repo, number)
            yield
    finally:
        entry.users -= 1
        if entry.users == 0 and _review_queues.get(key) is entry:
            del _review_queues[key]


def _review_identity(
    signature: inspect.Signature, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[inspect.BoundArguments, str, str, str, int]:
    request = signature.bind(*args, **kwargs)
    request.apply_defaults()
    return (
        request,
        str(request.arguments["platform"]),
        str(request.arguments["owner"]),
        str(request.arguments["repo"]),
        int(request.arguments["number"]),
    )


def queue_pr_reviews(
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    signature = inspect.signature(func)

    @wraps(func)
    async def queued(*args: P.args, **kwargs: P.kwargs) -> R:
        _, platform, owner, repo, number = _review_identity(signature, args, kwargs)
        async with _pr_review_slot(platform, owner, repo, number):
            try:
                return await func(*args, **kwargs)
            except asyncio.CancelledError:
                review_tracker.fail(f"{owner}/{repo}", number, "Review cancelled")
                raise

    return queued


def queue_pr_commands(
    func: Callable[P, Coroutine[Any, Any, R]], review_commands: Collection[str]
) -> Callable[P, Coroutine[Any, Any, R]]:
    signature = inspect.signature(func)
    normalized_review_commands = {command.lower().strip() for command in review_commands}

    @wraps(func)
    async def queued(*args: P.args, **kwargs: P.kwargs) -> R:
        request, platform, owner, repo, number = _review_identity(signature, args, kwargs)
        question = str(request.arguments["question"]).lower().strip()
        if question not in normalized_review_commands:
            return await func(*args, **kwargs)

        async with _pr_review_slot(platform, owner, repo, number):
            try:
                return await func(*args, **kwargs)
            except asyncio.CancelledError:
                review_tracker.fail(f"{owner}/{repo}", number, "Review cancelled")
                raise

    return queued
