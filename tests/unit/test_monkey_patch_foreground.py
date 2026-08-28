"""Tests for the experimental pyatv foreground-app monkey patch."""

from types import SimpleNamespace
from typing import Any, cast

from pyatv.protocols.mrp import protobuf as mrp_protobuf
from pyatv.protocols.mrp.player_state import Client as MrpClient

import monkey_patch


def _client(bundle_identifier: str, display_name: str, process_identifier: int) -> MrpClient:
    client_info = mrp_protobuf.NowPlayingClient()
    client_info.bundleIdentifier = bundle_identifier
    client_info.displayName = display_name
    client_info.processIdentifier = process_identifier
    return MrpClient(client_info)


def test_client_gets_shadow_foreground_flag() -> None:
    client = _client("com.netflix.Netflix", "Netflix", 101)

    assert client.__dict__["_isForeground"] is False


def test_promoting_foreground_client_clears_previous_client() -> None:
    netflix = _client("com.netflix.Netflix", "Netflix", 101)
    youtube = _client("com.google.ios.youtube", "YouTube", 202)
    manager = cast("Any", SimpleNamespace())

    assert monkey_patch._set_foreground_client(manager, netflix, "test") is True
    assert netflix.__dict__["_isForeground"] is True

    assert monkey_patch._set_foreground_client(manager, youtube, "test") is True
    assert netflix.__dict__["_isForeground"] is False
    assert youtube.__dict__["_isForeground"] is True
    assert manager.__dict__["_uc_foreground_client"] is youtube


def test_metadata_app_prefers_experimental_foreground_client() -> None:
    netflix = _client("com.netflix.Netflix", "Netflix", 101)
    youtube = _client("com.google.ios.youtube", "YouTube", 202)
    manager = cast("Any", SimpleNamespace(client=netflix))
    metadata = cast("Any", SimpleNamespace(psm=manager))

    monkey_patch._set_foreground_client(manager, youtube, "test")
    app = monkey_patch.patched_mrp_metadata_app(metadata)

    assert app is not None
    assert app.name == "YouTube"
    assert app.identifier == "com.google.ios.youtube"


def test_metadata_app_falls_back_to_normal_now_playing_client() -> None:
    netflix = _client("com.netflix.Netflix", "Netflix", 101)
    manager = cast("Any", SimpleNamespace(client=netflix))
    metadata = cast("Any", SimpleNamespace(psm=manager))

    app = monkey_patch.patched_mrp_metadata_app(metadata)

    assert app is not None
    assert app.name == "Netflix"
    assert app.identifier == "com.netflix.Netflix"


def test_patch_installation_is_idempotent() -> None:
    monkey_patch.apply_mrp_foreground_app_patch()
    monkey_patch.apply_mrp_foreground_app_patch()
