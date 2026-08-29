"""Setup-flow resilience tests."""

from unittest.mock import AsyncMock, MagicMock

import pyatv
from pyatv.const import Protocol
import pytest
from ucapi import DriverSetupRequest, IntegrationSetupError, SetupError

from config import AtvDevice
import setup_flow
from tv import AppleTv


@pytest.mark.asyncio
async def test_setup_handler_contains_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(_msg: DriverSetupRequest) -> SetupError:
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(setup_flow, "_handle_driver_setup", fail)
    result = await setup_flow.driver_setup_handler(DriverSetupRequest(reconfigure=False, setup_data={}))
    assert isinstance(result, SetupError)
    assert result.error_type == IntegrationSetupError.CONNECTION_REFUSED


@pytest.mark.asyncio
async def test_setup_handler_contains_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(_msg: DriverSetupRequest) -> SetupError:
        raise TimeoutError

    monkeypatch.setattr(setup_flow, "_handle_driver_setup", fail)
    result = await setup_flow.driver_setup_handler(DriverSetupRequest(reconfigure=False, setup_data={}))
    assert isinstance(result, SetupError)
    assert result.error_type == IntegrationSetupError.TIMEOUT


@pytest.mark.asyncio
async def test_pairing_retries_after_refused_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    device = AtvDevice(
        identifier="apple-tv-id",
        name="Living Room Apple TV",
        credentials=[],
        mac_address="apple-tv-id",
    )
    initial_config = MagicMock()
    initial_config.all_identifiers = {"apple-tv-id"}
    refreshed_config = MagicMock()
    refreshed_config.all_identifiers = {"apple-tv-id"}
    atv = AppleTv(device, pairing_atv=initial_config)

    first = MagicMock()
    first.begin = AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused"))
    first.close = AsyncMock()
    second = MagicMock()
    second.begin = AsyncMock()
    second.close = AsyncMock()
    second.device_provides_pin = True

    pair_mock = AsyncMock(side_effect=[first, second])
    scan_mock = AsyncMock(return_value=[refreshed_config])
    monkeypatch.setattr(pyatv, "pair", pair_mock)
    monkeypatch.setattr(pyatv, "scan", scan_mock)

    result = await atv.start_pairing(Protocol.AirPlay, "Test AirPlay")

    assert result == 0
    assert pair_mock.await_count == 2
    first.close.assert_awaited_once()
    scan_mock.assert_awaited_once()
    assert atv._pairing_atv is refreshed_config  # noqa: SLF001


@pytest.mark.asyncio
async def test_finish_pairing_cleans_up_on_failure() -> None:
    device = AtvDevice(identifier="apple-tv-id", name="Apple TV", credentials=[])
    atv = AppleTv(device, pairing_atv=MagicMock())
    pairing = MagicMock()
    pairing.finish = AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused"))
    pairing.close = AsyncMock()
    atv._pairing_process = pairing  # noqa: SLF001

    with pytest.raises(ConnectionRefusedError):
        await atv.finish_pairing()

    pairing.close.assert_awaited_once()
    assert atv._pairing_process is None  # noqa: SLF001
