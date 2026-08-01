"""Runtime services supplied by the unified webhook server."""

from typing import Protocol


class InstallationTokenAuth(Protocol):
    async def get_installation_token(self, installation_id: int) -> str: ...


github_app_auth: InstallationTokenAuth | None = None
gitlab_auth: object | None = None
forgejo_auth: object | None = None
bot_name = "miracodeai"


def configure_runtime(
    app_auth: InstallationTokenAuth | None,
    configured_bot_name: str,
    *,
    configured_gitlab_auth: object | None = None,
    configured_forgejo_auth: object | None = None,
) -> None:
    global github_app_auth, gitlab_auth, forgejo_auth, bot_name
    github_app_auth = app_auth
    gitlab_auth = configured_gitlab_auth
    forgejo_auth = configured_forgejo_auth
    bot_name = configured_bot_name
