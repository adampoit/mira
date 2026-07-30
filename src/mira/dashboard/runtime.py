"""Runtime services supplied by the unified webhook server."""

from typing import Protocol


class InstallationTokenAuth(Protocol):
    async def get_installation_token(self, installation_id: int) -> str: ...


github_app_auth: InstallationTokenAuth | None = None
bot_name = "miracodeai"


def configure_runtime(
    app_auth: InstallationTokenAuth | None,
    configured_bot_name: str,
) -> None:
    global github_app_auth, bot_name
    github_app_auth = app_auth
    bot_name = configured_bot_name
