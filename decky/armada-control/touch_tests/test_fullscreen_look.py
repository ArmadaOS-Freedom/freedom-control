import math

import pytest

from py_modules.freedom_touch.engine import TouchEngine


BASE_LAYOUT = [
    {'id': 'left_stick', 'kind': 'stick', 'x': 0.0, 'y': 0.55, 'w': 0.30, 'h': 0.35, 'output': 'left'},
    {'id': 'right_stick', 'kind': 'stick', 'x': 0.70, 'y': 0.55, 'w': 0.30, 'h': 0.35, 'output': 'right'},
    {'id': 'a_button', 'kind': 'button', 'x': 0.80, 'y': 0.10, 'w': 0.12, 'h': 0.12, 'output': 'a'},
    {'id': 'dpad', 'kind': 'dpad', 'x': 0.05, 'y': 0.10, 'w': 0.25, 'h': 0.25, 'output': 'left'},
    {'id': 'pad', 'kind': 'pad', 'x': 0.40, 'y': 0.10, 'w': 0.12, 'h': 0.12, 'output': 'right'},
]


def make_engine(flag=False, layout=None, **kwargs):
    kwargs.setdefault('deadzone', 0.0)
    eng = TouchEngine(BASE_LAYOUT if layout is None else layout, right_stick_fullscreen=flag, **kwargs)
    eng.set_active(True)
    return eng


def axes(events):
    return {name: value for kind, name, value in events if kind == 'axis'}


def test_flag_default_disabled_blank_background_is_ignored():
    eng = TouchEngine(BASE_LAYOUT)
    eng.set_active(True)
    eng.begin(1, 0.50, 0.40, 0.0)
    moved = eng.move(1, 0.55, 0.44, 0.02)
    assert moved == []
    assert eng.tick(1.0) == []


def test_disabled_right_stick_remains_absolute_axes():
    eng = make_engine(flag=False)
    eng.begin(1, 0.80, 0.725, 0.0)
    moved = eng.move(1, 0.90, 0.725, 0.10)
    out = axes(moved)
    assert 'rx' in out and 'ry' in out
    assert abs(out['ry']) < 1e-6


def test_enabled_blank_background_creates_look_owner_without_down_output():
    eng = make_engine(flag=True)
    assert eng.begin(7, 0.50, 0.40, 1.0) == []
    moved = eng.move(7, 0.55, 0.44, 1.02)
    out = axes(moved)
    assert 'rx' in out and 'ry' in out
    assert eng._look_owner['pointer_id'] == 7


def test_right_stick_rect_is_excluded_and_uses_relative_motion():
    eng = make_engine(flag=True)
    eng.begin(3, 0.80, 0.70, 0.0)
    move = eng.move(3, 0.84, 0.73, 0.02)
    out = axes(move)
    assert set(out) == {'rx', 'ry'}
    assert abs(out['rx']) > 0 and abs(out['ry']) > 0


def test_explicit_left_stick_button_dpad_pad_win_over_look():
    eng = make_engine(flag=True)
    eng.set_active(True)
    for pid, (x, y) in enumerate(
        [(0.10, 0.70), (0.83, 0.14), (0.14, 0.18), (0.44, 0.14)], start=1
    ):
        eng.begin(pid, x, y, 0.0)
    assert eng._look_owner is None
    eng.begin(10, 0.50, 0.40, 0.0)
    assert eng._look_owner.get('pointer_id') == 10


def test_look_speed_uses_delta_over_dt_and_radial_deadzone():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    out = axes(eng.move(1, 0.55, 0.50, 0.10))
    assert abs(out['rx'] - 0.25) < 1e-6
    assert abs(out['ry']) < 1e-6


def test_equal_x_and_y_deltas_with_aspect_match_magnitude():
    aspect = 2.0
    eng = make_engine(flag=True, aspect_ratio=aspect)
    eng.begin(1, 0.50, 0.50, 0.0)
    dx = 0.04
    dy = dx * aspect
    out = axes(eng.move(1, 0.54, 0.50 + dy, 0.05))
    assert abs(abs(out['rx']) - abs(out['ry'])) < 1e-6


def test_multitouch_left_stick_and_button_operate_while_look_continues():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    eng.begin(2, 0.10, 0.70, 0.01)
    assert eng.begin(3, 0.83, 0.14, 0.01) == [('button', 'a', True)]
    look = axes(eng.move(1, 0.53, 0.51, 0.03))
    left = eng.move(2, 0.20, 0.725, 0.04)
    btn = eng.move(3, 0.83, 0.14, 0.04)
    assert set(look) == {'rx', 'ry'}
    assert eng._look_owner['pointer_id'] == 1
    assert axes(left)['lx'] > 0
    assert btn == []
    rel = eng.end(3, 0.05)
    assert ('button', 'a', False) in rel
    assert eng._look_owner['pointer_id'] == 1
    assert axes(eng.move(1, 0.54, 0.51, 0.06))['rx'] > 0


def test_second_blank_finger_does_not_steal_or_reset_owner():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    eng.begin(2, 0.52, 0.52, 0.01)
    assert eng._look_owner['pointer_id'] == 1
    eng.move(1, 0.54, 0.50, 0.02)
    assert eng._look_owner['pointer_id'] == 1


def test_release_nonowner_does_not_clear_owner():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    eng.begin(2, 0.50, 0.60, 0.0)
    eng.end(2, 0.02)
    assert eng._look_owner['pointer_id'] == 1
    assert axes(eng.move(1, 0.52, 0.50, 0.03))['rx'] > 0


def test_tick_idle_returns_neutral_once_then_fresh_move_continues():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    eng.move(1, 0.54, 0.50, 0.02)
    idle = eng.tick(0.07)
    assert idle == [('axis', 'rx', 0.0), ('axis', 'ry', 0.0)]
    assert eng.tick(0.08) == []
    again = axes(eng.move(1, 0.57, 0.50, 0.09))
    assert again['rx'] > 0


def test_reverse_direction_after_idle_works():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    eng.move(1, 0.55, 0.50, 0.02)
    eng.tick(0.20)
    back = axes(eng.move(1, 0.50, 0.50, 0.22))
    assert back['rx'] < 0


def test_release_deactivate_cancel_neutralize_and_reacquire():
    eng = make_engine(flag=True)
    eng.begin(1, 0.50, 0.50, 0.0)
    eng.move(1, 0.55, 0.50, 0.02)
    cancel = eng.cancel_all()
    assert axes(cancel) == {'rx': 0.0, 'ry': 0.0}
    assert eng._look_owner is None
    eng.begin(2, 0.50, 0.50, 0.04)
    assert eng._look_owner['pointer_id'] == 2
    off = eng.set_active(False)
    assert axes(off) == {'rx': 0.0, 'ry': 0.0}
    assert eng.begin(3, 0.50, 0.50, 0.06) == []
    assert eng.tick(1.0) == []
    eng.set_active(True)
    eng.begin(4, 0.50, 0.50, 0.08)
    assert eng._look_owner['pointer_id'] == 4


def test_invalid_flag_values_rejected():
    for bad in ('yes', 1, None):
        try:
            TouchEngine(BASE_LAYOUT, right_stick_fullscreen=bad)
        except (TypeError, ValueError):
            continue
        pytest.fail(f'invalid flag accepted: {bad!r}')


def test_invalid_numeric_inputs_ignored_without_poisoning():
    eng = make_engine(flag=True)
    assert eng.begin(1, float('nan'), 0.50, 0.0) == []
    assert eng.begin(1, 0.50, float('inf'), 0.0) == []
    assert eng.begin(1, 0.50, 0.50, float('nan')) == []
    eng.begin(2, 0.50, 0.50, 1.0)
    assert eng.move(2, 0.55, 0.50, -1.0) == []
    assert eng.move(2, float('nan'), 0.50, 1.01) == []
    assert eng.move(2, 0.55, float('inf'), 1.02) == []
    assert eng.move(2, 0.55, 0.50, 0.99) == []
    assert eng._look_owner['pointer_id'] == 2
    ok = axes(eng.move(2, 0.55, 0.50, 1.05))
    assert ok['rx'] > 0


def test_after_release_can_reacquire_and_tick_without_owner_returns_empty():
    eng = make_engine(flag=True)
    assert eng.tick(1.0) == []
    eng.begin(1, 0.50, 0.50, 2.0)
    eng.end(1, 2.02)
    assert eng.tick(2.2) == []
    eng.begin(2, 0.55, 0.50, 2.3)
    assert eng._look_owner['pointer_id'] == 2
    assert eng.end(2, 2.31) == [('axis', 'rx', 0.0), ('axis', 'ry', 0.0)]


def test_enabled_edge_zone_wins_before_look():
    eng = make_engine(flag=True, edge_swipes=True)
    left = eng.begin(1, 0.03, 0.50, 0.0)
    right = eng.begin(2, 0.97, 0.50, 0.0)
    assert left == []
    assert right == []
    assert eng._look_owner is None
    assert eng.move(1, 0.15, 0.50, 0.02) == [('menu', 'guide')]
    assert eng.move(2, 0.85, 0.50, 0.02) == [('menu', 'qam')]


def test_flag_true_custom_layout_without_right_stick():
    eng = make_engine(flag=True, layout=[])
    eng.begin(1, 0.50, 0.40, 0.0)
    out = axes(eng.move(1, 0.55, 0.44, 0.02))
    assert 'rx' in out and 'ry' in out
    assert eng._look_owner['pointer_id'] == 1


def test_look_nonzero_radial_deadzone_and_unit_clamp():
    eng = make_engine(flag=True, deadzone=0.2)
    eng.begin(1, 0.50, 0.50, 0.0)
    tiny = axes(eng.move(1, 0.50001, 0.50, 0.02))
    assert tiny['rx'] == 0.0 and tiny['ry'] == 0.0
    large = axes(eng.move(1, 0.95, 0.50, 0.03))
    assert math.hypot(large['rx'], large['ry']) <= 1.0 + 1e-6
