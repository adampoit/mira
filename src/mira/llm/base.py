# pyright: standard

"""Provider protocol — the interface that all LLM backends must satisfy."""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from mira.models import PRInfo


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """Structural interface for LLM providers.

    Both the OpenAI-compatible provider and direct-API providers
    (Bedrock, Anthropic, Vertex, etc.) satisfy this protocol.

    Capability annotations:
        supports_json_mode: Provider natively supports response_format=json_object.
        supports_tool_calling: Provider supports function/tool calling.
    """

    supports_json_mode: ClassVar[bool]
    supports_tool_calling: ClassVar[bool]

    total_prompt_tokens: int
    total_completion_tokens: int

    async def complete(
        self,
        messages: list[dict[str, str]],
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...

    async def complete_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...

    async def complete_agentic(
        self,
        messages: list,
        tools: list[dict],
        temperature: float | None = None,
    ) -> dict: ...

    async def review(
        self, messages: list[dict[str, str]], temperature: float | None = None
    ) -> str: ...

    async def walkthrough(self, messages: list[dict[str, str]]) -> str: ...

    def count_tokens(self, text: str) -> int: ...

    @property
    def usage(self) -> dict[str, int]: ...


@runtime_checkable
class NativeAgentLoopProvider(Protocol):
    """Provider whose ``review`` method owns the complete tool-use loop."""

    owns_agentic_loop: bool


class RepositoryProvider(Protocol):
    """Read-only repository operations exposed to an agentic LLM provider."""

    async def get_repo_tree(self, pr_info: PRInfo, ref: str) -> list[str]: ...

    async def get_file_content(self, pr_info: PRInfo, path: str, ref: str) -> str: ...


@runtime_checkable
class SnapshotRepositoryProvider(Protocol):
    """Optional bulk repository access used to make agent search efficient."""

    async def get_repo_snapshot(self, pr_info: PRInfo, ref: str) -> dict[str, str] | None: ...


@runtime_checkable
class RepositoryAwareProvider(Protocol):
    """Optional capability for providers that expose repository tools."""

    async def bind_repository(self, provider: RepositoryProvider, pr_info: PRInfo) -> None: ...
