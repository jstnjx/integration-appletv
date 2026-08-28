"""Tests for synchronized selector/media-player state updates."""

from typing import Any, cast

import pytest
from ucapi import StatusCodes
from ucapi.media_player import Attributes

from selection_sync import run_synced_selection
import tv


class _Events:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def emit(self, event: Any, *args: Any) -> None:
        self.calls.append((event, *args))


class _Device:
    def __init__(self) -> None:
        self.identifier = "atv-test"
        self.events = _Events()


@pytest.mark.asyncio
async def test_successful_selection_broadcasts_shared_source() -> None:
    device = _Device()
    selected: list[str] = []

    async def handler(option: str) -> StatusCodes:
        selected.append(option)
        return StatusCodes.OK

    result = await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "Netflix",
    )

    assert result == StatusCodes.OK
    assert selected == ["Netflix"]
    assert device.events.calls == [
        (tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE.value: "Netflix"}),
    ]


@pytest.mark.asyncio
async def test_failed_selection_does_not_broadcast_source() -> None:
    device = _Device()

    async def handler(_option: str) -> StatusCodes:
        return StatusCodes.BAD_REQUEST

    result = await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "Netflix",
    )

    assert result == StatusCodes.BAD_REQUEST
    assert device.events.calls == []
