"""Tests for the lightweight foreground-app source watcher."""

from typing import Any

import pytest
from ucapi import media_player

import driver

DEVICE_ID = "test-apple-tv"


class FakeAppleTv:
    """Minimal AppleTv stand-in exposing a sequence of cached app names."""

    def __init__(self, values: list[str | None]) -> None:
        self._values = iter(values)

    @property
    def app_name(self) -> str:
        value = next(self._values)
        if value is None:
            driver._configured_atvs.pop(DEVICE_ID, None)  # noqa: SLF001
            return ""
        return value


@pytest.mark.asyncio
async def test_source_watcher_publishes_only_real_app_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated samples are ignored while a new foreground app publishes SOURCE."""
    atv = FakeAppleTv(["App Store", "App Store", "Netflix", None])
    updates: list[tuple[str, dict[str, Any]]] = []

    def capture_update(device_id: str, update: dict[str, Any]) -> None:
        updates.append((device_id, update))

    monkeypatch.setattr(driver, "SOURCE_POLL_INTERVAL", 0)
    monkeypatch.setattr(driver, "on_atv_update", capture_update)
    driver._configured_atvs[DEVICE_ID] = atv  # type: ignore[assignment]  # noqa: SLF001

    await driver._watch_atv_source(DEVICE_ID, atv)  # type: ignore[arg-type]  # noqa: SLF001

    assert updates == [
        (DEVICE_ID, {media_player.Attributes.SOURCE: "App Store"}),
        (DEVICE_ID, {media_player.Attributes.SOURCE: "Netflix"}),
    ]
