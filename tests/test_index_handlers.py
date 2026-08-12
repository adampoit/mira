# pyright: standard

from unittest.mock import AsyncMock, MagicMock

import pytest

from mira.config import MiraConfig
from mira.index.store import IndexStore
from mira.platforms import index_handlers


@pytest.mark.asyncio
async def test_incremental_index_persists_failure(monkeypatch: pytest.MonkeyPatch):
    app_db = MagicMock()
    store = MagicMock()
    store.all_paths.return_value = {"src/existing.py", "src/updated.py"}
    failure = RuntimeError("indexing model unavailable")

    def fake_create_llm(_config: object) -> MagicMock:
        return MagicMock()

    def fake_open_store(*_args: object, **_kwargs: object) -> MagicMock:
        return store

    monkeypatch.setattr(index_handlers, "_get_app_db", lambda: app_db)
    monkeypatch.setattr(index_handlers, "load_config", MiraConfig)
    monkeypatch.setattr(index_handlers, "create_llm", fake_create_llm)
    monkeypatch.setattr(IndexStore, "open", fake_open_store)
    monkeypatch.setattr(index_handlers, "index_diff", AsyncMock(side_effect=failure))

    with pytest.raises(RuntimeError, match="model unavailable"):
        await index_handlers.run_incremental_index(
            owner="acme",
            repo="api",
            fetcher=MagicMock(),
            changed_paths=["src/updated.py"],
            removed_paths=[],
            default_branch="main",
        )

    app_db.set_repo_status.assert_called_once_with(
        "acme",
        "api",
        "failed",
        files_indexed=2,
        error="indexing model unavailable",
        platform="github",
    )
    store.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_incremental_index_records_total_files_when_no_files_need_summaries(
    monkeypatch: pytest.MonkeyPatch,
):
    app_db = MagicMock()
    store = MagicMock()
    store.all_paths.return_value = {"src/a.py", "src/b.py", "src/c.py"}

    def fake_create_llm(_config: object) -> MagicMock:
        return MagicMock()

    def fake_open_store(*_args: object, **_kwargs: object) -> MagicMock:
        return store

    monkeypatch.setattr(index_handlers, "_get_app_db", lambda: app_db)
    monkeypatch.setattr(index_handlers, "load_config", MiraConfig)
    monkeypatch.setattr(index_handlers, "create_llm", fake_create_llm)
    monkeypatch.setattr(IndexStore, "open", fake_open_store)
    monkeypatch.setattr(index_handlers, "index_diff", AsyncMock(return_value=0))

    await index_handlers.run_incremental_index(
        owner="acme",
        repo="api",
        fetcher=MagicMock(),
        changed_paths=["README.md"],
        removed_paths=[],
        default_branch="main",
    )

    app_db.set_repo_status.assert_called_once_with(
        "acme",
        "api",
        "ready",
        files_indexed=3,
        bump_last_indexed=True,
        platform="github",
    )
    store.close.assert_called_once_with()
