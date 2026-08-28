#!/usr/bin/env python3
"""
This module handles monkey patching of the pyatv library.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""
# pyright: reportPrivateUsage=false

from collections.abc import Awaitable, Callable
from copy import copy
import logging
import time
from typing import Any, cast

from pyatv import exceptions
from pyatv.auth import hap_tlv8
from pyatv.auth.hap_pairing import HapCredentials, PairSetupProcedure
from pyatv.auth.hap_srp import SRPAuthHandler
from pyatv.interface import App
from pyatv.protocols.airplay.auth.hap import _AIRPLAY_HEADERS, AirPlayHapPairSetupProcedure, _get_pairing_data
from pyatv.protocols.airplay.auth.legacy import AirPlayLegacyPairSetupProcedure
from pyatv.protocols.airplay.pairing import AirPlayMajorVersion, AirPlayPairingHandler, AuthenticationType
from pyatv.protocols.airplay.srp import LegacySRPAuthHandler, new_credentials
from pyatv.protocols.mrp import MrpMetadata, protobuf as mrp_protobuf
from pyatv.protocols.mrp.player_state import Client as MrpClient
from pyatv.protocols.mrp.player_state import PlayerStateManager
from pyatv.support import error_handler
from pyatv.support.http import HttpConnection, http_connect

_LOG = logging.getLogger(__name__)
HapPairSetupProcedureFactory = Callable[[HttpConnection, SRPAuthHandler, str | None], AirPlayHapPairSetupProcedure]

# Apple MediaRemote internally keeps an `_isForeground` flag on its now-playing client model, but that private ivar is
# not part of pyatv's public API and is not known to be serialized over MRP. This experimental patch mirrors the flag
# locally on pyatv Client objects and treats post-bootstrap UPDATE_CLIENT_MESSAGE traffic as a foreground hint. If tvOS
# does emit a client update when an application enters the foreground, metadata.app can therefore follow that app even
# before it starts playback. If it does not, normal pyatv now-playing behavior remains the fallback.
_FOREGROUND_BOOTSTRAP_SECONDS = 3.0
_FOREGROUND_FLAG = "_isForeground"
_FOREGROUND_CLIENT = "_uc_foreground_client"
_FOREGROUND_READY_AT = "_uc_foreground_ready_at"
_MRP_FOREGROUND_PATCH_APPLIED = False

_ORIGINAL_MRP_CLIENT_INIT = MrpClient.__init__
_ORIGINAL_PSM_INIT = PlayerStateManager.__init__
_ORIGINAL_PSM_HANDLE_UPDATE_CLIENT = cast(
    "Callable[[PlayerStateManager, Any], Awaitable[None]]",
    getattr(PlayerStateManager, "_handle_update_client"),
)
_ORIGINAL_PSM_HANDLE_REMOVE_CLIENT = cast(
    "Callable[[PlayerStateManager, Any], Awaitable[None]]",
    getattr(PlayerStateManager, "_handle_remove_client"),
)
_ORIGINAL_MRP_METADATA_APP_GETTER = MrpMetadata.app.fget


def patched_airplay_hap_pair_setup(
    auth_type: AuthenticationType,
    connection: HttpConnection,
    display_name: str | None = None,
) -> PairSetupProcedure:
    """Return Pair-Setup procedure with an optional receiver-visible name."""
    _LOG.debug("Setting up new AirPlay Pair-Setup procedure with type %s", auth_type)

    if auth_type == AuthenticationType.Legacy:
        legacy_srp = LegacySRPAuthHandler(new_credentials())
        legacy_srp.initialize()
        return AirPlayLegacyPairSetupProcedure(connection, legacy_srp)
    if auth_type == AuthenticationType.HAP:
        srp = SRPAuthHandler()
        srp.initialize()
        hap_pair_setup_procedure = cast("HapPairSetupProcedureFactory", AirPlayHapPairSetupProcedure)
        return hap_pair_setup_procedure(connection, srp, display_name)

    msg = f"authentication type {auth_type} does not support Pair-Setup"
    raise exceptions.NotSupportedError(msg)


def patched_airplay_hap_pair_setup_procedure_init(
    self: AirPlayHapPairSetupProcedure,
    http: HttpConnection,
    auth_handler: SRPAuthHandler,
    display_name: str | None = None,
):
    """Initialize HAP pairing with an optional receiver-visible name."""
    self.http = http
    self.srp = auth_handler
    headers = copy(_AIRPLAY_HEADERS)
    if display_name:
        headers["X-Apple-Client-Name"] = display_name
    setup = cast("Any", self)
    setup._headers = headers  # noqa: SLF001
    self._atv_salt = None
    self._atv_pub_key = None


async def patched_airplay_hap_pair_setup_procedure_start_pairing(self: AirPlayHapPairSetupProcedure) -> None:
    """Start the authentication process.

    This method will show the expected PIN on screen.
    """
    self.srp.initialize()
    headers = cast("Any", self)._headers  # noqa: SLF001

    await self.http.post("/pair-pin-start", headers=headers)

    data = {hap_tlv8.TlvValue.Method: b"\x00", hap_tlv8.TlvValue.SeqNo: b"\x01"}
    resp = await self.http.post("/pair-setup", body=hap_tlv8.write_tlv(data), headers=headers)
    pairing_data = _get_pairing_data(resp)

    self._atv_salt = pairing_data[hap_tlv8.TlvValue.Salt]
    self._atv_pub_key = pairing_data[hap_tlv8.TlvValue.PublicKey]


async def patched_airplay_hap_pair_setup_procedure_finish_pairing(
    self: AirPlayHapPairSetupProcedure,
    username: str,  # noqa: ARG001
    pin_code: int,
    display_name: str | None,
) -> HapCredentials:
    """Finish authentication process.

    A username (generated by new_credentials) and the PIN code shown on
    screen must be provided.
    """
    # Step 1
    self.srp.step1(pin_code)
    headers = cast("Any", self)._headers  # noqa: SLF001

    pub_key, proof = self.srp.step2(self._atv_pub_key, self._atv_salt)
    data = {
        hap_tlv8.TlvValue.SeqNo: b"\x03",
        hap_tlv8.TlvValue.PublicKey: pub_key,
        hap_tlv8.TlvValue.Proof: proof,
    }
    await self.http.post("/pair-setup", body=hap_tlv8.write_tlv(data), headers=headers)

    data = {
        hap_tlv8.TlvValue.SeqNo: b"\x05",
        hap_tlv8.TlvValue.EncryptedData: self.srp.step3(name=display_name),
    }
    resp = await self.http.post("/pair-setup", body=hap_tlv8.write_tlv(data), headers=headers)
    pairing_data = _get_pairing_data(resp)

    encrypted_data = pairing_data[hap_tlv8.TlvValue.EncryptedData]
    return self.srp.step4(encrypted_data)


async def patched_airplay_pairing_begin(self: AirPlayPairingHandler) -> None:
    """Start pairing process."""
    self.http = await http_connect(self.address, self.service.port)
    self.pairing_procedure = patched_airplay_hap_pair_setup(
        (
            AuthenticationType.HAP
            if self.airplay_version == AirPlayMajorVersion.AirPlayV2
            else AuthenticationType.Legacy
        ),
        self.http,
        self._name,
    )
    self._has_paired = False
    return await error_handler(self.pairing_procedure.start_pairing, exceptions.PairingError)


def patched_mrp_client_init(self: MrpClient, client: Any) -> None:
    """Add a local shadow of MediaRemote's private `_isForeground` state to an MRP client."""
    _ORIGINAL_MRP_CLIENT_INIT(self, client)
    setattr(self, _FOREGROUND_FLAG, False)


def patched_mrp_player_state_manager_init(self: PlayerStateManager, protocol: Any) -> None:
    """Initialize experimental foreground tracking after pyatv initializes its normal MRP state."""
    _ORIGINAL_PSM_INIT(self, protocol)
    setattr(self, _FOREGROUND_CLIENT, None)
    setattr(self, _FOREGROUND_READY_AT, time.monotonic() + _FOREGROUND_BOOTSTRAP_SECONDS)


def _set_foreground_client(manager: PlayerStateManager, client: MrpClient, reason: str) -> bool:
    """Promote one MRP client to the local foreground slot and clear the previous flag."""
    current = cast("MrpClient | None", getattr(manager, _FOREGROUND_CLIENT, None))
    if current is client and bool(getattr(client, _FOREGROUND_FLAG, False)):
        return False

    if current is not None:
        setattr(current, _FOREGROUND_FLAG, False)

    setattr(client, _FOREGROUND_FLAG, True)
    setattr(manager, _FOREGROUND_CLIENT, client)
    _LOG.info(
        "Experimental MRP foreground app: %s (%s), reason=%s",
        client.display_name or "unknown",
        client.bundle_identifier,
        reason,
    )
    return True


async def _force_mrp_state_update(manager: PlayerStateManager) -> None:
    """Force pyatv's push updater to rebuild metadata after a foreground-only change."""
    state_updated = cast("Callable[..., Awaitable[Any]]", getattr(manager, "_state_updated"))
    await state_updated()


async def patched_mrp_handle_update_client(self: PlayerStateManager, message: Any) -> None:
    """Treat post-bootstrap MRP client updates as an experimental foreground-app hint."""
    update_client = mrp_protobuf.extract_inner(message)
    client_info = update_client.client
    bundle_identifier = client_info.bundleIdentifier
    process_identifier = client_info.processIdentifier if client_info.HasField("processIdentifier") else 0
    display_name = client_info.displayName if client_info.HasField("displayName") else ""
    visibility = client_info.nowPlayingVisibility if client_info.HasField("nowPlayingVisibility") else None

    await _ORIGINAL_PSM_HANDLE_UPDATE_CLIENT(self, message)

    ready_at = float(getattr(self, _FOREGROUND_READY_AT, 0.0))
    bootstrap = time.monotonic() < ready_at
    _LOG.debug(
        "Experimental MRP client update: app=%s bundle=%s pid=%s nowPlayingVisibility=%s bootstrap=%s",
        display_name or "unknown",
        bundle_identifier or "unknown",
        process_identifier,
        visibility,
        bootstrap,
    )

    # A connection initially replays UPDATE_CLIENT_MESSAGE for every known media client. Do not interpret that burst as
    # foreground lifecycle. After the bootstrap window, a full client identity update is treated as a foreground hint.
    if bootstrap or not bundle_identifier or process_identifier <= 0:
        return

    client = self.get_client(client_info)
    if _set_foreground_client(self, client, "UPDATE_CLIENT_MESSAGE"):
        await _force_mrp_state_update(self)


async def patched_mrp_handle_remove_client(self: PlayerStateManager, message: Any) -> None:
    """Clear the experimental foreground slot when that MRP client is removed."""
    client_to_remove = mrp_protobuf.extract_inner(message).client
    foreground = cast("MrpClient | None", getattr(self, _FOREGROUND_CLIENT, None))
    removed_foreground = foreground is not None and foreground.bundle_identifier == client_to_remove.bundleIdentifier

    await _ORIGINAL_PSM_HANDLE_REMOVE_CLIENT(self, message)

    if removed_foreground and foreground is not None:
        setattr(foreground, _FOREGROUND_FLAG, False)
        setattr(self, _FOREGROUND_CLIENT, None)
        _LOG.info("Experimental MRP foreground app removed: %s", client_to_remove.bundleIdentifier)
        await _force_mrp_state_update(self)


def patched_mrp_metadata_app(self: MrpMetadata) -> App | None:
    """Prefer the experimental foreground client, otherwise retain pyatv's normal Now Playing app behavior."""
    manager = cast("PlayerStateManager", getattr(self, "psm"))
    foreground = cast("MrpClient | None", getattr(manager, _FOREGROUND_CLIENT, None))
    if foreground is not None and bool(getattr(foreground, _FOREGROUND_FLAG, False)):
        return App(foreground.display_name, foreground.bundle_identifier)

    if _ORIGINAL_MRP_METADATA_APP_GETTER is None:
        return None
    return _ORIGINAL_MRP_METADATA_APP_GETTER(self)


def apply_mrp_foreground_app_patch() -> None:
    """Install the experimental pyatv foreground-app monkey patch once."""
    global _MRP_FOREGROUND_PATCH_APPLIED
    if _MRP_FOREGROUND_PATCH_APPLIED:
        return

    setattr(MrpClient, "__init__", patched_mrp_client_init)
    setattr(PlayerStateManager, "__init__", patched_mrp_player_state_manager_init)
    setattr(PlayerStateManager, "_handle_update_client", patched_mrp_handle_update_client)
    setattr(PlayerStateManager, "_handle_remove_client", patched_mrp_handle_remove_client)
    setattr(MrpMetadata, "app", property(patched_mrp_metadata_app))
    _MRP_FOREGROUND_PATCH_APPLIED = True
    _LOG.warning(
        "Enabled experimental pyatv MRP foreground-app patch; `_isForeground` is inferred from post-bootstrap client "
        "updates and is not read directly from tvOS MediaRemote memory"
    )


# This module is imported by driver.py before any Apple TV connection is created, so install the MRP patch here rather
# than requiring another driver-level monkey-patch assignment. Existing AirPlay pairing patches are still installed by
# driver.main() below their corresponding pyatv imports.
apply_mrp_foreground_app_patch()
