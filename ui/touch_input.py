"""Touchscreen tap detection for the Raspberry Pi Touch Display 2.

The panel's capacitive touch controller is exposed as a normal Linux evdev
device, so we read `input_event` records straight from `/dev/input/event*`
rather than taking on a dependency. A tap is the rising edge of `BTN_TOUCH`,
which the Goodix driver emits alongside the multi-touch slots; ignoring the
slot data entirely means a five-finger mash still counts as one tap.

Reading these nodes requires membership in the `input` group.
"""

from __future__ import annotations

import glob
import logging
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from ui.key_input import Debouncer

logger = logging.getLogger(__name__)

# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
# On 64-bit Linux timeval is two 64-bit words, so the record is 24 bytes.
_EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)

EV_KEY = 0x01
BTN_TOUCH = 0x14A

_DEVICE_ROOT = Path("/sys/class/input")


def _device_name(event_name: str) -> str:
    try:
        return (_DEVICE_ROOT / event_name / "device" / "name").read_text().strip()
    except OSError:
        return ""


def _supports_touch(event_name: str) -> bool:
    """True when the device advertises BTN_TOUCH in its key capability bitmap.

    `capabilities/key` is a big-endian hex bitmap of supported key codes; bit
    0x14A set means the device reports touch contacts.
    """
    try:
        raw = (_DEVICE_ROOT / event_name / "device" / "capabilities" / "key").read_text().strip()
    except OSError:
        return False
    if not raw:
        return False
    words = raw.split()
    bitmap = 0
    # Words are printed most-significant first, each up to 64 bits wide.
    for index, word in enumerate(reversed(words)):
        try:
            bitmap |= int(word, 16) << (64 * index)
        except ValueError:
            return False
    return bool(bitmap >> BTN_TOUCH & 1)


def find_touch_device() -> Optional[str]:
    """Locate the touchscreen event node, or None when there isn't one."""
    for path in sorted(glob.glob("/dev/input/event*")):
        event_name = Path(path).name
        if _supports_touch(event_name):
            logger.info("Touchscreen found: %s (%s)", path, _device_name(event_name) or "unnamed")
            return path
    return None


def iter_taps(stream, debouncer: Debouncer, clock: Callable[[], float] = time.monotonic) -> Iterator[None]:
    """Yield once per debounced BTN_TOUCH press read from `stream`."""
    while True:
        record = stream.read(EVENT_SIZE)
        if not record or len(record) < EVENT_SIZE:
            return
        _sec, _usec, ev_type, code, value = struct.unpack(_EVENT_FORMAT, record)
        if ev_type == EV_KEY and code == BTN_TOUCH and value == 1:
            if debouncer.accept(clock()):
                yield None


def start_touch_listener(
    on_touch: Callable[[], None],
    *,
    device_path: Optional[str] = None,
    debounce_seconds: float = 0.35,
) -> bool:
    """Start a daemon thread that calls ``on_touch`` on each screen tap.

    Returns True when the listener is active. A missing or unreadable device is
    logged as a warning and returns False, so the display still runs without
    touch input.
    """
    path = device_path or find_touch_device()
    if path is None:
        logger.warning(
            "Touch input disabled: no touchscreen event device found. "
            "Is the panel's touch controller detected (10-inch Ilitek ili_v3 on I2C 0x41)?"
        )
        return False

    try:
        stream = open(path, "rb", buffering=0)
    except OSError as exc:
        logger.warning("Touch input disabled: cannot read %s (%s)", path, exc)
        return False

    threading.Thread(
        target=_run_touch_loop,
        args=(stream, on_touch, debounce_seconds),
        daemon=True,
        name="touchscreen-listener",
    ).start()
    return True


def _run_touch_loop(stream, on_touch: Callable[[], None], debounce_seconds: float) -> None:
    debouncer = Debouncer(debounce_seconds)
    try:
        for _ in iter_taps(stream, debouncer):
            try:
                on_touch()
            except Exception as exc:
                logger.error("touch handler error: %s", exc, exc_info=True)
    except OSError as exc:
        logger.warning("Touch device read failed; touch input stopped: %s", exc)
    finally:
        stream.close()
