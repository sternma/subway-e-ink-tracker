"""Backlight control for the DSI panel.

Device selection is capability-based rather than name-based. On a Pi 5 the
kernel can expose several `/sys/class/backlight` entries -- the real one plus
non-functional ghosts -- and which name appears depends on the DSI port and
enumeration order (`panel_backlight@1`, `4-0045`, `6-0045`, `10-0045`,
`rpi_backlight` have all been observed). Probing by writing a value back is the
only reliable way to find the one that works.

Writing to these files needs group access; see the udev rule installed by
`scripts/provision_pi.sh`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BACKLIGHT_ROOT = Path("/sys/class/backlight")


class BacklightUnavailableError(RuntimeError):
    """Raised when no writable backlight device can be found."""


class Backlight:
    """A single backlight device's brightness and power state."""

    def __init__(self, path: Path):
        self.path = path
        self._max: Optional[int] = None

    @property
    def max_brightness(self) -> int:
        if self._max is None:
            self._max = int((self.path / "max_brightness").read_text().strip())
        return self._max

    def get_brightness(self) -> int:
        return int((self.path / "brightness").read_text().strip())

    def set_brightness(self, value: int) -> None:
        clamped = max(0, min(self.max_brightness, int(value)))
        (self.path / "brightness").write_text(str(clamped))

    def get_brightness_percent(self) -> int:
        return round(self.get_brightness() * 100 / self.max_brightness)

    def set_brightness_percent(self, percent: float) -> None:
        pct = max(0.0, min(100.0, float(percent)))
        self.set_brightness(round(pct * self.max_brightness / 100))

    def is_on(self) -> bool:
        """True when the panel is powered.

        `bl_power` follows the kernel's FB_BLANK convention, where 0 means
        unblanked, so a missing file is treated as "on".
        """
        try:
            return int((self.path / "bl_power").read_text().strip()) == 0
        except (OSError, ValueError):
            return True

    def set_on(self, on: bool) -> None:
        (self.path / "bl_power").write_text("0" if on else "1")

    def toggle(self) -> bool:
        """Flip panel power; returns the new state."""
        new_state = not self.is_on()
        self.set_on(new_state)
        logger.info("Backlight %s", "on" if new_state else "off")
        return new_state


def _is_writable(candidate: Path) -> bool:
    """Probe a device by writing its current brightness back to itself."""
    try:
        current = (candidate / "brightness").read_text().strip()
        (candidate / "brightness").write_text(current)
        return True
    except (OSError, ValueError):
        return False


def discover(root: Path = BACKLIGHT_ROOT) -> Optional[Backlight]:
    """Find the first backlight device that accepts a write, or None."""
    if not root.is_dir():
        return None

    candidates = sorted(p for p in root.iterdir() if (p / "brightness").exists())
    if not candidates:
        return None

    for candidate in candidates:
        if _is_writable(candidate):
            logger.info("Using backlight device %s", candidate.name)
            return Backlight(candidate)

    logger.warning(
        "Found %d backlight device(s) but none are writable: %s. "
        "Is the udev rule installed and the user in the video group?",
        len(candidates),
        ", ".join(c.name for c in candidates),
    )
    return None


def require() -> Backlight:
    backlight = discover()
    if backlight is None:
        raise BacklightUnavailableError("no writable backlight device found")
    return backlight
