"""Synchronize selector commands with shared Apple TV entity state."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import Any, cast

from ucapi import StatusCodes
from ucapi.media_player import Attributes as MediaAttr

import tv
from utils import replace_bad_chars

_LOG = logging.getLogger(__name__)

_SOURCE_ATTRIBUTE = MediaAttr.SOURCE.value
_SOUND_MODE_ATTRIBUTE = MediaAttr.SOUND_MODE.value
_STATES_ATTR = "_selection_sync_states"
_ORIGINAL_EMIT_ATTR = "_selection_sync_original_emit"
_ORIGINAL_APP_NAME_ATTR = "_selection_sync_original_app_name"
_APP_NAME_PATCHED_ATTR = "_selection_sync_app_name_patched"


@dataclass
class _SelectionState:
    """Authoritative state for a selection while the backing protocol catches up."""

    target: str
    stale_values: set[str] = field(default_factory=set)


def _attribute_name(key: Any) -> str:
    """Return a stable string representation for an enum/string attribute key."""
    return str(getattr(key, "value", key))


def _normalise(attribute: str, value: Any) -> str:
    """Normalize values exactly like the integration does for source updates."""
    if value is None:
        return ""
    result = str(value)
    if attribute == _SOURCE_ATTRIBUTE and result:
        return replace_bad_chars(result)
    return result


def _states(device: tv.AppleTv) -> dict[str, _SelectionState]:
    """Return the per-device authoritative selection states."""
    obj = cast("Any", device)
    states = getattr(obj, _STATES_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(obj, _STATES_ATTR, states)
    return cast("dict[str, _SelectionState]", states)


def _existing_states(device: tv.AppleTv) -> dict[str, _SelectionState] | None:
    """Return existing states without allocating a container on ordinary reads."""
    states = getattr(cast("Any", device), _STATES_ATTR, None)
    return cast("dict[str, _SelectionState]", states) if isinstance(states, dict) else None


def _raw_app_name(device: tv.AppleTv) -> str:
    """Read pyatv's unmodified app/Now Playing value."""
    original = getattr(cast("Any", tv.AppleTv), _ORIGINAL_APP_NAME_ATTR, None)
    if isinstance(original, property) and original.fget is not None:
        try:
            return str(original.fget(device) or "")
        except Exception:  # noqa: BLE001 - test doubles and resilient runtime fallback
            _LOG.debug(
                "[%s] Failed to read raw app name",
                getattr(device, "log_id", getattr(device, "identifier", "Apple TV")),
                exc_info=True,
            )
    return str(getattr(cast("Any", device), "app_name", "") or "")


def _current_backing_value(device: tv.AppleTv, attribute: str) -> str:
    """Read the backing value before issuing a selection command."""
    if attribute == _SOURCE_ATTRIBUTE:
        return _normalise(attribute, _raw_app_name(device))
    if attribute == _SOUND_MODE_ATTRIBUTE:
        return _normalise(attribute, getattr(cast("Any", device), "output_devices", ""))
    return ""


def _clear_state(device: tv.AppleTv, attribute: str, reason: str) -> None:
    """Release an authoritative selection once the backing state has caught up or changed."""
    states = _existing_states(device)
    if not states or attribute not in states:
        return
    state = states.pop(attribute)
    _LOG.debug(
        "[%s] Selection sync released %s=%s: %s",
        getattr(device, "log_id", getattr(device, "identifier", "Apple TV")),
        attribute,
        state.target,
        reason,
    )


def _filter_update(device: tv.AppleTv, update: dict[Any, Any]) -> dict[Any, Any]:
    """Suppress stale backing values while a commanded selection is authoritative."""
    states = _existing_states(device)
    if not states:
        return update

    filtered = update
    for key, raw_value in list(update.items()):
        attribute = _attribute_name(key)
        state = states.get(attribute)
        if state is None:
            continue

        value = _normalise(attribute, raw_value)
        if value == state.target:
            # For source, app_name is monkey-patched to expose the authoritative
            # target. Verify against the original pyatv property before treating
            # this as confirmation; otherwise the 10s poll would confirm its own
            # synthetic target and the following poll could revert to stale media.
            if attribute != _SOURCE_ATTRIBUTE or _normalise(attribute, _raw_app_name(device)) == state.target:
                _clear_state(device, attribute, "backing state confirmed target")
            continue

        if not value or value in state.stale_values:
            if filtered is update:
                filtered = dict(update)
            filtered.pop(key, None)
            _LOG.debug(
                "[%s] Suppressing stale %s=%s while selected value %s is authoritative",
                getattr(device, "log_id", getattr(device, "identifier", "Apple TV")),
                attribute,
                value or "<empty>",
                state.target,
            )
            continue

        # A non-empty value that is neither the requested target nor one of the
        # known stale values is meaningful new state (for example an external app
        # switch followed by playback). Let it through and stop overriding.
        _clear_state(device, attribute, f"backing state changed to {value}")

    return filtered


def _install_update_filter(device: tv.AppleTv) -> Callable[..., Any]:
    """Intercept device UPDATE events so stale values cannot overwrite a selection."""
    obj = cast("Any", device)
    original_emit = getattr(obj, _ORIGINAL_EMIT_ATTR, None)
    if callable(original_emit):
        return cast("Callable[..., Any]", original_emit)

    emitter = cast("Any", device.events)
    original_emit = emitter.emit
    setattr(obj, _ORIGINAL_EMIT_ATTR, original_emit)

    def filtered_emit(event: Any, *args: Any, **kwargs: Any) -> Any:
        if event == tv.EVENTS.UPDATE and len(args) >= 2 and isinstance(args[1], dict):
            update = _filter_update(device, args[1])
            if not update:
                return False
            args = (args[0], update, *args[2:])
        return original_emit(event, *args, **kwargs)

    emitter.emit = filtered_emit
    return cast("Callable[..., Any]", original_emit)


def _synced_app_name(device: tv.AppleTv) -> str:
    """Prefer a commanded app over pyatv's stale Now Playing app."""
    raw = _raw_app_name(device)
    states = _existing_states(device)
    if not states:
        return raw

    state = states.get(_SOURCE_ATTRIBUTE)
    if state is None:
        return raw

    value = _normalise(_SOURCE_ATTRIBUTE, raw)
    if value == state.target:
        _clear_state(device, _SOURCE_ATTRIBUTE, "pyatv app confirmed target")
        return raw
    if value and value not in state.stale_values:
        _clear_state(device, _SOURCE_ATTRIBUTE, f"pyatv app changed to {value}")
        return raw
    return state.target


def _install_app_name_patch() -> None:
    """Make App Select/current source reads honor the authoritative selection."""
    cls = cast("Any", tv.AppleTv)
    if getattr(cls, _APP_NAME_PATCHED_ATTR, False):
        return
    original = tv.AppleTv.app_name
    setattr(cls, _ORIGINAL_APP_NAME_ATTR, original)
    cls.app_name = property(_synced_app_name)
    setattr(cls, _APP_NAME_PATCHED_ATTR, True)


_install_app_name_patch()


async def run_synced_selection(
    device: tv.AppleTv,
    handler: Callable[[str], Awaitable[StatusCodes]],
    attribute: str,
    option: str,
) -> StatusCodes:
    """Run a selection handler and keep every entity synchronized on success.

    A successful app launch is authoritative until pyatv reports the requested
    app itself or a genuinely different value. The old Now Playing app and empty
    playback-reset values are suppressed in the meantime, so opening a new app
    does not immediately snap the Select/sensor/media-player source back to the
    application that was previously playing media.
    """
    previous_value = _current_backing_value(device, attribute)
    result = await handler(option)
    if result != StatusCodes.OK:
        return result

    target = _normalise(attribute, option)
    states = _states(device)
    existing = states.get(attribute)
    stale_values = set(existing.stale_values) if existing else set()
    if existing and existing.target and existing.target != target:
        stale_values.add(existing.target)
    if previous_value and previous_value != target:
        stale_values.add(previous_value)
    stale_values.discard(target)
    states[attribute] = _SelectionState(target=target, stale_values=stale_values)

    # Install the interceptor before publishing the new state, but deliberately
    # call the original emitter for this optimistic update. It must not be
    # mistaken for confirmation from pyatv.
    original_emit = _install_update_filter(device)
    original_emit(tv.EVENTS.UPDATE, device.identifier, {attribute: target})
    return result
