# pyright: standard

"""Pi coding-agent adapter for Mira's existing LLM provider boundary."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, TypeAlias, cast
from weakref import WeakKeyDictionary

from mira.llm.base import RepositoryProvider, SnapshotRepositoryProvider
from mira.llm.tool_schemas import SUBMIT_REVIEW_TOOL, SUBMIT_WALKTHROUGH_TOOL
from mira.models import PRInfo

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
PiTraceSink: TypeAlias = Callable[[str, str, Mapping[str, object]], None]

_MAX_TRACE_VALUE_CHARS = 20_000


def _trace_text(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= _MAX_TRACE_VALUE_CHARS:
        return text
    return f"{text[:_MAX_TRACE_VALUE_CHARS].rstrip()}… [truncated]"


def _trace_value(value: object) -> object:
    if isinstance(value, str) and len(value) <= _MAX_TRACE_VALUE_CHARS:
        return value
    if isinstance(value, str):
        return f"{value[:_MAX_TRACE_VALUE_CHARS].rstrip()}… [truncated]"
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    if len(encoded) <= _MAX_TRACE_VALUE_CHARS:
        return value
    return f"{encoded[:_MAX_TRACE_VALUE_CHARS].rstrip()}… [truncated]"


def _parse_json_object(payload: bytes) -> JsonObject:
    decoded = cast(object, json.loads(payload))
    if not isinstance(decoded, dict):
        raise TypeError("Expected a JSON object")
    return cast(JsonObject, decoded)


def _string_parameter(params: JsonObject, name: str, default: str | None = None) -> str:
    value = params.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"Repository tool parameter {name!r} must be a string")
    return value


def _integer_parameter(params: JsonObject, name: str, default: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Repository tool parameter {name!r} must be an integer")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Repository tool parameter {name!r} must be an integer") from exc


def _usage_count(value: JsonValue | None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _worker_environment() -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
    }
    if api_key := os.environ.get("OPENCODE_API_KEY"):
        environment["OPENCODE_API_KEY"] = api_key
    return environment


_MAX_GREP_FILES = 15
_MAX_GREP_MATCHES = 30
_MAX_GREP_LINE_CHARS = 240
_MAX_TOOL_OUTPUT_CHARS = 50_000
_SKIP_GREP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
_SKIP_GREP_EXTENSIONS = (
    ".eot",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".map",
    ".pdf",
    ".png",
    ".svg",
    ".tar",
    ".ttf",
    ".woff",
    ".woff2",
    ".zip",
)
_SKIP_GREP_FILES = {"package-lock.json", "pipfile.lock", "yarn.lock"}
_SNAPSHOT_CACHE: WeakKeyDictionary[object, dict[tuple[str, str, str], dict[str, str] | None]] = (
    WeakKeyDictionary()
)


def _grep_candidate(path: str) -> bool:
    parts = path.lower().split("/")
    return (
        not any(part in _SKIP_GREP_DIRS for part in parts[:-1])
        and parts[-1] not in _SKIP_GREP_FILES
        and not parts[-1].endswith(_SKIP_GREP_EXTENSIONS)
    )


_SUBMIT_TEXT_TOOL: JsonObject = {
    "type": "function",
    "function": {
        "name": "submit_text_response",
        "description": "Submit the requested free-form response.",
        "parameters": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        },
    },
}

_SUBMIT_JSON_TOOL: JsonObject = {
    "type": "function",
    "function": {
        "name": "submit_json_response",
        "description": "Submit the JSON object requested by the task instructions.",
        "parameters": {"type": "object"},
    },
}

REPOSITORY_TOOLS: list[JsonObject] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a repository file at the pull request head.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search repository file contents for a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path_glob": {"type": "string"},
                    "ignore_case": {"type": "boolean"},
                    "path_only": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find",
            "description": "Find repository paths matching a glob.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List files and directories below a repository path.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    },
]


class PiLLMProvider:
    """Execute each existing Mira LLM call as an isolated Pi agent session."""

    supports_json_mode: ClassVar[bool] = True
    supports_tool_calling: ClassVar[bool] = True
    owns_agentic_loop = True

    def __init__(self, model: str, thinking_level: str) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._repository_provider: RepositoryProvider | None = None
        self._pr_info: PRInfo | None = None
        self._repo_tree: list[str] = []
        self._file_cache: dict[str, str] = {}
        self._trace_sink: PiTraceSink | None = None

    def set_trace_sink(self, sink: PiTraceSink | None) -> None:
        """Attach a best-effort sink for the worker's live agent events."""
        self._trace_sink = sink

    def _trace(
        self,
        event_type: str,
        detail: str = "",
        data: Mapping[str, object] | None = None,
    ) -> None:
        sink = self._trace_sink
        if sink is None:
            return
        try:
            sink(event_type, detail, dict(data or {}))
        except Exception as exc:
            # Observability must never change the review result.
            logger.debug("Pi trace sink failed: %s", exc)

    async def bind_repository(self, provider: RepositoryProvider, pr_info: PRInfo) -> None:
        """Give Pi read-only access to the PR head through Mira's provider API."""
        self._repository_provider = provider
        self._pr_info = pr_info
        self._repo_tree = await provider.get_repo_tree(pr_info, pr_info.head_sha)
        self._file_cache.clear()

    async def complete(
        self,
        messages: list[dict[str, str]],
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        del temperature, max_tokens
        result = await self._run(
            messages,
            _SUBMIT_JSON_TOOL if json_mode else _SUBMIT_TEXT_TOOL,
        )
        if json_mode:
            return result
        try:
            payload = json.loads(result)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Pi worker returned malformed text submission JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
            raise RuntimeError("Pi worker returned an invalid text submission")
        return cast(str, payload["content"])

    async def complete_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        del temperature, max_tokens
        return await self._run(messages, self._result_tool(tools))

    async def complete_agentic(
        self,
        messages: list,
        tools: list[dict],
        temperature: float | None = None,
    ) -> dict:
        del temperature
        result_tool = self._result_tool(tools)
        result = await self._run(messages, result_tool)
        function = cast(dict, result_tool["function"])
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "pi-result",
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": result,
                    },
                }
            ],
        }

    async def review(self, messages: list[dict[str, str]], temperature: float | None = None) -> str:
        return await self.complete_with_tools(messages, [SUBMIT_REVIEW_TOOL], temperature)

    async def walkthrough(self, messages: list[dict[str, str]]) -> str:
        return await self.complete_with_tools(messages, [SUBMIT_WALKTHROUGH_TOOL])

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    @property
    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.total_prompt_tokens,
            "output_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
        }

    @staticmethod
    def _result_tool(tools: list[dict]) -> dict:
        result_tools = [
            tool
            for tool in tools
            if isinstance(tool.get("function"), dict)
            and str(tool["function"].get("name", "")).startswith("submit_")
        ]
        if len(result_tools) != 1:
            raise ValueError("Pi requires exactly one result submission tool")
        return result_tools[0]

    async def _run(self, messages: list, result_tool: dict) -> str:
        worker = os.environ.get("MIRA_PI_WORKER", "mira-pi-worker")
        try:
            timeout = float(os.environ.get("MIRA_PI_TIMEOUT_SECONDS", "900"))
        except ValueError as exc:
            raise RuntimeError("MIRA_PI_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise RuntimeError("MIRA_PI_TIMEOUT_SECONDS must be greater than zero")

        function = result_tool.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("Pi result tool must have a function name")
        result_tool_name = function["name"]
        run_id = os.urandom(6).hex()
        started_at = time.monotonic()
        trace_base: dict[str, object] = {
            "run_id": run_id,
            "model": self.model,
            "thinking_level": self.thinking_level,
            "result_tool": result_tool_name,
        }
        self._trace(
            "agent_start",
            f"Pi worker started for {result_tool_name}.",
            trace_base,
        )
        with tempfile.TemporaryDirectory(prefix="mira-pi-") as workdir:
            session_root = Path(
                os.environ.get("MIRA_PI_SESSION_DIR", str(Path(workdir) / "sessions"))
            )
            session_dir = session_root / f"{result_tool_name}-{os.urandom(6).hex()}"
            agent_dir = Path(workdir) / "agent"
            session_dir.mkdir(parents=True, exist_ok=True)
            agent_dir.mkdir()
            try:
                process = await asyncio.create_subprocess_exec(
                    worker,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_worker_environment(),
                )
            except OSError as exc:
                detail = f"Unable to start Pi worker {worker!r}: {exc}"
                self._trace("error", detail, trace_base)
                raise RuntimeError(detail) from exc

            stdin = process.stdin
            stdout = process.stdout
            stderr = process.stderr
            assert stdin and stdout and stderr
            stderr_chunks: list[bytes] = []

            async def consume_stderr() -> None:
                while chunk := await stderr.read(4096):
                    stderr_chunks.append(chunk)
                    while sum(map(len, stderr_chunks)) > 16_384:
                        _ = stderr_chunks.pop(0)

            stderr_task = asyncio.create_task(consume_stderr())
            request = {
                "type": "start",
                "messages": messages,
                "tools": [
                    result_tool,
                    *(REPOSITORY_TOOLS if self._pr_info is not None else []),
                ],
                "result_tool": result_tool_name,
                "cwd": workdir,
                "agent_dir": str(agent_dir),
                "session_dir": str(session_dir),
                "model": self.model,
                "thinking_level": self.thinking_level,
            }
            stdin.write((json.dumps(request) + "\n").encode())
            await stdin.drain()

            async def consume() -> JsonValue:
                while line := await stdout.readline():
                    try:
                        message = _parse_json_object(line)
                    except (json.JSONDecodeError, TypeError) as exc:
                        excerpt = line.decode(errors="replace").strip()[:300]
                        raise RuntimeError(
                            f"Pi worker emitted malformed protocol JSON: {excerpt!r}"
                        ) from exc
                    kind = message.get("type")
                    if kind == "tool_request":
                        await self._answer_repository_tool(process, message)
                    elif kind == "done":
                        usage_value = message.get("usage")
                        usage = (
                            cast(JsonObject, usage_value) if isinstance(usage_value, dict) else {}
                        )
                        self.total_prompt_tokens += _usage_count(
                            usage.get("input", usage.get("input_tokens"))
                        )
                        self.total_completion_tokens += _usage_count(
                            usage.get("output", usage.get("output_tokens"))
                        )
                        if "result" not in message:
                            raise RuntimeError("Pi worker completed without a result")
                        result = message["result"]
                        duration_ms = round((time.monotonic() - started_at) * 1000)
                        trace_data = {
                            **trace_base,
                            "result": _trace_value(result),
                            "usage": usage,
                            "duration_ms": duration_ms,
                        }
                        self._trace("result", _trace_text(result), trace_data)
                        self._trace(
                            "agent_end",
                            "Pi worker completed.",
                            {**trace_base, "usage": usage, "duration_ms": duration_ms},
                        )
                        return result
                    elif kind == "error":
                        raise RuntimeError(str(message.get("error") or "Pi worker failed"))
                    elif kind == "thinking_delta":
                        delta = message.get("delta")
                        if isinstance(delta, str) and delta:
                            self._trace(
                                "thinking_delta",
                                delta,
                                {
                                    **trace_base,
                                    "channel": "thinking",
                                    "characters": len(delta),
                                },
                            )
                    elif kind == "text_delta":
                        delta = message.get("delta")
                        if isinstance(delta, str) and delta:
                            self._trace(
                                "text_delta",
                                delta,
                                {
                                    **trace_base,
                                    "channel": "text",
                                    "characters": len(delta),
                                },
                            )
                    elif kind == "stream_boundary":
                        channel = message.get("channel")
                        boundary = message.get("boundary")
                        self._trace(
                            "stream_boundary",
                            "",
                            {
                                **trace_base,
                                "channel": channel,
                                "boundary": boundary,
                            },
                        )
                    elif kind == "tool_start":
                        tool = message.get("tool")
                        args = message.get("args")
                        tool_name = tool if isinstance(tool, str) else "unknown"
                        self._trace(
                            "tool_start",
                            f"{tool_name}({_trace_text(args)})",
                            {
                                **trace_base,
                                "tool": tool_name,
                                "tool_call_id": _trace_value(message.get("id")),
                                "args": _trace_value(args),
                            },
                        )
                    elif kind == "tool_end":
                        tool = message.get("tool")
                        result = message.get("result")
                        tool_name = tool if isinstance(tool, str) else "unknown"
                        is_error = message.get("is_error") is True
                        self._trace(
                            "tool_end",
                            _trace_text(result),
                            {
                                **trace_base,
                                "tool": tool_name,
                                "tool_call_id": _trace_value(message.get("id")),
                                "result": _trace_value(result),
                                "is_error": is_error,
                            },
                        )
                    else:
                        raise RuntimeError(f"Pi worker emitted unknown protocol message: {kind!r}")
                stderr_text = b"".join(stderr_chunks).decode(errors="replace").strip()
                detail = f": {stderr_text}" if stderr_text else ""
                raise RuntimeError(f"Pi worker exited without a result{detail}")

            try:
                try:
                    result = await asyncio.wait_for(consume(), timeout=timeout)
                except TimeoutError as exc:
                    raise RuntimeError(f"Pi worker timed out after {timeout:g} seconds") from exc
                _ = await asyncio.wait_for(process.wait(), timeout=10)
                if not isinstance(result, str):
                    raise RuntimeError("Pi worker returned a non-string submission")
                return result
            except asyncio.CancelledError:
                self._trace("error", "Pi worker cancelled.", {**trace_base, "cancelled": True})
                raise
            except Exception as exc:
                self._trace("error", str(exc) or type(exc).__name__, trace_base)
                raise
            finally:
                if process.returncode is None:
                    process.kill()
                    with contextlib.suppress(ProcessLookupError):
                        _ = await process.wait()
                _ = stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    async def _answer_repository_tool(
        self, process: asyncio.subprocess.Process, message: JsonObject
    ) -> None:
        stdin = process.stdin
        if stdin is None or self._repository_provider is None or self._pr_info is None:
            raise RuntimeError("Pi repository tools are not bound")

        method_value = message.get("method")
        method = method_value if isinstance(method_value, str) else ""
        params_value = message.get("params")
        params = cast(JsonObject, params_value) if isinstance(params_value, dict) else {}
        try:
            result = await self._execute_repository_tool(method, params)
            response: JsonObject = {
                "type": "tool_response",
                "id": message.get("id"),
                "result": result,
            }
        except Exception as exc:
            response = {
                "type": "tool_response",
                "id": message.get("id"),
                "error": str(exc),
            }
        stdin.write((json.dumps(response) + "\n").encode())
        await stdin.drain()

    async def _execute_repository_tool(self, method: str, params: JsonObject) -> str:
        if method == "read":
            path = _string_parameter(params, "path")
            content = await self._read_file(path)
            lines = content.splitlines()
            offset = max(1, _integer_parameter(params, "offset", 1))
            limit = min(500, max(1, _integer_parameter(params, "limit", 200)))
            result = "\n".join(
                f"{number}: {line}"
                for number, line in enumerate(lines[offset - 1 : offset - 1 + limit], offset)
            )
        elif method == "find":
            pattern = _string_parameter(params, "pattern")
            result = "\n".join(path for path in self._repo_tree if fnmatch.fnmatch(path, pattern))
        elif method == "ls":
            prefix = _string_parameter(params, "path", "").strip("/")
            prefix = f"{prefix}/" if prefix else ""
            result = "\n".join(
                sorted(
                    {
                        path[len(prefix) :].split("/", 1)[0]
                        for path in self._repo_tree
                        if path.startswith(prefix)
                    }
                )
            )
        elif method == "grep":
            result = await self._grep(params)
        else:
            raise ValueError(f"Unsupported repository tool: {method}")

        if len(result) > _MAX_TOOL_OUTPUT_CHARS:
            return result[:_MAX_TOOL_OUTPUT_CHARS] + "\n... [tool output truncated]"
        return result

    async def _read_file(self, path: str) -> str:
        if self._repository_provider is None or self._pr_info is None:
            raise RuntimeError("Pi repository tools are not bound")
        if self._repo_tree and path not in self._repo_tree:
            raise ValueError(f"File not found at PR head: {path}")
        if path not in self._file_cache:
            self._file_cache[path] = await self._repository_provider.get_file_content(
                self._pr_info, path, self._pr_info.head_sha
            )
        return self._file_cache[path]

    async def _repository_snapshot(self) -> dict[str, str] | None:
        provider = self._repository_provider
        pr_info = self._pr_info
        if (
            provider is None
            or pr_info is None
            or not isinstance(provider, SnapshotRepositoryProvider)
        ):
            return None
        key = (pr_info.owner, pr_info.repo, pr_info.head_sha)
        try:
            cache = _SNAPSHOT_CACHE.setdefault(provider, {})
        except TypeError:
            return await provider.get_repo_snapshot(pr_info, pr_info.head_sha)
        if key not in cache:
            cache[key] = await provider.get_repo_snapshot(pr_info, pr_info.head_sha)
        return cache[key]

    async def _grep(self, params: JsonObject) -> str:
        pattern = _string_parameter(params, "pattern")
        path_glob = _string_parameter(params, "path_glob", "*")
        if params.get("path_only") is True:
            try:
                regex = re.compile(pattern)
            except re.error:
                return "\n".join(
                    path
                    for path in self._repo_tree
                    if fnmatch.fnmatch(path, path_glob) and pattern in path
                )
            return "\n".join(
                path
                for path in self._repo_tree
                if fnmatch.fnmatch(path, path_glob) and regex.search(path)
            )

        snapshot = await self._repository_snapshot()
        if snapshot is not None:
            self._file_cache.update(snapshot)
            if not self._repo_tree:
                self._repo_tree = list(snapshot)
        max_files = 1_000 if snapshot is not None else _MAX_GREP_FILES

        flags = re.IGNORECASE if params.get("ignore_case") is True else 0
        regex = re.compile(pattern, flags)
        matches: list[str] = []
        files_scanned = 0
        for path in self._repo_tree:
            if files_scanned >= max_files or len(matches) >= _MAX_GREP_MATCHES:
                break
            if not fnmatch.fnmatch(path, path_glob) or not _grep_candidate(path):
                continue
            files_scanned += 1
            try:
                content = await self._read_file(path)
            except Exception:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    snippet = line[:_MAX_GREP_LINE_CHARS]
                    if len(line) > _MAX_GREP_LINE_CHARS:
                        snippet += "…"
                    matches.append(f"{path}:{number}:{snippet}")
                    if len(matches) >= _MAX_GREP_MATCHES:
                        break
        if matches:
            return "\n".join(matches)
        return f"[no matches; scanned {files_scanned} files. Use path_glob to narrow the search.]"
