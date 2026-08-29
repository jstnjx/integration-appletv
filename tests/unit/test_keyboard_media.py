"""Tests for the media-browser Apple TV keyboard workaround."""

from unittest.mock import AsyncMock, MagicMock

from pyatv.const import KeyboardFocusState
import pytest
from ucapi import StatusCodes
from ucapi.media_player import BrowseOptions, SearchOptions

from config import AtvDevice
import media_player
from media_player import AppleTVMediaPlayer


def _media_player() -> tuple[AppleTVMediaPlayer, AtvDevice]:
    device_config = AtvDevice(
        identifier="apple-tv-id",
        name="Living Room Apple TV",
        credentials=[{"protocol": "companion", "credentials": "companion-credentials"}],
        mac_address="apple-tv-id",
    )
    device = MagicMock()
    device.identifier = device_config.identifier
    device.device_config = device_config
    device.attributes = {}
    entity = AppleTVMediaPlayer(device_config, device, MagicMock())
    return entity, device_config


@pytest.mark.asyncio
async def test_browse_exposes_keyboard_search_when_focused(monkeypatch: pytest.MonkeyPatch) -> None:
    entity, device_config = _media_player()
    focus = AsyncMock(return_value=(KeyboardFocusState.Focused, StatusCodes.OK))
    monkeypatch.setattr(media_player, "keyboard_current_focus", focus)

    result = await entity.browse(BrowseOptions())

    assert not isinstance(result, StatusCodes)
    assert result.media is not None
    assert result.media.title == "Apple TV Keyboard"
    assert result.media.can_search is True
    assert result.media.items is not None
    assert result.media.items[0].title == "Keyboard ready"
    focus.assert_awaited_once_with(device_config)


@pytest.mark.asyncio
async def test_search_forwards_query_to_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    entity, device_config = _media_player()
    send_text = AsyncMock(return_value=StatusCodes.OK)
    monkeypatch.setattr(media_player, "keyboard_set_text", send_text)

    result = await entity.search(SearchOptions(query="  Severance  "))

    assert not isinstance(result, StatusCodes)
    assert result.media[0].media_id == "keyboard-sent"
    assert result.media[0].title == "Sent: Severance"
    send_text.assert_awaited_once_with(device_config, "Severance")


@pytest.mark.asyncio
async def test_search_reports_missing_keyboard_focus(monkeypatch: pytest.MonkeyPatch) -> None:
    entity, _ = _media_player()
    monkeypatch.setattr(
        media_player,
        "keyboard_set_text",
        AsyncMock(return_value=StatusCodes.BAD_REQUEST),
    )

    result = await entity.search(SearchOptions(query="Ted Lasso"))

    assert not isinstance(result, StatusCodes)
    assert result.media[0].media_id == "keyboard-not-sent"
    assert result.media[0].title == "Text not sent"
    assert result.media[0].subtitle == "Apple TV keyboard is not focused"


@pytest.mark.asyncio
async def test_empty_search_does_not_send_text(monkeypatch: pytest.MonkeyPatch) -> None:
    entity, _ = _media_player()
    send_text = AsyncMock(return_value=StatusCodes.OK)
    monkeypatch.setattr(media_player, "keyboard_set_text", send_text)

    result = await entity.search(SearchOptions(query="   "))

    assert not isinstance(result, StatusCodes)
    assert result.media == []
    assert result.pagination.count == 0
    send_text.assert_not_awaited()
