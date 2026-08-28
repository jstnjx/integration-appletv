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
        self.log_id = "Apple TV Test"
        self.events = _Events()
        self.app_name = "Netflix"
        self.output_devices = "TV"


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
        "YouTube",
    )

    assert result == StatusCodes.OK
    assert selected == ["YouTube"]
    assert device.events.calls == [
        (tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE.value: "YouTube"}),
    ]


@pytest.mark.asyncio
async def test_stale_previous_app_cannot_overwrite_selected_app() -> None:
    device = _Device()

    async def handler(_option: str) -> StatusCodes:
        return StatusCodes.OK

    await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "YouTube",
    )

    # pyatv still reports the app that previously owned Now Playing. The stale
    # source is stripped, while unrelated attributes continue to propagate.
    device.events.emit(
        tv.EVENTS.UPDATE,
        "atv-test",
        {
            Attributes.SOURCE: "Netflix",
            Attributes.STATE: "STANDBY",
        },
    )

    assert device.events.calls[-1] == (
        tv.EVENTS.UPDATE,
        "atv-test",
        {Attributes.STATE: "STANDBY"},
    )

    # Empty source resets caused by idle/stopped playback must not clear the
    # selected app either.
    call_count = len(device.events.calls)
    device.events.emit(tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE: ""})
    assert len(device.events.calls) == call_count


@pytest.mark.asyncio
async def test_selected_app_confirmation_releases_stale_guard() -> None:
    device = _Device()

    async def handler(_option: str) -> StatusCodes:
        return StatusCodes.OK

    await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "YouTube",
    )

    # Simulate pyatv genuinely switching its backing app to the requested app.
    device.app_name = "YouTube"
    device.events.emit(tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE: "YouTube"})
    assert device.events.calls[-1] == (
        tv.EVENTS.UPDATE,
        "atv-test",
        {Attributes.SOURCE: "YouTube"},
    )

    # Once confirmed, ordinary backing-state changes are accepted again.
    device.app_name = "Netflix"
    device.events.emit(tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE: "Netflix"})
    assert device.events.calls[-1] == (
        tv.EVENTS.UPDATE,
        "atv-test",
        {Attributes.SOURCE: "Netflix"},
    )


@pytest.mark.asyncio
async def test_new_selection_keeps_prior_requested_apps_stale() -> None:
    device = _Device()

    async def handler(_option: str) -> StatusCodes:
        return StatusCodes.OK

    await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "YouTube",
    )
    await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "Spotify",
    )

    # A delayed YouTube update from the previous command must not overwrite the
    # newer Spotify selection.
    call_count = len(device.events.calls)
    device.events.emit(tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE: "YouTube"})
    assert len(device.events.calls) == call_count


@pytest.mark.asyncio
async def test_genuinely_different_app_supersedes_selection() -> None:
    device = _Device()

    async def handler(_option: str) -> StatusCodes:
        return StatusCodes.OK

    await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "YouTube",
    )

    # A third app that was neither the selected target nor the stale previous
    # Now Playing app represents meaningful new state and is allowed through.
    device.app_name = "Spotify"
    device.events.emit(tv.EVENTS.UPDATE, "atv-test", {Attributes.SOURCE: "Spotify"})
    assert device.events.calls[-1] == (
        tv.EVENTS.UPDATE,
        "atv-test",
        {Attributes.SOURCE: "Spotify"},
    )


@pytest.mark.asyncio
async def test_failed_selection_does_not_broadcast_source() -> None:
    device = _Device()

    async def handler(_option: str) -> StatusCodes:
        return StatusCodes.BAD_REQUEST

    result = await run_synced_selection(
        cast("tv.AppleTv", device),
        handler,
        Attributes.SOURCE.value,
        "YouTube",
    )

    assert result == StatusCodes.BAD_REQUEST
    assert device.events.calls == []
