"""Thin wrappers for the privileged Game Mode display orientation helper.

The system helper owns persistence and the panel state; this module only
validates the closed enum and forwards the request, so the plugin never
invents a supported or saved state it did not read back.
"""

from .privileged import call

# Panel axes: every value is relative to the panel's own orientation, so
# "normal" is never translated into landscape or portrait here.
ORIENTATIONS = ("normal", "left", "right", "upsidedown")
UNSET = ""

# The empty string is the accepted "no override" request value.
ALLOWED = ORIENTATIONS + (UNSET,)


def _state(result):
    """Return the helper's state, refusing anything that is not a full answer."""
    if not isinstance(result, dict):
        raise ValueError("helper returned a non-object state: %r" % (result,))
    supported = result.get("supported")
    if not isinstance(supported, bool):
        raise ValueError("helper returned a non-boolean supported flag: %r" % (supported,))
    orientation = result.get("orientation")
    if not isinstance(orientation, str) or orientation not in ALLOWED:
        raise ValueError("helper returned an unsupported orientation: %r" % (orientation,))
    default = result.get("defaultOrientation")
    if not isinstance(default, str) or default not in ORIENTATIONS:
        raise ValueError("helper returned an invalid default orientation: %r" % (default,))
    return {
        "supported": supported,
        "orientation": orientation,
        "defaultOrientation": default,
    }


def get_display_orientation():
    """Read the helper's state; helper failures and malformed answers propagate."""
    return _state(call("get_display_orientation"))


def set_display_orientation(orientation):
    """Apply one enum value (or the empty string); helper failures propagate."""
    if not isinstance(orientation, str):
        raise ValueError("orientation must be a string")
    if orientation not in ALLOWED:
        raise ValueError("unsupported orientation: %r" % (orientation,))
    return _state(call("set_display_orientation", orientation=orientation))
