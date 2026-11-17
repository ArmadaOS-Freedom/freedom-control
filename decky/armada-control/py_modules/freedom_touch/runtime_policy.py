"""Pure runtime policy for the Freedom touch controller.

This module contains no hardware access and depends only on the standard
library.  It decides when a game session should be captured, which
InputPlumber composite belongs to us, validates persisted settings, expands a
layout for display, and turns raw multitouch evdev events into frame-level
contact events.
"""

import math

from py_modules.freedom_touch.layout import (
    default_layout,
    tablet_compact_layout,
    tablet_trackpads_layout,
    validate_layout,
)
from py_modules.freedom_touch.presets import (
    BUILTIN_PRESET_IDS,
    BUILTIN_PRESET_NAMES,
    CUSTOM_NAME_MAX_LENGTH,
    DEFAULT_DPAD_MODE,
    DEFAULT_PRESET_ID,
    DPAD_MODES,
    LEGACY_PRESET_ID,
    LEGACY_PRESET_NAME,
    RETIRED_PRESET_IDS,
    preset_catalog,
)

# Retired built-in ids that a saved active selection may still name, mapped to
# the layout factory that produced their ergonomics.  These factories are
# helper exports only; they are never catalog entries again.
_RETIRED_LAYOUT_FACTORIES = {
    "tablet_compact": tablet_compact_layout,
    "tablet_trackpads": tablet_trackpads_layout,
}

COMPOSITE_IFACE = "org.shadowblip.Input.CompositeDevice"
COMPOSITE_NAME = "Freedom Touch Controller"

# Raw evdev constants.  Numeric values keep this module independent of
# python-evdev, which is not available in every runtime.
EV_SYN = 0
EV_ABS = 3
SYN_REPORT = 0
SYN_DROPPED = 3
MT_SLOT = 47
MT_TRACKING_ID = 57
MT_POSITION_X = 53
MT_POSITION_Y = 54

# Steam's Quick Access Menu reports this input app id even while a real game
# owns the graphics session, so it can never identify a running game.
QAM_APP_ID = 769

SETTINGS_VERSION = 2
LEGACY_SETTINGS_VERSION = 1
DEFAULT_OPACITY = 0.45
DEFAULT_SCALE = 1.0
DEFAULT_DEADZONE = 0.1
DEFAULT_HAPTICS_ENABLED = True
# 25 percent maps to the historical 0x4000 magnitude.
DEFAULT_HAPTICS_STRENGTH = 25
DEFAULT_RIGHT_STICK_FULLSCREEN = False
# The control body palette.  Documents written before this field existed keep
# the historical white chrome; the setting is global, not per preset.
DEFAULT_BUTTON_COLOR = "white"
BUTTON_COLORS = ("white", "black")

MIN_OPACITY = 0.15
MAX_OPACITY = 1.0
MIN_SCALE = 0.6
MAX_SCALE = 1.4
MIN_DEADZONE = 0.0
MAX_DEADZONE = 0.5
MIN_HAPTICS_STRENGTH = 0
MAX_HAPTICS_STRENGTH = 100
MAX_HEARTBEAT_AGE = 3.0


def _app_id(value):
    """Return *value* as a positive non-QAM app id, or None if invalid."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text, 10)
        except ValueError:
            return None
    else:
        return None
    if number <= 0 or number == QAM_APP_ID:
        return None
    return number


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be numeric" % field)
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError("%s must be finite" % field)
    if not math.isfinite(number):
        raise ValueError("%s must be finite" % field)
    return number


def _percent(value, field):
    """Return an integer 0..100 percent, rejecting bool and non-numeric."""

    number = _finite_number(value, field)
    if number != int(number):
        raise ValueError("%s must be an integer percent" % field)
    integer = int(number)
    if not MIN_HAPTICS_STRENGTH <= integer <= MAX_HAPTICS_STRENGTH:
        raise ValueError("%s is out of range" % field)
    return integer


def should_capture(enabled, running_appid, input_appid, gfx_appid,
                   heartbeat_age, ready):
    """Return True only for a live, matching, non-QAM game session."""

    if not enabled or not ready:
        return False
    if isinstance(heartbeat_age, bool) or not isinstance(heartbeat_age, (int, float)):
        return False
    age = float(heartbeat_age)
    if not math.isfinite(age) or age < 0.0 or age > MAX_HEARTBEAT_AGE:
        return False

    running = _app_id(running_appid)
    source = _app_id(input_appid)
    graphics = _app_id(gfx_appid)
    if running is None or source is None or graphics is None:
        return False
    return running == source == graphics


def choose_composite(objects, source_path):
    """Return the one composite object fed by *source_path*, else None."""

    if not isinstance(objects, dict):
        return None

    matches = []
    for object_path, interfaces in objects.items():
        if not isinstance(interfaces, dict):
            continue
        composite = interfaces.get(COMPOSITE_IFACE)
        if not isinstance(composite, dict):
            continue
        if composite.get("Name") != COMPOSITE_NAME:
            continue
        sources = composite.get("SourceDevicePaths")
        if not isinstance(sources, (list, tuple)):
            continue
        if any(source == source_path for source in sources):
            matches.append(object_path)

    if len(matches) == 1:
        return matches[0]
    return None


def is_legacy_settings(raw):
    """Return True for a persisted v1 settings object needing migration."""

    return (
        isinstance(raw, dict)
        and not isinstance(raw.get("version"), bool)
        and raw.get("version") == LEGACY_SETTINGS_VERSION
    )


def _options(raw):
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")

    opacity = _finite_number(raw.get("opacity", DEFAULT_OPACITY), "opacity")
    if not MIN_OPACITY <= opacity <= MAX_OPACITY:
        raise ValueError("opacity is out of range")

    deadzone = _finite_number(raw.get("deadzone", DEFAULT_DEADZONE), "deadzone")
    if not MIN_DEADZONE <= deadzone <= MAX_DEADZONE:
        raise ValueError("deadzone is out of range")

    edge_swipes = raw.get("edge_swipes", False)
    if not isinstance(edge_swipes, bool):
        raise ValueError("edge_swipes must be a boolean")

    button_color = raw.get("button_color", DEFAULT_BUTTON_COLOR)
    if not isinstance(button_color, str) or button_color not in BUTTON_COLORS:
        raise ValueError(
            "button_color must be one of: %s" % (", ".join(BUTTON_COLORS),)
        )

    edge_swipes = raw.get("edge_swipes", False)
    if not isinstance(edge_swipes, bool):
        raise ValueError("edge_swipes must be a boolean")

    haptics_enabled = raw.get("haptics_enabled", DEFAULT_HAPTICS_ENABLED)
    if not isinstance(haptics_enabled, bool):
        raise ValueError("haptics_enabled must be a boolean")

    haptics_strength = _percent(
        raw.get("haptics_strength", DEFAULT_HAPTICS_STRENGTH),
        "haptics_strength",
    )

    dpad_mode = raw.get("dpad_mode", DEFAULT_DPAD_MODE)
    if not isinstance(dpad_mode, str) or dpad_mode not in DPAD_MODES:
        raise ValueError(
            "dpad_mode must be one of: %s" % (", ".join(DPAD_MODES),)
        )

    right_stick_fullscreen = raw.get(
        "right_stick_fullscreen", DEFAULT_RIGHT_STICK_FULLSCREEN
    )
    if not isinstance(right_stick_fullscreen, bool):
        raise ValueError("right_stick_fullscreen must be a boolean")

    return {
        "enabled": enabled,
        "opacity": opacity,
        "deadzone": deadzone,
        "button_color": button_color,
        "edge_swipes": edge_swipes,
        "haptics_enabled": haptics_enabled,
        "haptics_strength": haptics_strength,
        "dpad_mode": dpad_mode,
        "right_stick_fullscreen": right_stick_fullscreen,
    }


def _preset_name(value):
    if not isinstance(value, str):
        raise ValueError("preset name must be a string")
    name = value.strip()
    if not 1 <= len(name) <= CUSTOM_NAME_MAX_LENGTH:
        raise ValueError(
            "preset name must be 1 to %d characters"
            % CUSTOM_NAME_MAX_LENGTH
        )
    return name


def _custom_presets(raw, default_mode=DEFAULT_DPAD_MODE):
    """Return canonical custom presets.

    ``dpad_mode`` and ``base_preset_id`` were added later.  Entries written
    before that keep working: a missing mode inherits *default_mode* (the
    document's own top-level mode) and a missing base becomes the tablet
    default.  An explicit but unsupported value is always rejected, including
    the retired preset ids, which were never valid bases.
    """

    if not isinstance(raw, (list, tuple)):
        raise ValueError("custom_presets must be a list")

    # Loading existing data and validating a new save are two different rules:
    # a custom preset's id may never collide with a built-in id, and two
    # custom presets may not share an id or a name, but a custom name *may*
    # be spelled like a built-in.  Users who saved "PS" or "Switch" before
    # those became built-ins would otherwise lose access to their file; the
    # save coordinator (not this loader) rejects NEW such names.
    seen_ids = set(BUILTIN_PRESET_IDS)
    seen_names = set()
    canonical = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each custom preset must be a dict")

        preset_id = item.get("id")
        if not isinstance(preset_id, str) or not preset_id.strip():
            raise ValueError("custom preset id must be a non-empty string")
        preset_id = preset_id.strip()
        if preset_id in BUILTIN_PRESET_IDS:
            raise ValueError("custom preset id is built in: %r" % (preset_id,))
        if preset_id in seen_ids:
            raise ValueError("duplicate preset id: %r" % (preset_id,))
        seen_ids.add(preset_id)

        name = _preset_name(item.get("name"))
        if name in seen_names:
            raise ValueError("duplicate preset name: %r" % (name,))
        seen_names.add(name)

        scale = _finite_number(item.get("scale", DEFAULT_SCALE), "scale")
        if not MIN_SCALE <= scale <= MAX_SCALE:
            raise ValueError("preset scale is out of range")

        dpad_mode = item.get("dpad_mode", default_mode)
        if not isinstance(dpad_mode, str) or dpad_mode not in DPAD_MODES:
            raise ValueError(
                "preset dpad_mode must be one of: %s"
                % (", ".join(DPAD_MODES),)
            )

        base_preset_id = item.get("base_preset_id", DEFAULT_PRESET_ID)
        if (
            not isinstance(base_preset_id, str)
            or base_preset_id not in BUILTIN_PRESET_IDS
        ):
            raise ValueError(
                "preset base_preset_id must be a built-in preset id"
            )

        if "layout" not in item:
            raise ValueError("custom preset is missing layout")
        layout = validate_layout(item["layout"])

        canonical.append(
            {
                "id": preset_id,
                "name": name,
                "layout": layout,
                "scale": scale,
                "dpad_mode": dpad_mode,
                "base_preset_id": base_preset_id,
            }
        )
    return canonical


def _migrate_legacy_settings(raw):
    """Upgrade v1 settings, keeping their raw layout as a custom preset."""

    options = _options(raw)
    old_scale = _finite_number(raw.get("scale", DEFAULT_SCALE), "scale")
    if not MIN_SCALE <= old_scale <= MAX_SCALE:
        raise ValueError("scale is out of range")

    custom_presets = []
    if "layout" in raw:
        legacy_layout = validate_layout(raw["layout"])
        custom_presets.append(
            {
                "id": LEGACY_PRESET_ID,
                "name": LEGACY_PRESET_NAME,
                "layout": legacy_layout,
                "scale": old_scale,
                "dpad_mode": options["dpad_mode"],
                "base_preset_id": DEFAULT_PRESET_ID,
            }
        )

    standard = validate_layout(default_layout())
    return {
        "enabled": options["enabled"],
        "opacity": options["opacity"],
        "scale": DEFAULT_SCALE,
        "deadzone": options["deadzone"],
        "button_color": options["button_color"],
        "edge_swipes": options["edge_swipes"],
        "haptics_enabled": options["haptics_enabled"],
        "haptics_strength": options["haptics_strength"],
        "dpad_mode": options["dpad_mode"],
        "right_stick_fullscreen": options["right_stick_fullscreen"],
        "layout": standard,
        "active_preset_id": DEFAULT_PRESET_ID,
        "custom_presets": custom_presets,
        "version": SETTINGS_VERSION,
    }


def validate_settings(raw):
    """Return canonical settings, raising ValueError for invalid input."""

    if not isinstance(raw, dict):
        raise ValueError("settings must be a dict")

    version = raw.get("version", SETTINGS_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("version must be an integer")
    if version == LEGACY_SETTINGS_VERSION:
        return _migrate_legacy_settings(raw)
    if version != SETTINGS_VERSION:
        raise ValueError("unsupported settings version: %r" % (version,))

    options = _options(raw)
    explicit_mode = "dpad_mode" in raw
    custom_presets = _custom_presets(
        raw.get("custom_presets", []), options["dpad_mode"]
    )

    active_id = raw.get("active_preset_id", DEFAULT_PRESET_ID)
    if not isinstance(active_id, str) or not active_id:
        raise ValueError("active_preset_id must be a non-empty string")

    # A document written before the catalog changed may still name one of the
    # retired built-ins.  Its saved layout belonged to that ergonomics, so keep
    # it as a preserved legacy custom preset and fall back to the default
    # active preset instead of resetting the user's geometry.
    retired = active_id in RETIRED_PRESET_IDS
    if retired:
        retired_id = active_id
        active_id = DEFAULT_PRESET_ID

    active = None
    for preset in preset_catalog(custom_presets):
        if preset["id"] == active_id:
            active = preset
            break
    if active is None:
        raise ValueError("unknown active preset: %r" % (active_id,))

    if retired:
        if "layout" in raw:
            saved_layout = validate_layout(raw["layout"])
        else:
            # No saved layout: fall back to the retired preset's own factory
            # rather than silently adopting the standard layout.
            saved_layout = validate_layout(
                _RETIRED_LAYOUT_FACTORIES[retired_id]()
            )
        scale = _finite_number(raw.get("scale", active["scale"]), "scale")
        if not MIN_SCALE <= scale <= MAX_SCALE:
            raise ValueError("scale is out of range")

        legacy_id = LEGACY_PRESET_ID
        used_ids = {preset["id"] for preset in custom_presets}
        if legacy_id in used_ids:
            suffix = 1
            while "%s_%s" % (legacy_id, suffix) in used_ids:
                suffix += 1
            legacy_id = "%s_%s" % (legacy_id, suffix)
        used_names = {preset["name"] for preset in custom_presets}
        legacy_name = LEGACY_PRESET_NAME
        suffix = 1
        while legacy_name in used_names:
            legacy_name = "%s %d" % (LEGACY_PRESET_NAME, suffix)
            suffix += 1

        custom_presets = list(custom_presets)
        custom_presets.append(
            {
                "id": legacy_id,
                "name": legacy_name,
                "layout": saved_layout,
                "scale": scale,
                "dpad_mode": options["dpad_mode"],
                # The retired id is not a valid base; anchor the preserved
                # preset at the canonical tablet default so the canonical
                # state validates again on re-read.
                "base_preset_id": DEFAULT_PRESET_ID,
            }
        )
        layout = saved_layout
    else:
        scale = _finite_number(raw.get("scale", active["scale"]), "scale")
        if not MIN_SCALE <= scale <= MAX_SCALE:
            raise ValueError("scale is out of range")

        # An explicit layout is authoritative even when empty: the editor can
        # delete every component and the empty list must survive a reload.
        if "layout" in raw:
            layout = validate_layout(raw["layout"])
        else:
            layout = validate_layout(active["layout"])

    # The top-level mode is the runtime effective value.  An explicit one wins;
    # otherwise the active preset's own mode is the sensible default.
    dpad_mode = (
        options["dpad_mode"]
        if explicit_mode
        else active.get("dpad_mode", DEFAULT_DPAD_MODE)
    )

    return {
        "enabled": options["enabled"],
        "opacity": options["opacity"],
        "scale": scale,
        "deadzone": options["deadzone"],
        "button_color": options["button_color"],
        "edge_swipes": options["edge_swipes"],
        "haptics_enabled": options["haptics_enabled"],
        "haptics_strength": options["haptics_strength"],
        "dpad_mode": dpad_mode,
        "right_stick_fullscreen": options["right_stick_fullscreen"],
        "layout": layout,
        "active_preset_id": active_id,
        "custom_presets": custom_presets,
        "version": SETTINGS_VERSION,
    }


def effective_layout(layout, scale):
    """Return a scaled copy of *layout*, clamped to unit screen bounds.

    Scaling preserves each control's own center and never mutates the input.
    Oversized rectangles are shrunk proportionally so their aspect ratio is
    preserved; edge clamping shifts them back inside the screen without
    resizing.
    """

    result = []
    for rect in layout:
        item = dict(rect)

        center_x = float(rect["x"]) + float(rect["w"]) / 2.0
        center_y = float(rect["y"]) + float(rect["h"]) / 2.0
        width = float(rect["w"]) * scale
        height = float(rect["h"]) * scale
        x = center_x - width / 2.0
        y = center_y - height / 2.0

        longest = max(width, height)
        if longest > 1.0:
            factor = 1.0 / longest
            width *= factor
            height *= factor
            x = center_x - width / 2.0
            y = center_y - height / 2.0

        if x < 0.0:
            x = 0.0
        elif x + width > 1.0:
            x = 1.0 - width

        if y < 0.0:
            y = 0.0
        elif y + height > 1.0:
            y = 1.0 - height

        item["x"] = x
        item["y"] = y
        item["w"] = width
        item["h"] = height
        result.append(item)

    return result


def _as_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class FrameAccumulator:
    """Collapse raw multitouch events into begin/move/end contact events.

    Type-B devices report only the axes that changed, and the kernel expects
    the receiver to keep each slot's attributes for as long as the device
    state lives.  Cached positions therefore live in ``_positions``,
    independent of the active contacts in ``_contacts``: releasing a finger
    only drops its contact, so a later press on the same slot starts from
    the last reported coordinates even without fresh axis events.

    Events are emitted only on SYN_REPORT.  SYN_DROPPED immediately emits a
    single cancel and discards all state, cached positions included; every
    event until the following SYN_REPORT is ignored so a partially dropped
    frame cannot create ghost contacts.

    A frame that replaces this one (a new capture, or a device that was just
    opened) can be started from the cached slot state with
    ``clone_positions``/``seed_positions``: it then knows where each slot
    already is, but owns no contact and still needs a fresh TRACKING_ID.
    """

    def __init__(self):
        self._positions = {}
        self._contacts = {}
        self._slot = 0
        self._ends = []
        self._dropped = False

    def clone_positions(self):
        """Return a fresh frame holding only this one's cached slot state.

        The clone keeps every slot's last reported coordinates and the current
        slot number, so a press that reports a single axis, or none, still
        begins where the finger already is.  Active contacts and pending ends
        are deliberately not copied: the clone can only start a gesture from a
        new TRACKING_ID and can never take over a finger the game owns.

        Cached coordinates read inside a SYN_DROPPED window are not
        trustworthy, so such a clone starts empty *and* stays inside the
        dropped window: it ignores every event up to the next SYN_REPORT, so
        the tail of the dropped frame cannot begin a contact.  Only the frame
        after that SYN_REPORT starts a gesture.
        """

        clone = FrameAccumulator()
        if self._dropped:
            clone._dropped = True
            return clone
        clone._positions = {
            slot: dict(position) for slot, position in self._positions.items()
        }
        clone._slot = self._slot
        return clone

    def seed_positions(self, positions, slot=0):
        """Load a read-only slot snapshot into a fresh frame.

        *positions* maps slot numbers to mappings with ``x``/``y`` entries and
        *slot* is the device's current slot.  Only non-negative integer slots
        and coordinates survive.  Nothing here creates a contact, and a missing
        axis stays unknown so a begin still waits for the whole point instead
        of inventing one.
        """

        seeded = {}
        if isinstance(positions, dict):
            for key, value in positions.items():
                number = _as_int(key)
                if number is None or number < 0 or not isinstance(value, dict):
                    continue
                point = {
                    "x": _as_int(value.get("x")),
                    "y": _as_int(value.get("y")),
                }
                if point["x"] is None and point["y"] is None:
                    continue
                seeded[number] = point
        self._positions = seeded
        current = _as_int(slot)
        self._slot = current if current is not None and current >= 0 else 0

    def consume(self, event_type, code, value):
        """Feed one evdev event and return the events now ready to emit."""

        if self._dropped:
            if event_type == EV_SYN and code == SYN_REPORT:
                self._dropped = False
            return []

        if event_type == EV_SYN:
            if code == SYN_DROPPED:
                return self._cancel()
            if code == SYN_REPORT:
                return self._flush()
            return []

        if event_type != EV_ABS:
            return []

        if code == MT_SLOT:
            slot = _as_int(value)
            if slot is not None and slot >= 0:
                self._slot = slot
        elif code == MT_TRACKING_ID:
            tracking_id = _as_int(value)
            if tracking_id is None:
                return []
            if tracking_id < 0:
                self._release(self._slot)
            else:
                self._start(self._slot, tracking_id)
        elif code == MT_POSITION_X:
            self._position(self._slot, "x", value)
        elif code == MT_POSITION_Y:
            self._position(self._slot, "y", value)
        return []

    def _start(self, slot, tracking_id):
        contact = self._contacts.get(slot)
        if contact is not None:
            if contact["tid"] == tracking_id:
                return
            if contact["begun"]:
                self._ends.append(("end", contact["tid"], None, None))
        # The new contact inherits the slot's cached position so a press
        # without fresh axis events still begins where the slot was left.
        self._contacts[slot] = {
            "tid": tracking_id,
            "begun": False,
            "changed": False,
        }

    def _release(self, slot):
        contact = self._contacts.pop(slot, None)
        if contact is None:
            return
        if contact["begun"]:
            self._ends.append(("end", contact["tid"], None, None))

    def _position(self, slot, axis, value):
        number = _as_int(value)
        if number is None:
            return
        position = self._positions.get(slot)
        if position is None:
            position = {"x": None, "y": None}
            self._positions[slot] = position
        if position[axis] == number:
            return
        position[axis] = number
        contact = self._contacts.get(slot)
        if contact is not None:
            contact["changed"] = True

    def _flush(self):
        events = self._ends
        self._ends = []

        for slot, contact in self._contacts.items():
            position = self._positions.get(slot)
            if position is None:
                continue
            x = position["x"]
            y = position["y"]
            if x is None or y is None:
                continue
            if not contact["begun"]:
                events.append(("begin", contact["tid"], x, y))
                contact["begun"] = True
                contact["changed"] = False
            elif contact["changed"]:
                events.append(("move", contact["tid"], x, y))
                contact["changed"] = False

        return events

    def _cancel(self):
        self._positions.clear()
        self._contacts.clear()
        self._ends = []
        self._slot = 0
        self._dropped = True
        return [("cancel", None, None, None)]
