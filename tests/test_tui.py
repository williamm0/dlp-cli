import asyncio
from pathlib import Path

from dlp.config import SettingsRepository
from dlp.dependencies import DependencyName, DependencyStatus
from dlp.models import DownloadRequest, JobState, QueueItem, Settings
from dlp.ui import DownloaderApp
from dlp.ui.app import DependencyDialog


def test_tui_renders_download_queue_and_settings() -> None:
    async def scenario() -> None:
        app = DownloaderApp()
        async with app.run_test() as pilot:
            assert app.query_one("#url-input")
            assert app.query_one("#queue-table")
            assert app.query_one("#save-settings")
            await pilot.press("s")
            assert app.query_one("#tabs").active == "settings-tab"

    asyncio.run(scenario())


def test_tui_retry_resets_failed_item_and_increments_retry_count() -> None:
    async def scenario() -> None:
        app = DownloaderApp()
        started: list[str] = []
        app._start_or_prompt = lambda requests: started.extend(  # type: ignore[method-assign]
            request.job_id for request in requests
        )
        async with app.run_test():
            request = DownloadRequest("job-1", "https://example.com/video", Settings())
            app.items[request.job_id] = QueueItem(request, state=JobState.FAILED, error="failed")
            app._refresh_queue()
            app.action_retry()

            assert app.items[request.job_id].state == JobState.QUEUED
            assert app.items[request.job_id].retry_count == 1
            assert started == ["job-1"]

    asyncio.run(scenario())


def test_tui_dependency_dialog_exposes_install_retry_and_skip() -> None:
    async def scenario() -> None:
        app = DownloaderApp()
        async with app.run_test() as pilot:
            app.push_screen(
                DependencyDialog(
                    [
                        DependencyStatus(
                            DependencyName.FFMPEG,
                            available=False,
                            required=True,
                            reason="merge support",
                            manual_command="Install ffmpeg manually",
                        )
                    ]
                )
            )
            await pilot.pause()
            for button_id in (
                "#install-dependencies",
                "#retry-dependencies",
                "#skip-dependencies",
            ):
                assert app.screen.query_one(button_id)

    asyncio.run(scenario())


def test_tui_saves_settings_and_preserves_multiline_queue_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = DownloaderApp()
        app.repository = SettingsRepository(tmp_path / "config.toml")
        app._start_or_prompt = lambda _requests: None  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            url_input = app.query_one("#url-input")
            url_input.text = "https://example.com/one\n# skip\nhttps://example.com/two"
            await pilot.click("#add-button")
            assert [item.request.url for item in app.items.values()] == [
                "https://example.com/one",
                "https://example.com/two",
            ]

            await pilot.press("s")
            app._save_settings()
            assert app.repository.path.exists()
            assert SettingsRepository(app.repository.path).load().quality_mode == "best"

    asyncio.run(scenario())
