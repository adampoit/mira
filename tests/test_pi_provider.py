# pyright: standard

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from unittest.mock import call, patch

import pytest
from pydantic import ValidationError

from mira.config import MiraConfig, ReviewConfig
from mira.llm.base import NativeAgentLoopProvider, RepositoryAwareProvider
from mira.llm.pi_provider import PiLLMProvider
from mira.llm.review_factory import create_review_llms
from mira.models import PRInfo


def _pi_provider() -> PiLLMProvider:
    return PiLLMProvider(
        model="deepseek/deepseek-v4-pro",
        thinking_level="high",
    )


@pytest.fixture
def fake_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    worker = tmp_path / "fake-pi-worker"
    _ = worker.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time

request = json.loads(sys.stdin.readline())
root = os.path.dirname(__file__)
scenario_file = os.path.join(root, "scenario")
scenario = open(scenario_file).read() if os.path.exists(scenario_file) else "success"
if scenario == "error":
    print(json.dumps({"type": "error", "error": "worker exploded"}), flush=True)
elif scenario == "malformed":
    print("{not-json", flush=True)
elif scenario == "timeout":
    open(os.path.join(root, "worker.pid"), "w").write(str(os.getpid()))
    time.sleep(30)
elif scenario == "environment":
    print(json.dumps({"type": "done", "result": json.dumps(dict(os.environ))}), flush=True)
elif scenario == "request":
    print(json.dumps({"type": "done", "result": json.dumps(request)}), flush=True)
elif scenario == "text":
    print(json.dumps({"type": "done", "result": json.dumps({"content": "plain response"})}), flush=True)
elif scenario == "tools":
    responses = []
    calls = [
        ("read", {"path": "src/app.py", "offset": 2, "limit": 1}),
        ("grep", {"pattern": "needle", "path_glob": "*.py"}),
        ("grep", {"pattern": "app", "path_only": True}),
        ("find", {"pattern": "src/*"}),
        ("ls", {"path": "src"}),
    ]
    for identifier, (method, params) in enumerate(calls, 1):
        print(json.dumps({"type": "tool_request", "id": identifier, "method": method, "params": params}), flush=True)
        responses.append(json.loads(sys.stdin.readline())["result"])
    print(json.dumps({"type": "done", "result": json.dumps(responses), "usage": {"input": 3, "output": 2}}), flush=True)
elif scenario == "grep_budget":
    print(json.dumps({"type": "tool_request", "id": 1, "method": "grep", "params": {"pattern": "absent"}}), flush=True)
    response = json.loads(sys.stdin.readline())
    print(json.dumps({"type": "done", "result": response["result"]}), flush=True)
else:
    print(json.dumps({"type": "tool_start", "tool": "submit_review", "args": {}}), flush=True)
    print(json.dumps({"type": "tool_end", "tool": "submit_review", "is_error": False}), flush=True)
    print(json.dumps({"type": "done", "result": '{"summary":"ok"}', "usage": {"input": 7, "output": 4}}), flush=True)
""",
        encoding="utf-8",
    )
    worker.chmod(0o755)
    monkeypatch.setenv("MIRA_PI_WORKER", str(worker))
    monkeypatch.setenv("MIRA_PI_SESSION_DIR", str(tmp_path / "sessions"))
    return worker


@pytest.mark.asyncio
async def test_pi_worker_returns_structured_output(fake_worker: Path) -> None:
    assert fake_worker.is_file()
    provider = _pi_provider()

    result = await provider.review([{"role": "user", "content": "review"}])

    assert json.loads(result) == {"summary": "ok"}
    assert provider.usage == {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11}


@pytest.mark.asyncio
async def test_pi_worker_receives_messages_model_and_thinking_level(fake_worker: Path) -> None:
    _ = (fake_worker.parent / "scenario").write_text("request")
    provider = PiLLMProvider(
        model="deepseek/deepseek-v4-flash",
        thinking_level="medium",
    )
    messages = [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "Review this"},
    ]

    request = cast(dict[str, object], json.loads(await provider.review(messages)))

    assert request["result_tool"] == "submit_review"
    assert request["messages"] == messages
    assert request["model"] == "deepseek/deepseek-v4-flash"
    assert request["thinking_level"] == "medium"
    tools = cast(list[dict[str, dict[str, object]]], request["tools"])
    assert [tool["function"]["name"] for tool in tools] == ["submit_review"]
    assert "result_mode" not in request
    assert "json_mode" not in request
    assert "pass_name" not in request


@pytest.mark.asyncio
async def test_pi_text_completion_uses_structured_submission(fake_worker: Path) -> None:
    _ = (fake_worker.parent / "scenario").write_text("text")

    result = await _pi_provider().complete(
        [{"role": "user", "content": "Answer the question"}],
        json_mode=False,
    )

    assert result == "plain response"


@pytest.mark.asyncio
async def test_pi_worker_receives_only_allowlisted_environment(
    fake_worker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (fake_worker.parent / "scenario").write_text("environment")
    monkeypatch.setenv("HOME", "/allowlisted-home")
    monkeypatch.setenv("OPENCODE_API_KEY", "opencode-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://sensitive")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-forward")

    environment = cast(
        dict[str, str],
        json.loads(await _pi_provider().review([{"role": "user", "content": "review"}])),
    )

    assert environment["PATH"] == os.environ["PATH"]
    assert environment["HOME"] == "/allowlisted-home"
    assert environment["OPENCODE_API_KEY"] == "opencode-secret"
    assert environment["PI_OFFLINE"] == "1"
    assert environment["PI_SKIP_VERSION_CHECK"] == "1"
    assert environment["PI_TELEMETRY"] == "0"
    assert "DATABASE_URL" not in environment
    assert "UNRELATED_SECRET" not in environment
    assert "MIRA_PI_WORKER" not in environment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "message"),
    [("error", "worker exploded"), ("malformed", "malformed protocol JSON")],
)
async def test_pi_worker_protocol_failures_are_explicit(
    fake_worker: Path,
    scenario: str,
    message: str,
) -> None:
    _ = (fake_worker.parent / "scenario").write_text(scenario)

    with pytest.raises(RuntimeError, match=message):
        _ = await _pi_provider().review([{"role": "user", "content": "review"}])


@pytest.mark.asyncio
async def test_pi_worker_timeout_cleans_up_process(
    fake_worker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = fake_worker.parent / "worker.pid"
    _ = (fake_worker.parent / "scenario").write_text("timeout")
    monkeypatch.setenv("MIRA_PI_TIMEOUT_SECONDS", "0.5")

    with pytest.raises(RuntimeError, match="timed out"):
        _ = await _pi_provider().review([{"role": "user", "content": "review"}])

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_pi_repository_tools_use_public_provider_api(fake_worker: Path) -> None:
    _ = (fake_worker.parent / "scenario").write_text("tools")

    class RepositoryProvider:
        def __init__(self) -> None:
            self.read_paths: list[str] = []
            self.snapshot_calls = 0

        async def get_repo_tree(self, pr_info: PRInfo, ref: str) -> list[str]:
            del pr_info
            assert ref == "head123"
            return ["src/app.py", "src/other.py", "README.md"]

        async def get_file_content(self, pr_info: PRInfo, path: str, ref: str) -> str:
            del pr_info
            assert ref == "head123"
            self.read_paths.append(path)
            return {
                "src/app.py": "first\nneedle here\nthird",
                "src/other.py": "no match",
                "README.md": "# Project",
            }[path]

        async def get_repo_snapshot(self, pr_info: PRInfo, ref: str) -> dict[str, str]:
            del pr_info
            assert ref == "head123"
            self.snapshot_calls += 1
            return {
                "src/app.py": "first\nneedle here\nthird",
                "src/other.py": "no match",
                "README.md": "# Project",
            }

    repository = RepositoryProvider()
    pr_info = PRInfo("", "", "main", "feature", "", 1, "owner", "repo", "head123")
    provider = _pi_provider()
    await provider.bind_repository(repository, pr_info)

    result = cast(
        list[str], json.loads(await provider.review([{"role": "user", "content": "review"}]))
    )

    assert result == [
        "2: needle here",
        "src/app.py:2:needle here",
        "src/app.py",
        "src/app.py\nsrc/other.py",
        "app.py\nother.py",
    ]
    assert repository.read_paths == ["src/app.py"]
    assert repository.snapshot_calls == 1


@pytest.mark.asyncio
async def test_pi_grep_has_a_file_budget_and_skips_vendor_paths(fake_worker: Path) -> None:
    _ = (fake_worker.parent / "scenario").write_text("grep_budget")

    class RepositoryProvider:
        def __init__(self) -> None:
            self.read_paths: list[str] = []

        async def get_repo_tree(self, pr_info: PRInfo, ref: str) -> list[str]:
            del pr_info, ref
            return [
                "node_modules/package/index.js",
                "vendor/library.py",
                "package-lock.json",
                *[f"src/file_{index}.py" for index in range(30)],
            ]

        async def get_file_content(self, pr_info: PRInfo, path: str, ref: str) -> str:
            del pr_info, ref
            self.read_paths.append(path)
            return "nothing here"

    repository = RepositoryProvider()
    pr_info = PRInfo("", "", "main", "feature", "", 1, "owner", "repo", "head123")
    provider = _pi_provider()
    await provider.bind_repository(repository, pr_info)

    result = await provider.review([{"role": "user", "content": "review"}])

    assert "scanned 15 files" in result
    assert repository.read_paths == [f"src/file_{index}.py" for index in range(15)]


def test_create_review_llms_uses_configured_pipeline_models() -> None:
    config = MiraConfig()
    llm_config = config.llm
    review_config = object()
    indexing_config = object()
    review_llm = object()
    indexing_llm = object()

    with (
        patch(
            "mira.dashboard.models_config.llm_config_for",
            side_effect=[review_config, indexing_config],
        ) as resolve_config,
        patch(
            "mira.llm.review_factory.create_llm",
            side_effect=[review_llm, indexing_llm],
        ) as create,
    ):
        result = cast(tuple[object, object], create_review_llms(config))

    assert result == (review_llm, indexing_llm)
    assert resolve_config.call_args_list == [
        call("review", llm_config),
        call("indexing", llm_config),
    ]
    assert create.call_args_list == [call(review_config), call(indexing_config)]


def test_create_review_llms_builds_pi_providers_from_resolved_models() -> None:
    config = MiraConfig()
    config.review.engine = "pi_agent"
    review_config = config.llm.model_copy(
        update={"model": "deepseek/deepseek-v4-pro", "reasoning_effort": "high"}
    )
    indexing_config = config.llm.model_copy(
        update={"model": "deepseek/deepseek-v4-flash", "reasoning_effort": None}
    )

    with patch(
        "mira.dashboard.models_config.llm_config_for",
        side_effect=[review_config, indexing_config],
    ):
        review_llm, indexing_llm = cast(
            tuple[PiLLMProvider, PiLLMProvider],
            create_review_llms(config),
        )

    assert review_llm.model == "deepseek/deepseek-v4-pro"
    assert review_llm.thinking_level == "high"
    assert indexing_llm.model == "deepseek/deepseek-v4-flash"
    assert indexing_llm.thinking_level == "off"


def test_pi_capabilities_are_explicit() -> None:
    provider = _pi_provider()

    assert isinstance(provider, NativeAgentLoopProvider)
    assert isinstance(provider, RepositoryAwareProvider)


def test_pipeline_is_default_and_engine_value_is_validated() -> None:
    assert ReviewConfig().engine == "pipeline"
    assert ReviewConfig(engine="pi_agent").engine == "pi_agent"
    with pytest.raises(ValidationError):
        _ = ReviewConfig.model_validate({"engine": "shadow"})
