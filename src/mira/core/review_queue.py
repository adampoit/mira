"""Process-local serialization for review requests targeting the same PR."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import ParamSpec, TypeVar, cast

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
    signature: inspect.Signature, args: tuple[object, ...], kwargs: dict[str, object]
) -> tuple[inspect.BoundArguments, str, str, str, int]:
    request = signature.bind(*args, **kwargs)
    request.apply_defaults()
    arguments = cast(dict[str, object], request.arguments)
    number = arguments["number"]
    if not isinstance(number, int):
        raise TypeError("Review number must be an integer")
    return (
        request,
        str(arguments["platform"]),
        str(arguments["owner"]),
        str(arguments["repo"]),
        number,
    )


def queue_pr_reviews(
    func: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    signature = inspect.signature(func)

    @wraps(func)
    async def queued(*args: P.args, **kwargs: P.kwargs) -> R:
        _, platform, owner, repo, number = _review_identity(signature, args, kwargs)
        async with _pr_review_slot(platform, owner, repo, number):
            return await func(*args, **kwargs)

    return queued


def queue_pr_commands(
    func: Callable[P, Awaitable[R]], review_commands: Collection[str]
) -> Callable[P, Awaitable[R]]:
    signature = inspect.signature(func)
    normalized_review_commands = {command.lower().strip() for command in review_commands}

    @wraps(func)
    async def queued(*args: P.args, **kwargs: P.kwargs) -> R:
        request, platform, owner, repo, number = _review_identity(signature, args, kwargs)
        arguments = cast(dict[str, object], request.arguments)
        question = str(arguments["question"]).lower().strip()
        if question not in normalized_review_commands:
            return await func(*args, **kwargs)

        async with _pr_review_slot(platform, owner, repo, number):
            return await func(*args, **kwargs)

    return queued
