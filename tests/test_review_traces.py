import asyncio
import json
import time
from pathlib import Path
from typing import cast

import pytest

import mira.dashboard.review_traces as review_traces
from mira.dashboard.review_traces import TraceStore
from mira.models import PRInfo


@pytest.fixture
def pr_info() -> PRInfo:
    return PRInfo(
        owner="miracodeai",
        repo="mira",
        number=42,
        title="Add review traces",
        description="",
        base_branch="main",
        head_branch="review-traces",
        url="https://github.com/miracodeai/mira/pull/42",
        head_sha="abc123",
    )


def test_trace_store_records_and_summarizes_session(tmp_path: Path, pr_info: PRInfo) -> None:
    store = TraceStore(tmp_path)
    trace = store.start(pr_info)

    trace.emit(
        "action",
        "Review agent 1 started",
        data={"pass": "review", "agent_id": 1},
    )
    trace.emit(
        "decision",
        "Review agent 1 complete",
        data={
            "pass": "review",
            "agent_id": 1,
            "findings": [{"title": "Example"}],
        },
    )
    assert store.finish(trace.session_id, "completed")

    session = store.get(trace.session_id)
    assert session["status"] == "completed"
    events = cast(list[dict[str, object]], session["events"])
    assert [event["id"] for event in events] == [1, 2, 3]

    summary = store.list_sessions()[0]
    assert summary["id"] == trace.session_id
    assert summary["current_pass"] == "review"
    assert summary["agent_count"] == 1
    assert summary["completed_agents"] == 1
    assert summary["findings"] == 1
    assert summary["event_count"] == 3
    assert "events" not in summary


def test_trace_context_can_be_refreshed(tmp_path: Path, pr_info: PRInfo) -> None:
    store = TraceStore(tmp_path)
    trace = store.start_details(
        owner=pr_info.owner,
        repo=pr_info.repo,
        pr_number=pr_info.number,
        pr_title="",
        pr_url=pr_info.url,
    )

    trace.update_context(pr_info)

    session = store.get(trace.session_id)
    assert session["pr_title"] == pr_info.title
    assert session["head_sha"] == pr_info.head_sha


def test_trace_store_rejects_invalid_session_ids(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)

    with pytest.raises(ValueError):
        _ = store.get("../outside")


def test_trace_heartbeat_and_terminal_transition_are_idempotent(
    tmp_path: Path, pr_info: PRInfo
) -> None:
    store = TraceStore(tmp_path, instance_id="current")
    trace = store.start(pr_info)
    first_heartbeat = cast(float, store.get(trace.session_id)["heartbeat_at"])

    time.sleep(0.001)
    assert trace.touch()
    assert cast(float, store.get(trace.session_id)["heartbeat_at"]) > first_heartbeat
    assert store.finish(trace.session_id, "completed", owner_id=trace.instance_id)
    assert not store.finish(trace.session_id, "failed", "late failure", owner_id=trace.instance_id)

    session = store.get(trace.session_id)
    assert session["status"] == "completed"
    assert "error" not in session


def test_startup_recovery_interrupts_previous_instance(tmp_path: Path, pr_info: PRInfo) -> None:
    previous_store = TraceStore(tmp_path, instance_id="previous")
    trace = previous_store.start(pr_info)
    current_store = TraceStore(tmp_path, instance_id="current")

    assert current_store.reconcile_previous_instances() == 1
    session = current_store.get(trace.session_id)
    assert session["status"] == "interrupted"
    assert session["finished_at"] is not None
    assert "restarted" in cast(str, session["error"]).lower()


def test_startup_recovery_migrates_legacy_running_trace(tmp_path: Path) -> None:
    session_id = "legacy123"
    tmp_path.mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / f"{session_id}.json").write_text(
        json.dumps(
            {
                "id": session_id,
                "status": "running",
                "owner": "miracodeai",
                "repo": "mira",
                "pr_number": 42,
                "pr_title": "Legacy review",
                "pr_url": "https://example.test/pull/42",
                "started_at": 100.0,
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    store = TraceStore(tmp_path, instance_id="current")

    assert store.reconcile_previous_instances() == 1
    session = store.get(session_id)
    assert session["status"] == "interrupted"
    assert session["attempt"] == 1
    assert session["retry_request"] is None
    assert session["head_sha"] == ""


def test_stale_lease_is_interrupted_and_does_not_remain_live(
    tmp_path: Path, pr_info: PRInfo
) -> None:
    store = TraceStore(tmp_path, instance_id="current", lease_timeout=0.1)
    trace = store.start(pr_info)
    path = tmp_path / f"{trace.session_id}.json"
    session = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    session["heartbeat_at"] = time.time() - 1
    _ = path.write_text(json.dumps(session), encoding="utf-8")

    assert store.find_live_session(pr_info.owner, pr_info.repo, pr_info.number) is None
    assert store.get(trace.session_id)["status"] == "interrupted"


def test_old_owner_cannot_finish_or_append_to_session(tmp_path: Path, pr_info: PRInfo) -> None:
    store = TraceStore(tmp_path, instance_id="replacement-owner")
    trace = store.start(pr_info)
    event_count = len(cast(list[object], store.get(trace.session_id)["events"]))

    assert not store.finish(trace.session_id, "failed", "late callback", owner_id="old-owner")
    assert not store.append(
        trace.session_id,
        "error",
        "Late callback",
        "",
        {},
        owner_id="old-owner",
    )
    session = store.get(trace.session_id)
    assert session["status"] == "running"
    assert len(cast(list[object], session["events"])) == event_count


@pytest.mark.asyncio
async def test_review_lifecycle_marks_cancellation_interrupted_and_reraises(
    tmp_path: Path, pr_info: PRInfo, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TraceStore(
        tmp_path,
        instance_id="current",
        heartbeat_interval=0.01,
        lease_timeout=1,
    )
    monkeypatch.setattr(review_traces, "store", store)
    session_id = ""

    async def cancel_during_review() -> None:
        nonlocal session_id
        async with review_traces.review_lifecycle(
            owner=pr_info.owner,
            repo=pr_info.repo,
            number=9876,
            pr_title=pr_info.title,
            pr_url=pr_info.url,
            retry_request={"platform": "github", "token": "must-not-persist"},
        ) as trace:
            assert trace is not None
            session_id = trace.session_id
            await asyncio.sleep(10)

    task = asyncio.create_task(cancel_during_review())
    await asyncio.sleep(0)
    _ = task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    session = store.get(session_id)
    assert session["status"] == "interrupted"
    assert session["retry_request"] == {"platform": "github"}
    assert not store._heartbeat_tasks  # pyright: ignore[reportPrivateUsage]
