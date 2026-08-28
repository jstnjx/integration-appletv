"""Synchronize selector commands with shared Apple TV entity state."""

from collections.abc import Awaitable, Callable

from ucapi import StatusCodes

import tv


async def run_synced_selection(
    device: tv.AppleTv,
    handler: Callable[[str], Awaitable[StatusCodes]],
    attribute: str,
    option: str,
) -> StatusCodes:
    """Run a selection handler and broadcast the selected value on success.

    Both media-player source selection and select entities use the same Apple TV
    operation. Broadcasting the selected value immediately keeps every entity
    backed by the same attribute synchronized while pyatv catches up with the
    foreground/Now Playing state.
    """
    result = await handler(option)
    if result == StatusCodes.OK:
        device.events.emit(tv.EVENTS.UPDATE, device.identifier, {attribute: option})
    return result
