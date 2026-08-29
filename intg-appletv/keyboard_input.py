"""Apple TV keyboard input bridge using the Companion protocol.

The Unfolded Circle Remote has no native keyboard entity. This module implements
text entry by reusing the media-player media-search text field and forwarding the
submitted query to the focused tvOS text field via pyatv's Companion keyboard API.

The existing Apple TV integration already pairs the Companion protocol, so this
bridge deliberately reuses the stored Companion credentials and does not require
an additional setup or pairing flow.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

import pyatv
from pyatv.const import KeyboardFocusState, Protocol
from ucapi import StatusCodes

from config import AtvDevice, AtvProtocol

if TYPE_CHECKING:
    from pyatv.interface import AppleTV, BaseConfig

_LOG = logging.getLogger(__name__)
_SCAN_TIMEOUT = 5


@dataclass(slots=True)
class _ClientResult:
    """Result of opening a temporary Companion connection."""

    client: AppleTV | None
    status: StatusCodes


def _companion_credentials(device: AtvDevice) -> str | None:
    """Return persisted Companion credentials for a configured Apple TV."""
    for credential in device.credentials:
        if credential.get("protocol") == AtvProtocol.COMPANION:
            value = credential.get("credentials")
            if value:
                return value
    return None


async def _find_companion_device(device: AtvDevice) -> BaseConfig | None:
    """Resolve a configured Apple TV using only the Companion protocol."""
    loop = asyncio.get_running_loop()
    identifier = device.mac_address or device.identifier
    hosts = [device.address] if device.address else None

    atvs = await pyatv.scan(
        loop,
        identifier=identifier,
        hosts=hosts,
        timeout=_SCAN_TIMEOUT,
        protocol=Protocol.Companion,
    )

    # A manually configured/stored address can become stale. Fall back to an
    # identifier-only scan, matching the resilience of the main connection path.
    if not atvs and hosts:
        atvs = await pyatv.scan(
            loop,
            identifier=identifier,
            timeout=_SCAN_TIMEOUT,
            protocol=Protocol.Companion,
        )

    return atvs[0] if atvs else None


async def _open_client(device: AtvDevice) -> _ClientResult:
    """Open an authenticated, Companion-only pyatv connection."""
    credentials = _companion_credentials(device)
    if not credentials:
        _LOG.warning("[%s] No Companion credentials available for keyboard input", device.name)
        return _ClientResult(None, StatusCodes.UNAUTHORIZED)

    try:
        config = await _find_companion_device(device)
        if config is None:
            _LOG.debug("[%s] Apple TV not found for keyboard input", device.name)
            return _ClientResult(None, StatusCodes.SERVICE_UNAVAILABLE)

        if config.get_service(Protocol.Companion) is None:
            _LOG.debug("[%s] Companion protocol is unavailable for keyboard input", device.name)
            return _ClientResult(None, StatusCodes.NOT_IMPLEMENTED)

        if not config.set_credentials(Protocol.Companion, credentials):
            _LOG.warning("[%s] Could not apply Companion credentials for keyboard input", device.name)
            return _ClientResult(None, StatusCodes.UNAUTHORIZED)

        client = await pyatv.connect(
            config,
            asyncio.get_running_loop(),
            protocol=Protocol.Companion,
        )
        return _ClientResult(client, StatusCodes.OK)
    except (TimeoutError, pyatv.exceptions.OperationTimeoutError):
        _LOG.warning("[%s] Keyboard Companion connection timed out", device.name)
        return _ClientResult(None, StatusCodes.TIMEOUT)
    except (
        pyatv.exceptions.AuthenticationError,
        pyatv.exceptions.NoCredentialsError,
        pyatv.exceptions.InvalidCredentialsError,
    ):
        _LOG.warning("[%s] Keyboard Companion authentication failed", device.name)
        return _ClientResult(None, StatusCodes.UNAUTHORIZED)
    except (
        pyatv.exceptions.ConnectionFailedError,
        pyatv.exceptions.ConnectionLostError,
        OSError,
    ) as err:
        _LOG.warning("[%s] Keyboard Companion connection failed: %s", device.name, err)
        return _ClientResult(None, StatusCodes.SERVICE_UNAVAILABLE)
    except Exception as err:  # noqa: BLE001 - isolate the optional keyboard workaround
        _LOG.exception("[%s] Unexpected keyboard Companion connection error: %s", device.name, err)
        return _ClientResult(None, StatusCodes.SERVER_ERROR)


async def _close_client(client: AppleTV) -> None:
    """Close a temporary pyatv connection and drain its shutdown tasks."""
    pending = client.close()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def current_focus(device: AtvDevice) -> tuple[KeyboardFocusState, StatusCodes]:
    """Return the current tvOS keyboard focus state."""
    result = await _open_client(device)
    if result.client is None:
        return KeyboardFocusState.Unknown, result.status

    try:
        return result.client.keyboard.text_focus_state, StatusCodes.OK
    except pyatv.exceptions.NotSupportedError:
        _LOG.debug("[%s] tvOS keyboard focus is not supported", device.name)
        return KeyboardFocusState.Unknown, StatusCodes.NOT_IMPLEMENTED
    except pyatv.exceptions.BlockedStateError:
        _LOG.debug("[%s] Keyboard connection closed while reading focus", device.name)
        return KeyboardFocusState.Unknown, StatusCodes.SERVICE_UNAVAILABLE
    except Exception as err:  # noqa: BLE001 - optional UI status must not affect playback
        _LOG.exception("[%s] Could not read tvOS keyboard focus: %s", device.name, err)
        return KeyboardFocusState.Unknown, StatusCodes.SERVER_ERROR
    finally:
        await _close_client(result.client)


async def set_text(device: AtvDevice, text: str) -> StatusCodes:
    """Replace the focused tvOS text field with the supplied text."""
    if not text:
        return StatusCodes.BAD_REQUEST

    result = await _open_client(device)
    if result.client is None:
        return result.status

    try:
        focus = result.client.keyboard.text_focus_state
        if focus != KeyboardFocusState.Focused:
            return StatusCodes.BAD_REQUEST

        await result.client.keyboard.text_set(text)
        return StatusCodes.OK
    except pyatv.exceptions.NotSupportedError:
        _LOG.debug("[%s] tvOS keyboard text entry is not supported", device.name)
        return StatusCodes.NOT_IMPLEMENTED
    except pyatv.exceptions.BlockedStateError:
        _LOG.debug("[%s] Keyboard connection closed while sending text", device.name)
        return StatusCodes.SERVICE_UNAVAILABLE
    except (TimeoutError, pyatv.exceptions.OperationTimeoutError):
        _LOG.warning("[%s] Sending keyboard text timed out", device.name)
        return StatusCodes.TIMEOUT
    except (
        pyatv.exceptions.AuthenticationError,
        pyatv.exceptions.NoCredentialsError,
        pyatv.exceptions.InvalidCredentialsError,
    ):
        _LOG.warning("[%s] Keyboard text authentication failed", device.name)
        return StatusCodes.UNAUTHORIZED
    except (
        pyatv.exceptions.ConnectionFailedError,
        pyatv.exceptions.ConnectionLostError,
        OSError,
    ) as err:
        _LOG.warning("[%s] Keyboard text connection failed: %s", device.name, err)
        return StatusCodes.SERVICE_UNAVAILABLE
    except Exception as err:  # noqa: BLE001 - keyboard input must remain isolated
        _LOG.exception("[%s] Could not send tvOS keyboard text: %s", device.name, err)
        return StatusCodes.SERVER_ERROR
    finally:
        await _close_client(result.client)
