import io
import struct

from ui.key_input import Debouncer
from ui.touch_input import (
    BTN_TOUCH,
    EVENT_SIZE,
    EV_KEY,
    _EVENT_FORMAT,
    find_touch_device,
    iter_taps,
    start_touch_listener,
)


def _event(ev_type: int, code: int, value: int) -> bytes:
    return struct.pack(_EVENT_FORMAT, 0, 0, ev_type, code, value)


def test_iter_taps_fires_on_btn_touch_press_only():
    stream = io.BytesIO(
        _event(EV_KEY, BTN_TOUCH, 1)
        + _event(EV_KEY, BTN_TOUCH, 1)
        + _event(EV_KEY, BTN_TOUCH, 0)
        + _event(3, 0x35, 100)
    )
    taps = list(iter_taps(stream, Debouncer(0.35), clock=lambda: 10.0))
    assert len(taps) == 1


def test_iter_taps_debounces_fast_retouch():
    now = [10.0]

    def clock():
        return now[0]

    stream = io.BytesIO(_event(EV_KEY, BTN_TOUCH, 1) + _event(EV_KEY, BTN_TOUCH, 1))
    taps = []
    for _ in iter_taps(stream, Debouncer(0.35), clock=clock):
        taps.append("tap")
        now[0] += 0.1
    assert taps == ["tap"]


def test_start_touch_listener_missing_device_does_not_crash(monkeypatch):
    monkeypatch.setattr("ui.touch_input.find_touch_device", lambda: None)
    assert not start_touch_listener(lambda: None)


def test_find_touch_device_picks_btn_touch_node(tmp_path, monkeypatch):
    event = tmp_path / "event5"
    event.write_bytes(b"")
    sysfs = tmp_path / "sys"
    key = sysfs / "event5" / "device" / "capabilities" / "key"
    key.parent.mkdir(parents=True)
    # BTN_TOUCH is bit 0x14A = 330. Word 5 (bits 320-383) has bit 10 set = 0x400.
    key.write_text("400 0 0 0 0 0\n")
    (sysfs / "event5" / "device" / "name").write_text("11-0041 ili_v3\n")

    monkeypatch.setattr("ui.touch_input._DEVICE_ROOT", sysfs)
    monkeypatch.setattr("ui.touch_input.glob.glob", lambda _pat: [str(event)])
    assert find_touch_device() == str(event)
    assert EVENT_SIZE == 24
