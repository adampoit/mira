from pathlib import Path
from typing import cast

import pytest

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
    store.finish(trace.session_id, "completed")

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
