"""Pure geometry and gesture tracking for the collapsible overlay chrome.

The module has no GTK, X11, or cairo imports so the overlay and the tests can
share exactly the same bubble rectangle, clamping, and drag/tap decisions.
Normalized positions use the same convention as the rest of the plugin: the
origin is the top-left corner, x grows right, and y grows down.
"""

import math

MODES = ("expanded", "collapsed")

# The bubble is a physical circle whose diameter is 8% of the screen height.
BUBBLE_DIAMETER = 0.08
DEFAULT_BUBBLE_X = 0.92
DEFAULT_BUBBLE_Y = 0.45

# The expanded-mode collapse pill sits below the top Start/Back buttons.
PILL_X = 0.46
PILL_Y = 0.15
PILL_WIDTH = 0.08
PILL_HEIGHT = 0.06
PILL_LABEL = "收起"

# A drag must travel this many pixels at 1920x1080, scaled by the screen
# height, before it stops counting as a tap.
DRAG_THRESHOLD = 8.0
REFERENCE_HEIGHT = 1080.0


def default_bubble():
    """Return a fresh default bubble top-left position."""

    return {"x": DEFAULT_BUBBLE_X, "y": DEFAULT_BUBBLE_Y}


def _finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be numeric" % field)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("%s must be finite" % field)
    return number


def viewport(width, height):
    """Return validated viewport dimensions as floats."""

    width = _finite(width, "width")
    height = _finite(height, "height")
    if width <= 0.0 or height <= 0.0:
        raise ValueError("viewport must be positive")
    return width, height


def aspect(width, height):
    """Return the viewport width/height ratio."""

    width, height = viewport(width, height)
    return width / height


def bubble_size(width, height):
    """Return the bubble size in normalized units for this viewport.

    The diameter is 8% of the screen height, so the normalized width has to
    be divided by the viewport aspect ratio to stay a physical circle.
    """

    width, height = viewport(width, height)
    height_n = BUBBLE_DIAMETER
    width_n = BUBBLE_DIAMETER * height / width
    if width_n > 1.0:
        # A viewport narrower than the circle's diameter cannot show the
        # requested circle; shrink both axes to fit without distorting it.
        width_n = 1.0
        height_n = width / height
    return width_n, height_n


def _bubble_point(bubble):
    if not isinstance(bubble, dict):
        raise ValueError("bubble must be an object")
    for key in ("x", "y"):
        if key not in bubble:
            raise ValueError("bubble.%s is missing" % key)
    return _finite(bubble["x"], "bubble.x"), _finite(bubble["y"], "bubble.y")


def bubble_normalized(bubble, width, height):
    """Return ``(x, y, w, h)`` in normalized units, clamped to the viewport.

    ``bubble`` carries the normalized top-left corner supplied by the daemon;
    the size always comes from the viewport aspect so the circle never
    stretches.
    """

    width, height = viewport(width, height)
    x, y = _bubble_point(bubble)
    bubble_width, bubble_height = bubble_size(width, height)
    limit_x = max(0.0, 1.0 - bubble_width)
    limit_y = max(0.0, 1.0 - bubble_height)
    x = min(max(x, 0.0), limit_x)
    y = min(max(y, 0.0), limit_y)
    return x, y, bubble_width, bubble_height


def bubble_rect(bubble, width, height):
    """Return the bubble rectangle in device pixels."""

    width, height = viewport(width, height)
    x, y, bubble_width, bubble_height = bubble_normalized(
        bubble, width, height
    )
    return (
        x * width,
        y * height,
        bubble_width * width,
        bubble_height * height,
    )


def pill_normalized():
    """Return the collapse pill rectangle in normalized units."""

    return PILL_X, PILL_Y, PILL_WIDTH, PILL_HEIGHT


def pill_rect(width, height):
    """Return the collapse pill rectangle in device pixels."""

    width, height = viewport(width, height)
    x, y, pill_width, pill_height = pill_normalized()
    return (
        x * width,
        y * height,
        pill_width * width,
        pill_height * height,
    )


def contains(rect, x, y):
    """Return True when the point is inside the half-open rectangle."""

    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise ValueError("rect must have four values")
    left, top, width, height = (float(value) for value in rect)
    return (
        left <= x < left + width
        and top <= y < top + height
    )


def drag_threshold(height):
    """Return the tap/drag threshold in pixels for a viewport height."""

    height = _finite(height, "height")
    if height <= 0.0:
        raise ValueError("height must be positive")
    return DRAG_THRESHOLD * height / REFERENCE_HEIGHT


def bubble_action(bubble, width, height):
    """Return the stdout action describing the bubble's resting position."""

    x, y, _width, _height = bubble_normalized(bubble, width, height)
    return {"action": "bubble", "x": round(x, 6), "y": round(y, 6)}


class BubbleDrag:
    """Track exactly one touch sequence that started on the bubble.

    ``begin`` performs the only hit test.  After that the sequence keeps
    receiving updates even when it leaves the original rectangle, which is
    what makes an edge-to-edge drag possible.  The finger's offset inside the
    bubble is preserved, so a grip near the rim does not jump the bubble under
    the centre of the touch.
    """

    def __init__(self, bubble, width, height):
        self.width, self.height = viewport(width, height)
        x, y, bubble_width, bubble_height = bubble_normalized(
            bubble, self.width, self.height
        )
        self._bubble = {"x": x, "y": y}
        self.start = {"x": x, "y": y}
        self._pixel_width = bubble_width * self.width
        self._pixel_height = bubble_height * self.height
        self._origin = None
        self._offset = None
        self._threshold = drag_threshold(self.height)
        self.active = False
        self.dragging = False

    @property
    def bubble(self):
        return dict(self._bubble)

    def begin(self, x, y):
        """Claim a touch sequence if it starts inside the bubble."""

        if self.active:
            return False
        try:
            x = _finite(x, "x")
            y = _finite(y, "y")
        except ValueError:
            return False

        left = self._bubble["x"] * self.width
        top = self._bubble["y"] * self.height
        if not contains(
            (left, top, self._pixel_width, self._pixel_height), x, y
        ):
            return False

        self.active = True
        self.dragging = False
        self._origin = (x, y)
        self._offset = (x - left, y - top)
        return True

    def update(self, x, y):
        """Move the bubble once the scaled drag threshold is crossed."""

        if not self.active:
            return False
        try:
            x = _finite(x, "x")
            y = _finite(y, "y")
        except ValueError:
            return False

        if not self.dragging:
            distance = math.hypot(x - self._origin[0], y - self._origin[1])
            if distance < self._threshold:
                return False
            self.dragging = True

        left = x - self._offset[0]
        top = y - self._offset[1]
        limit_x = max(0.0, self.width - self._pixel_width)
        limit_y = max(0.0, self.height - self._pixel_height)
        left = min(max(left, 0.0), limit_x)
        top = min(max(top, 0.0), limit_y)
        self._bubble = {
            "x": left / self.width,
            "y": top / self.height,
        }
        return True

    def end(self):
        """Finish the sequence, returning ``"tap"``, ``"drag"``, or None."""

        if not self.active:
            return None
        outcome = "drag" if self.dragging else "tap"
        self.active = False
        self.dragging = False
        return outcome

    def cancel(self):
        """Abandon the sequence; the current visual position is retained."""

        was_active = self.active
        self.active = False
        self.dragging = False
        self._origin = None
        self._offset = None
        return was_active
