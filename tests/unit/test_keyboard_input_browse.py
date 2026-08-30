"""Regression tests for connection-free keyboard media browsing."""

from unittest.mock import AsyncMock

from pyatv.const import KeyboardFocusState
import pytest
from ucapi import StatusCodes

from config import AtvDevice
import keyboard_input


@pytest.mark.asyncio
async def test_current_focus_does_not_open_companion_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    device = AtvDevice(
        identifier="apple-tv-id",
        name="Living Room Apple TV",
        credentials=[{"protocol": "companion", "credentials": "companion-credentials"}],
        mac_address="apple-tv-id",
    )
    open_client = AsyncMock(side_effect=AssertionError("browse must not open a Companion connection"))
    monkeypatch.setattr(keyboard_input, "_open_client", open_client)

    focus, status = await keyboard_input.current_focus(device)

    assert focus == KeyboardFocusState.Unknown
    assert status == StatusCodes.OK
    open_client.assert_not_awaited()
