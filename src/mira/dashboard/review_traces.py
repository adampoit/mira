"""Persisted, real-time traces for pull request reviews."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import cast

from mira.dashboard.events import bus
from mira.models import PRInfo

TraceData = dict[str, object]
TraceEvent = dict[str, object]
ReviewSession = dict[str, object]

ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
_RETRY_REQUEST_FIELDS = frozenset(
    {
        "platform",
        "owner",
        "repo",
        "pr_number",
        "pr_url",
        "pr_title",
        "head_sha",
        "bot_name",
        "visibility",
        "auth_scope",
    }
)
_RETRY_REQUEST_REQUIRED_FIELDS = frozenset(
    {
        "platform",
        "owner",
        "repo",
        "pr_number",
        "pr_url",
        "pr_title",
        "head_sha",
        "bot_name",
        "visibility",
        "auth_scope",
    }
)


def retry_request_is_complete(request: object) -> bool:
    """Return whether a persisted request has enough safe inputs to retry."""
    if not isinstance(request, Mapping):
        return False
    typed_request = cast(Mapping[str, object], request)
    if not _RETRY_REQUEST_REQUIRED_FIELDS.issubset(typed_request):
        return False
    for key in _RETRY_REQUEST_REQUIRED_FIELDS - {"pr_number", "auth_scope"}:
        value = typed_request.get(key)
        if not isinstance(value, str):
            return False
        if key != "pr_title" and not value:
            return False
    auth_scope = typed_request.get("auth_scope")
    if not (
        (isinstance(auth_scope, str) and bool(auth_scope))
        or (isinstance(auth_scope, int) and not isinstance(auth_scope, bool) and auth_scope > 0)
    ):
        return False
    pr_number = typed_request.get("pr_number")
    return isinstance(pr_number, int) and not isinstance(pr_number, bool) and pr_number > 0


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, default)))
    except ValueError:
        return default


class ReviewTrace:
    store: TraceStore
    session_id: str
    instance_id: str

    def __init__(self, store: TraceStore, session_id: str, instance_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.instance_id = instance_id

    def update_context(self, pr_info: PRInfo) -> None:
        _ = self.store.update_context(self.session_id, pr_info, owner_id=self.instance_id)

    def emit(
        self,
        kind: str,
        title: str,
        detail: str = "",
        data: Mapping[str, object] | None = None,
    ) -> None:
        _ = self.store.append(
            self.session_id,
            kind,
            title,
            detail,
            dict(data or {}),
            owner_id=self.instance_id,
        )

    def touch(self) -> bool:
        return self.store.touch(self.session_id, owner_id=self.instance_id)

    def set_provider_check(self, platform: str, check_id: int | str) -> bool:
        return self.store.set_provider_check(
            self.session_id,
            platform=platform,
            check_id=check_id,
            owner_id=self.instance_id,
        )

    def finish_provider_check(self, status: str, error: str = "") -> bool:
        return self.store.update_provider_check(
            self.session_id,
            status=status,
            error=error,
            owner_id=self.instance_id,
        )


class TraceStore:
    path: Path
    instance_id: str
    heartbeat_interval: float
    lease_timeout: float
    _lock: threading.Lock
    _heartbeat_tasks: set[asyncio.Task[None]]
    _recovery_tasks: set[asyncio.Task[None]]

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        instance_id: str | None = None,
        heartbeat_interval: float | None = None,
        lease_timeout: float | None = None,
    ) -> None:
        if path is None:
            configured_path = os.environ.get("MIRA_TRACE_DIR")
            if configured_path:
                path = configured_path
            else:
                index_dir = Path(os.environ.get("MIRA_INDEX_DIR", "./data/indexes"))
                path = index_dir.parent / "review-traces"
        self.path = Path(path)
        self.instance_id = instance_id or uuid.uuid4().hex
        self.heartbeat_interval = heartbeat_interval or _env_float(
            "MIRA_TRACE_HEARTBEAT_SECONDS", 10.0
        )
        self.lease_timeout = lease_timeout or _env_float("MIRA_TRACE_LEASE_SECONDS", 60.0)
        self._lock = threading.Lock()
        self._heartbeat_tasks = set()
        self._recovery_tasks = set()

    def _file(self, session_id: str) -> Path:
        if not session_id or not session_id.replace("-", "").isalnum():
            raise ValueError("Invalid review session id")
        return self.path / f"{session_id}.json"

    def start(
        self,
        pr_info: PRInfo,
        *,
        retry_request: Mapping[str, object] | None = None,
        attempt: int = 1,
        retry_of: str | None = None,
    ) -> ReviewTrace:
        return self.start_details(
            owner=pr_info.owner,
            repo=pr_info.repo,
            pr_number=pr_info.number,
            pr_title=pr_info.title,
            pr_url=pr_info.url,
            head_sha=pr_info.head_sha,
            retry_request=retry_request,
            attempt=attempt,
            retry_of=retry_of,
        )

    def start_details(
        self,
        *,
        owner: str,
        repo: str,
        pr_number: int,
        pr_title: str,
        pr_url: str,
        head_sha: str = "",
        status: str = "running",
        retry_request: Mapping[str, object] | None = None,
        attempt: int = 1,
        retry_of: str | None = None,
        automatic_recovery_attempts: int = 0,
    ) -> ReviewTrace:
        if status not in ACTIVE_STATUSES:
            raise ValueError(f"Invalid initial review status: {status}")
        now = time.time()
        session_id = uuid.uuid4().hex
        session: ReviewSession = {
            "id": session_id,
            "status": status,
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "pr_url": pr_url,
            "head_sha": head_sha,
            "instance_id": self.instance_id,
            "heartbeat_at": now,
            "last_event_at": now,
            "attempt": max(1, attempt),
            "automatic_recovery_attempts": max(0, automatic_recovery_attempts),
            "retry_request": self._sanitize_retry_request(retry_request),
            "retry_of": retry_of,
            "replacement_id": None,
            "provider_check": None,
            "started_at": now,
            "finished_at": None,
            "events": [],
        }
        with self._lock:
            self._write(session)
        trace = ReviewTrace(self, session_id, self.instance_id)
        trace.emit(
            "context",
            "Review queued" if status == "queued" else "Review context received",
            f"Pull request #{pr_number}: {pr_title}",
            {
                "repository": f"{owner}/{repo}",
                "head_sha": head_sha,
                "pr_url": pr_url,
                "attempt": max(1, attempt),
            },
        )
        return trace

    def activate(self, session_id: str, *, owner_id: str) -> ReviewTrace:
        with self._lock:
            session = self._read(session_id)
            if session.get("instance_id") != owner_id:
                raise RuntimeError("Review session is owned by another process")
            if session.get("status") == "queued":
                session["status"] = "running"
            elif session.get("status") != "running":
                raise RuntimeError("Review session is already finished")
            session["heartbeat_at"] = time.time()
            self._write(session)
        bus.emit("review_trace_status", {"session_id": session_id, "status": "running"})
        return ReviewTrace(self, session_id, owner_id)

    def update_context(self, session_id: str, pr_info: PRInfo, *, owner_id: str) -> bool:
        with self._lock:
            session = self._read(session_id)
            if not self._is_owned_active(session, owner_id):
                return False
            session.update(
                pr_title=pr_info.title,
                pr_url=pr_info.url,
                head_sha=pr_info.head_sha,
                heartbeat_at=time.time(),
            )
            retry_request_value = session.get("retry_request")
            if isinstance(retry_request_value, dict):
                retry_request = cast(TraceData, retry_request_value)
                retry_request.update(
                    pr_title=pr_info.title,
                    pr_url=pr_info.url,
                    head_sha=pr_info.head_sha,
                )
            self._write(session)
        return True

    def append(
        self,
        session_id: str,
        kind: str,
        title: str,
        detail: str,
        data: TraceData,
        *,
        owner_id: str,
    ) -> bool:
        with self._lock:
            session = self._read(session_id)
            if not self._is_owned_active(session, owner_id):
                return False
            events = cast(list[TraceEvent], session["events"])
            now = time.time()
            event: TraceEvent = {
                "id": len(events) + 1,
                "kind": kind,
                "title": title,
                "detail": detail,
                "data": data,
                "created_at": now,
            }
            events.append(event)
            session["last_event_at"] = now
            session["heartbeat_at"] = now
            self._write(session)
        bus.emit("review_trace", {"session_id": session_id, "event": event})
        return True

    def touch(self, session_id: str, *, owner_id: str) -> bool:
        with self._lock:
            session = self._read(session_id)
            if not self._is_owned_active(session, owner_id):
                return False
            session["heartbeat_at"] = time.time()
            self._write(session)
        return True

    def set_provider_check(
        self,
        session_id: str,
        *,
        platform: str,
        check_id: int | str,
        owner_id: str,
    ) -> bool:
        with self._lock:
            session = self._read(session_id)
            if not self._is_owned_active(session, owner_id):
                return False
            session["provider_check"] = {
                "platform": platform,
                "check_id": check_id,
                "status": "in_progress",
                "started_at": time.time(),
            }
            self._write(session)
        return True

    def update_provider_check(
        self,
        session_id: str,
        *,
        status: str,
        error: str = "",
        owner_id: str | None = None,
    ) -> bool:
        with self._lock:
            session = self._read(session_id)
            if owner_id is not None and session.get("instance_id") != owner_id:
                return False
            check = session.get("provider_check")
            if not isinstance(check, dict):
                return False
            typed_check = cast(dict[str, object], check)
            updated = dict(typed_check)
            updated["status"] = status
            updated["updated_at"] = time.time()
            if error:
                updated["error"] = error
            session["provider_check"] = updated
            self._write(session)
        return True

    def record_recovery(self, session_id: str, status: str, detail: str = "") -> bool:
        with self._lock:
            session = self._read(session_id)
            session["recovery_status"] = status
            if detail:
                session["recovery_detail"] = detail
            self._write(session)
        return True

    def finish(
        self,
        session_id: str,
        status: str,
        detail: str = "",
        *,
        owner_id: str | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Invalid terminal review status: {status}")
        with self._lock:
            session = self._read(session_id)
            current_status = session.get("status")
            if current_status in TERMINAL_STATUSES:
                return False
            if owner_id is not None and session.get("instance_id") != owner_id:
                return False
            now = time.time()
            session["status"] = status
            session["finished_at"] = now
            session["heartbeat_at"] = now
            if detail:
                session["error"] = detail
            self._write(session)
        bus.emit("review_trace_status", {"session_id": session_id, "status": status})
        return True

    def link_replacement(self, session_id: str, replacement_id: str) -> None:
        with self._lock:
            session = self._read(session_id)
            existing = session.get("replacement_id")
            if existing not in (None, replacement_id):
                raise RuntimeError("Review session already has a replacement")
            session["replacement_id"] = replacement_id
            self._write(session)

    @staticmethod
    def _trace_metrics(events: list[TraceEvent]) -> TraceData:
        metrics: TraceData = {
            "pi_events": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "reasoning_chars": 0,
            "output_chars": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "duration_ms": 0,
            "models": [],
            "result_tools": [],
        }
        models: set[str] = set()
        result_tools: set[str] = set()
        for event in events:
            data = TraceStore._event_data(event)
            if data.get("source") != "pi":
                continue
            metrics["pi_events"] = cast(int, metrics["pi_events"]) + 1
            kind = event.get("kind")
            detail = event.get("detail")
            if kind == "agent_start":
                metrics["llm_calls"] = cast(int, metrics["llm_calls"]) + 1
            elif kind == "tool_call":
                metrics["tool_calls"] = cast(int, metrics["tool_calls"]) + 1
            elif kind == "tool_result" and data.get("is_error") is True:
                metrics["tool_errors"] = cast(int, metrics["tool_errors"]) + 1
            if kind == "reasoning" and isinstance(detail, str):
                metrics["reasoning_chars"] = cast(int, metrics["reasoning_chars"]) + len(detail)
            elif kind == "output" and isinstance(detail, str):
                metrics["output_chars"] = cast(int, metrics["output_chars"]) + len(detail)
            model = data.get("model")
            if isinstance(model, str) and model:
                models.add(model)
            result_tool = data.get("result_tool")
            if isinstance(result_tool, str) and result_tool:
                result_tools.add(result_tool)
            if kind != "agent_end":
                continue
            duration = data.get("duration_ms")
            if isinstance(duration, int | float) and not isinstance(duration, bool):
                metrics["duration_ms"] = cast(int, metrics["duration_ms"]) + int(duration)
            usage = data.get("usage")
            if not isinstance(usage, dict):
                continue
            typed_usage = cast(dict[str, object], usage)
            for metric, key in (
                ("input_tokens", "input"),
                ("output_tokens", "output"),
                ("cache_read_tokens", "cacheRead"),
                ("cache_write_tokens", "cacheWrite"),
                ("total_tokens", "total"),
            ):
                value = typed_usage.get(key)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    metrics[metric] = cast(int, metrics[metric]) + int(value)
        metrics["models"] = sorted(models)
        metrics["result_tools"] = sorted(result_tools)
        return metrics

    def get(self, session_id: str) -> ReviewSession:
        session = self._normalize(self._read(session_id))
        events = cast(list[TraceEvent], session.get("events", []))
        session["trace_metrics"] = self._trace_metrics(events)
        return session

    def list_sessions(self, limit: int = 200) -> list[ReviewSession]:
        sessions: list[ReviewSession] = []
        if not self.path.exists():
            return sessions
        for path in self.path.glob("*.json"):
            try:
                session = self._normalize(
                    cast(ReviewSession, json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, json.JSONDecodeError):
                continue
            events = cast(list[TraceEvent], session.pop("events", []))
            session["trace_metrics"] = self._trace_metrics(events)
            pass_events = [
                event
                for event in events
                if self._event_data(event).get("pass")
                and self._event_data(event).get("source") != "pi"
            ]
            current = pass_events[-1] if pass_events else None
            findings = 0
            agents: set[tuple[str, int]] = set()
            completed_agents: set[tuple[str, int]] = set()
            for event in events:
                data = self._event_data(event)
                event_findings = data.get("findings")
                if isinstance(event_findings, list):
                    findings = len(cast(list[object], event_findings))
                if data.get("source") == "pi":
                    continue
                pass_name = data.get("pass")
                agent_id = data.get("agent_id")
                if isinstance(pass_name, str) and isinstance(agent_id, int):
                    key = (pass_name, agent_id)
                    agents.add(key)
                    title = event.get("title")
                    if (
                        event.get("kind") == "decision"
                        and isinstance(title, str)
                        and title.endswith("complete")
                    ):
                        completed_agents.add(key)
            current_data = self._event_data(current) if current else {}
            session.update(
                current_pass=current_data.get("pass"),
                current_agent=current_data.get("agent_id"),
                findings=findings,
                agent_count=len(agents),
                completed_agents=len(completed_agents),
                event_count=len(events),
                lease_live=self.is_live(session),
            )
            sessions.append(session)
        sessions.sort(key=self._started_at, reverse=True)
        return sessions[:limit]

    def is_live(self, session: ReviewSession, *, now: float | None = None) -> bool:
        if session.get("status") not in ACTIVE_STATUSES:
            return False
        heartbeat_at = session.get("heartbeat_at")
        if not isinstance(heartbeat_at, int | float):
            return False
        current_time = time.time() if now is None else now
        return current_time - float(heartbeat_at) <= self.lease_timeout

    def find_live_session(self, owner: str, repo: str, pr_number: int) -> ReviewSession | None:
        _ = self.reconcile_stale()
        return next(
            (
                session
                for session in self.list_sessions(limit=500)
                if session.get("owner") == owner
                and session.get("repo") == repo
                and session.get("pr_number") == pr_number
                and self.is_live(session)
            ),
            None,
        )

    def reconcile_previous_instances(self) -> int:
        return len(self.interrupt_previous_instances())

    def interrupt_previous_instances(self) -> list[ReviewSession]:
        """Interrupt active sessions left by a process that no longer owns them."""
        return self._reconcile_sessions(
            lambda session: session.get("instance_id") != self.instance_id,
            "Mira restarted before this review completed.",
        )

    def reconcile_stale(self) -> int:
        now = time.time()
        return len(
            self._reconcile_sessions(
                lambda session: not self.is_live(session, now=now),
                "The review heartbeat lease expired before completion.",
            )
        )

    def _reconcile_sessions(
        self, should_interrupt: Callable[[ReviewSession], bool], detail: str
    ) -> list[ReviewSession]:
        recovered: list[ReviewSession] = []
        if not self.path.exists():
            return recovered
        with self._lock:
            for path in self.path.glob("*.json"):
                try:
                    session = self._normalize(
                        cast(ReviewSession, json.loads(path.read_text(encoding="utf-8")))
                    )
                except (OSError, json.JSONDecodeError):
                    continue
                if session.get("status") in ACTIVE_STATUSES and should_interrupt(session):
                    now = time.time()
                    session["status"] = "interrupted"
                    session["finished_at"] = now
                    session["heartbeat_at"] = now
                    session["last_event_at"] = now
                    session["error"] = detail
                    session["recovery_reason"] = detail
                    recovered.append(dict(session))
                self._write(session)
        return recovered

    def register_heartbeat(self, task: asyncio.Task[None]) -> None:
        self._heartbeat_tasks.add(task)
        task.add_done_callback(self._heartbeat_tasks.discard)

    async def stop_heartbeats(self) -> None:
        tasks = list(self._heartbeat_tasks)
        for task in tasks:
            _ = task.cancel()
        if tasks:
            _ = await asyncio.gather(*tasks, return_exceptions=True)

    def register_recovery_task(self, task: asyncio.Task[None]) -> None:
        self._recovery_tasks.add(task)
        task.add_done_callback(self._recovery_tasks.discard)

    async def stop_recovery_tasks(self) -> None:
        tasks = list(self._recovery_tasks)
        for task in tasks:
            _ = task.cancel()
        if tasks:
            _ = await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _sanitize_retry_request(request: Mapping[str, object] | None) -> TraceData | None:
        if request is None:
            return None
        return {
            key: value
            for key, value in request.items()
            if key in _RETRY_REQUEST_FIELDS and isinstance(value, str | int | bool)
        }

    @staticmethod
    def _event_data(event: TraceEvent) -> TraceData:
        data = event.get("data")
        return cast(TraceData, data) if isinstance(data, dict) else {}

    @staticmethod
    def _started_at(session: ReviewSession) -> float:
        started_at = session.get("started_at")
        return float(started_at) if isinstance(started_at, int | float) else 0.0

    @staticmethod
    def _is_owned_active(session: ReviewSession, owner_id: str) -> bool:
        return session.get("status") in ACTIVE_STATUSES and session.get("instance_id") == owner_id

    def _normalize(self, session: ReviewSession) -> ReviewSession:
        normalized = dict(session)
        events = normalized.get("events")
        if not isinstance(events, list):
            events = []
            normalized["events"] = events
        started_at = self._started_at(normalized)
        last_event_at = started_at
        if events:
            created_at = cast(dict[str, object], events[-1]).get("created_at")
            if isinstance(created_at, int | float):
                last_event_at = float(created_at)
        _ = normalized.setdefault("head_sha", "")
        _ = normalized.setdefault("instance_id", None)
        _ = normalized.setdefault("heartbeat_at", last_event_at)
        _ = normalized.setdefault("last_event_at", last_event_at)
        _ = normalized.setdefault("attempt", 1)
        _ = normalized.setdefault("automatic_recovery_attempts", 0)
        _ = normalized.setdefault("retry_request", None)
        _ = normalized.setdefault("retry_of", None)
        _ = normalized.setdefault("replacement_id", None)
        _ = normalized.setdefault("provider_check", None)
        _ = normalized.setdefault("trace_metrics", {})
        _ = normalized.setdefault("recovery_status", None)
        _ = normalized.setdefault("recovery_detail", None)
        _ = normalized.setdefault("finished_at", None)
        return normalized

    def _read(self, session_id: str) -> ReviewSession:
        with self._file(session_id).open(encoding="utf-8") as handle:
            return cast(ReviewSession, json.load(handle))

    def _write(self, session: ReviewSession) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        session_id = session.get("id")
        if not isinstance(session_id, str):
            raise ValueError("Review session is missing an id")
        target = self._file(session_id)
        temporary = target.with_suffix(".tmp")
        _ = temporary.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        _ = temporary.replace(target)


store = TraceStore()


async def _heartbeat(trace: ReviewTrace) -> None:
    while True:
        await asyncio.sleep(trace.store.heartbeat_interval)
        if not trace.touch():
            return


@asynccontextmanager
async def review_lifecycle(
    *,
    owner: str,
    repo: str,
    number: int,
    pr_title: str,
    pr_url: str,
    retry_request: Mapping[str, object],
    session_id: str | None = None,
    already_claimed: bool = False,
) -> AsyncGenerator[ReviewTrace | None, None]:
    """Own trace, heartbeat, and in-memory status transitions for one review."""
    from mira.core.review_status import tracker as review_tracker

    repo_full = f"{owner}/{repo}"
    if not already_claimed and not review_tracker.try_start(repo_full, number, pr_title, pr_url):
        if session_id is not None:
            _ = store.finish(
                session_id,
                "interrupted",
                "Another review was already active for this pull request.",
                owner_id=store.instance_id,
            )
        yield None
        return

    try:
        trace = (
            store.activate(session_id, owner_id=store.instance_id)
            if session_id is not None
            else store.start_details(
                owner=owner,
                repo=repo,
                pr_number=number,
                pr_title=pr_title,
                pr_url=pr_url,
                retry_request=retry_request,
            )
        )
    except Exception as exc:
        review_tracker.fail(repo_full, number, str(exc))
        raise

    heartbeat_task = asyncio.create_task(_heartbeat(trace))
    store.register_heartbeat(heartbeat_task)
    try:
        yield trace
    except asyncio.CancelledError:
        detail = "Review cancelled before completion."
        with suppress(Exception):
            trace.emit("error", "Review interrupted", detail)
            _ = store.finish(trace.session_id, "interrupted", detail, owner_id=trace.instance_id)
        review_tracker.interrupt(repo_full, number, detail)
        raise
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        with suppress(Exception):
            trace.emit("error", "Review stopped", detail)
            _ = store.finish(trace.session_id, "failed", detail, owner_id=trace.instance_id)
        review_tracker.fail(repo_full, number, detail)
        raise
    else:
        _ = store.finish(trace.session_id, "completed", owner_id=trace.instance_id)
        review_tracker.complete(repo_full, number)
    finally:
        _ = heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
