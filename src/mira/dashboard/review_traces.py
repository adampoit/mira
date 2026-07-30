"""Persisted, real-time traces for pull request reviews."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from mira.dashboard.events import bus
from mira.models import PRInfo

TraceData = dict[str, object]
TraceEvent = dict[str, object]
ReviewSession = dict[str, object]


class ReviewTrace:
    store: TraceStore
    session_id: str

    def __init__(self, store: TraceStore, session_id: str) -> None:
        self.store = store
        self.session_id = session_id

    def update_context(self, pr_info: PRInfo) -> None:
        self.store.update_context(self.session_id, pr_info)

    def emit(
        self,
        kind: str,
        title: str,
        detail: str = "",
        data: Mapping[str, object] | None = None,
    ) -> None:
        self.store.append(self.session_id, kind, title, detail, dict(data or {}))


class TraceStore:
    path: Path
    _lock: threading.Lock

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            configured_path = os.environ.get("MIRA_TRACE_DIR")
            if configured_path:
                path = configured_path
            else:
                index_dir = Path(os.environ.get("MIRA_INDEX_DIR", "./data/indexes"))
                path = index_dir.parent / "review-traces"
        self.path = Path(path)
        self._lock = threading.Lock()

    def _file(self, session_id: str) -> Path:
        if not session_id or not session_id.replace("-", "").isalnum():
            raise ValueError("Invalid review session id")
        return self.path / f"{session_id}.json"

    def start(self, pr_info: PRInfo) -> ReviewTrace:
        return self.start_details(
            owner=pr_info.owner,
            repo=pr_info.repo,
            pr_number=pr_info.number,
            pr_title=pr_info.title,
            pr_url=pr_info.url,
            head_sha=pr_info.head_sha,
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
    ) -> ReviewTrace:
        session_id = uuid.uuid4().hex
        session: ReviewSession = {
            "id": session_id,
            "status": "running",
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "pr_url": pr_url,
            "head_sha": head_sha,
            "started_at": time.time(),
            "finished_at": None,
            "events": [],
        }
        with self._lock:
            self._write(session)
        trace = ReviewTrace(self, session_id)
        trace.emit(
            "context",
            "Review context received",
            f"Pull request #{pr_number}: {pr_title}",
            {
                "repository": f"{owner}/{repo}",
                "head_sha": head_sha,
                "pr_url": pr_url,
            },
        )
        return trace

    def update_context(self, session_id: str, pr_info: PRInfo) -> None:
        with self._lock:
            session = self.get(session_id)
            session.update(
                pr_title=pr_info.title,
                pr_url=pr_info.url,
                head_sha=pr_info.head_sha,
            )
            self._write(session)

    def append(
        self,
        session_id: str,
        kind: str,
        title: str,
        detail: str,
        data: TraceData,
    ) -> None:
        with self._lock:
            session = self.get(session_id)
            events = cast(list[TraceEvent], session["events"])
            event: TraceEvent = {
                "id": len(events) + 1,
                "kind": kind,
                "title": title,
                "detail": detail,
                "data": data,
                "created_at": time.time(),
            }
            events.append(event)
            self._write(session)
        bus.emit("review_trace", {"session_id": session_id, "event": event})

    def finish(self, session_id: str, status: str, detail: str = "") -> None:
        with self._lock:
            session = self.get(session_id)
            session["status"] = status
            session["finished_at"] = time.time()
            if detail:
                session["error"] = detail
            self._write(session)
        bus.emit("review_trace_status", {"session_id": session_id, "status": status})

    def get(self, session_id: str) -> ReviewSession:
        with self._file(session_id).open(encoding="utf-8") as handle:
            return cast(ReviewSession, json.load(handle))

    def list_sessions(self, limit: int = 200) -> list[ReviewSession]:
        sessions: list[ReviewSession] = []
        if not self.path.exists():
            return sessions
        for path in self.path.glob("*.json"):
            try:
                session = cast(ReviewSession, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
            events = cast(list[TraceEvent], session.pop("events", []))
            pass_events = [event for event in events if self._event_data(event).get("pass")]
            current = pass_events[-1] if pass_events else None
            findings = 0
            agents: set[tuple[str, int]] = set()
            completed_agents: set[tuple[str, int]] = set()
            for event in events:
                data = self._event_data(event)
                event_findings = data.get("findings")
                if isinstance(event_findings, list):
                    findings = len(cast(list[object], event_findings))
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
            )
            sessions.append(session)
        sessions.sort(key=self._started_at, reverse=True)
        return sessions[:limit]

    @staticmethod
    def _event_data(event: TraceEvent) -> TraceData:
        data = event.get("data")
        return cast(TraceData, data) if isinstance(data, dict) else {}

    @staticmethod
    def _started_at(session: ReviewSession) -> float:
        started_at = session.get("started_at")
        return float(started_at) if isinstance(started_at, int | float) else 0.0

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
