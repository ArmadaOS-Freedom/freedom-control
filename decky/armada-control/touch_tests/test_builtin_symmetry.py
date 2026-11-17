import copy
import pytest

from py_modules.freedom_touch.presets import builtin_presets
from py_modules.freedom_touch.layout import circular_stick_rect
from py_modules.freedom_touch.runtime_policy import effective_layout
from py_modules.freedom_touch.engine import TouchEngine


ASPECTS = (956 / 599, 1920 / 1080, 3040 / 1904)
MIRROR_PAIRS = (
    ("stick_l", "stick_r"),
    ("pad_l", "pad_r"),
    ("btn_l3", "btn_r3"),
    ("btn_lb", "btn_rb"),
    ("btn_lt", "btn_rt"),
    ("btn_guide", "btn_qam"),
    ("btn_back", "btn_start"),
)


def _by_id(layout):
    return {c["id"]: c for c in layout}


def _cy(c):
    return c["y"] + c["h"] / 2.0


def _cx(c):
    return c["x"] + c["w"] / 2.0


def _union(controls):
    x0 = min(c["x"] for c in controls)
    y0 = min(c["y"] for c in controls)
    x1 = max(c["x"] + c["w"] for c in controls)
    y1 = max(c["y"] + c["h"] for c in controls)
    return x0, y0, x1 - x0, y1 - y0


@pytest.mark.parametrize("aspect", ASPECTS)
@pytest.mark.parametrize(
    "preset", builtin_presets(), ids=[p["id"] for p in builtin_presets()]
)
def test_builtin_symmetry(preset, aspect):
    name = preset["id"]
    raw = preset["layout"]
    layout = effective_layout(raw, 1.0)
    by_id = _by_id(layout)
    assert len(by_id) == 19

    # Mirrored pairs: equal dims, equal y, mirror x extents
    for left, right in MIRROR_PAIRS:
        a, b = by_id[left], by_id[right]
        assert a["w"] == pytest.approx(b["w"])
        assert a["h"] == pytest.approx(b["h"])
        assert a["y"] == pytest.approx(b["y"])
        assert a["x"] == pytest.approx(1.0 - (b["x"] + b["w"]))

    # D-pad union mirrors face diamond union
    dpad = by_id["dpad_l"]
    face = [by_id[k] for k in ("btn_y", "btn_x", "btn_b", "btn_a")]
    dx, dy, dw, dh = _union([dpad])
    fx, fy, fw, fh = _union(face)
    assert dw == pytest.approx(fw)
    assert dh == pytest.approx(fh)
    assert dy == pytest.approx(fy)
    assert dx == pytest.approx(1.0 - (fx + fw))

    # Upper row is only the 4 shoulder keys; all share y and h.
    upper = [by_id[k] for k in (
        "btn_lb", "btn_lt", "btn_rt", "btn_rb",
    )]
    assert len({c["y"] for c in upper}) == 1
    assert len({c["h"] for c in upper}) == 1

    # Lower sticks/pads share cy=.790
    for k in ("stick_l", "stick_r", "pad_l", "pad_r"):
        assert _cy(by_id[k]) == pytest.approx(0.790)

    # L3/R3 centers below own sticks, center x equals stick center x
    for stick, click in (("stick_l", "btn_l3"), ("stick_r", "btn_r3")):
        assert _cy(by_id[click]) > _cy(by_id[stick])
        assert _cx(by_id[click]) == pytest.approx(_cx(by_id[stick]))

    # Stick projections: width*aspect == height (physical circle)
    for k in ("stick_l", "stick_r"):
        c = circular_stick_rect(by_id[k], aspect)
        assert c["w"] * aspect == pytest.approx(c["h"])

    # Family-correct labels
    labels = {c["id"]: c["label"] for c in layout}
    if name == "tablet_standard":
        assert labels["btn_y"] == "Y" and labels["btn_a"] == "A"
    elif name == "ps":
        assert labels["btn_y"] == "\u25b3" and labels["btn_a"] == "\u00d7"
    elif name == "switch":
        assert labels["btn_y"] == "X" and labels["btn_a"] == "B"

    # Engine hit-test at each intended center (normalized coords, scale 1)
    raw_snapshot = copy.deepcopy(raw)
    eng = TouchEngine(layout, aspect_ratio=aspect)
    for c in layout:
        if c["kind"] == "stick":
            rect = circular_stick_rect(c, aspect)
            cx = rect["x"] + rect["w"] / 2.0
            cy = rect["y"] + rect["h"] / 2.0
        else:
            cx, cy = _cx(c), _cy(c)
        hit = eng._hit(cx, cy)
        assert hit is not None
        assert hit["id"] == c["id"]
    assert raw == raw_snapshot

    # Independent fresh copies / no mutation
    fresh1 = next(p for p in builtin_presets() if p["id"] == name)["layout"]
    fresh2 = next(p for p in builtin_presets() if p["id"] == name)["layout"]
    assert fresh1 == fresh2
    assert fresh1 is not fresh2
    snapshot = copy.deepcopy(fresh2)
    fresh1[0]["x"] += 0.01
    assert fresh2 == snapshot
    fresh3 = next(p for p in builtin_presets() if p["id"] == name)["layout"]
    assert fresh3 == snapshot


@pytest.mark.parametrize("scale", (0.6, 1.0, 1.4))
@pytest.mark.parametrize(
    "preset", builtin_presets(), ids=[p["id"] for p in builtin_presets()]
)
def test_top_utility_keys_do_not_overlap_at_supported_scales(preset, scale):
    layout = effective_layout(preset["layout"], scale)
    ids = ("btn_guide", "btn_qam", "btn_back", "btn_start")
    utility = [c for c in layout if c["id"] in ids]
    assert len(utility) == 4
    for c in utility:
        assert c["x"] >= 0.0
        assert c["y"] >= 0.0
        assert c["x"] + c["w"] <= 1.0
        assert c["y"] + c["h"] <= 0.1
    for i, first in enumerate(utility):
        for second in utility[i + 1:]:
            assert not (
                first["x"] < second["x"] + second["w"]
                and second["x"] < first["x"] + first["w"]
                and first["y"] < second["y"] + second["h"]
                and second["y"] < first["y"] + first["h"]
            ), "%s overlaps %s at scale %s" % (
                first["id"], second["id"], scale
            )


@pytest.mark.parametrize("aspect", ASPECTS)
@pytest.mark.parametrize(
    "preset", builtin_presets(), ids=[p["id"] for p in builtin_presets()]
)
def test_builtin_controls_sit_in_lower_thumb_area(preset, aspect):
    layout = effective_layout(preset["layout"], 1.0)
    by_id = _by_id(layout)

    # D-pad and face-diamond union center Y sit in the lower thumb area.
    dpad = by_id["dpad_l"]
    face = [by_id[k] for k in ("btn_y", "btn_x", "btn_b", "btn_a")]
    _, dy, _, dh = _union([dpad])
    _, fy, _, fh = _union(face)
    assert dy + dh / 2.0 == pytest.approx(0.530)
    assert fy + fh / 2.0 == pytest.approx(0.530)

    # Sticks and pads share the same lower center line.
    for k in ("stick_l", "stick_r", "pad_l", "pad_r"):
        assert _cy(by_id[k]) == pytest.approx(0.790)

    # Shoulder row center Y moved down for two-handed grip.
    for k in ("btn_lb", "btn_lt", "btn_rt", "btn_rb"):
        assert _cy(by_id[k]) == pytest.approx(0.320)

    # Utility keys sit at cy=.0575: guide/QAM in the extreme top corners,
    # back/start at the upper middle (centres .44/.56).
    for k in ("btn_guide", "btn_qam", "btn_back", "btn_start"):
        assert _cy(by_id[k]) == pytest.approx(0.0575)
        assert by_id[k]["y"] + by_id[k]["h"] <= 0.1
    assert by_id["btn_guide"]["x"] + by_id["btn_guide"]["w"] < 0.1
    assert by_id["btn_qam"]["x"] > 0.9
    assert _cx(by_id["btn_back"]) == pytest.approx(0.44)
    assert _cx(by_id["btn_start"]) == pytest.approx(0.56)

    # Bottom clearance after circular projection; all projected rects in
    # bounds and non-overlapping.
    projected = [circular_stick_rect(c, aspect) for c in layout]
    for k in ("stick_l", "stick_r"):
        c = circular_stick_rect(by_id[k], aspect)
        assert 1.0 - (c["y"] + c["h"]) >= 0.015
    for c in projected:
        assert c["x"] >= 0.0
        assert c["y"] >= 0.0
        assert c["x"] + c["w"] <= 1.0
        assert c["y"] + c["h"] <= 1.0

    # No overlapping hit rectangles at default scale.
    for i, first in enumerate(projected):
        for second in projected[i + 1:]:
            assert not (
                first["x"] < second["x"] + second["w"]
                and second["x"] < first["x"] + first["w"]
                and first["y"] < second["y"] + second["h"]
                and second["y"] < first["y"] + first["h"]
            ), "%s overlaps %s" % (first["id"], second["id"])
