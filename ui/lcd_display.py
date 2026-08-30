"""Display backend for the Raspberry Pi Touch Display 2 (DSI LCD).

This replaces the IT8951 e-ink driver. An LCD has no ghosting and no waveform
selection, so there is nothing to optimize: every frame is a full blit. The
partial/clear flags the runner still passes are accepted and ignored.

Output goes through the DRM fbdev emulation node (`/dev/fb0`) that the
`vc4-kms-dsi-ili9881-7inch` overlay creates. The pixel layout is read from the
kernel via `FBIOGET_VSCREENINFO` rather than assumed, because the byte order of
a 32-bit framebuffer is not something to guess at.
"""

from __future__ import annotations

import fcntl
import logging
import mmap
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

FBIOGET_VSCREENINFO = 0x4600

# struct fb_var_screeninfo is 41 u32 fields on all Linux ABIs we care about.
_VSCREENINFO_FIELDS = 41
_VSCREENINFO_FORMAT = f"{_VSCREENINFO_FIELDS}I"

# Field indices within fb_var_screeninfo.
_IDX_XRES = 0
_IDX_YRES = 1
_IDX_BPP = 6
_IDX_RED = 8      # each fb_bitfield is (offset, length, msb_right)
_IDX_GREEN = 11
_IDX_BLUE = 14
_IDX_TRANSP = 17


class DisplayUnavailableError(RuntimeError):
    """Raised when the panel framebuffer cannot be opened."""


@dataclass(frozen=True)
class ChannelLayout:
    """Where one color channel sits within a packed pixel."""
    offset: int
    length: int


@dataclass(frozen=True)
class FramebufferGeometry:
    width: int
    height: int
    bits_per_pixel: int
    stride: int
    red: ChannelLayout
    green: ChannelLayout
    blue: ChannelLayout
    transp: ChannelLayout

    @property
    def bytes_per_pixel(self) -> int:
        return self.bits_per_pixel // 8

    @property
    def size_bytes(self) -> int:
        return self.stride * self.height

    def describe(self) -> str:
        return (
            f"{self.width}x{self.height} {self.bits_per_pixel}bpp "
            f"stride={self.stride} "
            f"r@{self.red.offset}/{self.red.length} "
            f"g@{self.green.offset}/{self.green.length} "
            f"b@{self.blue.offset}/{self.blue.length}"
        )


# Sensible XRGB8888 little-endian layout, used when the ioctl is unavailable.
_DEFAULT_LAYOUT = FramebufferGeometry(
    width=1200,
    height=1920,
    bits_per_pixel=32,
    stride=1200 * 4,
    red=ChannelLayout(16, 8),
    green=ChannelLayout(8, 8),
    blue=ChannelLayout(0, 8),
    transp=ChannelLayout(24, 8),
)


def _read_sysfs_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _sysfs_node_for(device: str) -> Path:
    return Path("/sys/class/graphics") / Path(device).name


def read_geometry(fd: int, device: str) -> FramebufferGeometry:
    """Read framebuffer geometry, preferring the ioctl and falling back to sysfs.

    The ioctl is the only source for channel offsets, so a failure there means
    we have to assume XRGB8888. Geometry itself is cross-checked against sysfs,
    which is also where `stride` comes from since the fixed-info ioctl has
    awkward struct alignment.
    """
    node = _sysfs_node_for(device)
    stride = _read_sysfs_int(node / "stride")

    try:
        raw = fcntl.ioctl(fd, FBIOGET_VSCREENINFO, b"\x00" * struct.calcsize(_VSCREENINFO_FORMAT))
        v = struct.unpack(_VSCREENINFO_FORMAT, raw)
    except OSError as exc:
        logger.warning(
            "FBIOGET_VSCREENINFO failed on %s (%s); assuming XRGB8888", device, exc
        )
        virtual = (node / "virtual_size").read_text().strip().split(",")
        width, height = int(virtual[0]), int(virtual[1])
        bpp = _read_sysfs_int(node / "bits_per_pixel") or 32
        return FramebufferGeometry(
            width=width,
            height=height,
            bits_per_pixel=bpp,
            stride=stride or width * (bpp // 8),
            red=_DEFAULT_LAYOUT.red,
            green=_DEFAULT_LAYOUT.green,
            blue=_DEFAULT_LAYOUT.blue,
            transp=_DEFAULT_LAYOUT.transp,
        )

    bpp = v[_IDX_BPP]
    width = v[_IDX_XRES]
    height = v[_IDX_YRES]
    return FramebufferGeometry(
        width=width,
        height=height,
        bits_per_pixel=bpp,
        stride=stride or width * (bpp // 8),
        red=ChannelLayout(v[_IDX_RED], v[_IDX_RED + 1]),
        green=ChannelLayout(v[_IDX_GREEN], v[_IDX_GREEN + 1]),
        blue=ChannelLayout(v[_IDX_BLUE], v[_IDX_BLUE + 1]),
        transp=ChannelLayout(v[_IDX_TRANSP], v[_IDX_TRANSP + 1]),
    )


def fit_to_panel(img: Image.Image, width: int, height: int) -> Image.Image:
    """Letterbox an image to the panel size, preserving aspect ratio.

    A size mismatch means the render config and the panel disagree, which is a
    configuration bug -- but a letterboxed picture is far more debuggable than a
    crash or a torn frame, so we scale and center rather than raise.
    """
    if img.size == (width, height):
        return img

    scale = min(width / img.width, height / img.height)
    new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    canvas.paste(
        img.resize(new_size, Image.Resampling.LANCZOS),
        ((width - new_size[0]) // 2, (height - new_size[1]) // 2),
    )
    return canvas


def pack_frame(img: Image.Image, geom: FramebufferGeometry) -> bytes:
    """Convert an RGB image into raw framebuffer bytes for `geom`.

    Pure function so the pixel math can be tested off-Pi.
    """
    if geom.bits_per_pixel not in (16, 32):
        raise ValueError(f"unsupported framebuffer depth: {geom.bits_per_pixel}bpp")

    if img.mode != "RGB":
        img = img.convert("RGB")
    img = fit_to_panel(img, geom.width, geom.height)

    rgb = np.asarray(img, dtype=np.uint8)
    accumulator = np.zeros((geom.height, geom.width), dtype=np.uint32)
    for plane, channel in enumerate((geom.red, geom.green, geom.blue)):
        if channel.length == 0:
            continue
        # Truncate each 8-bit channel to the depth the panel actually uses.
        values = (rgb[:, :, plane] >> (8 - channel.length)).astype(np.uint32)
        accumulator |= values << channel.offset

    if geom.transp.length:
        opaque = np.uint32((1 << geom.transp.length) - 1) << np.uint32(geom.transp.offset)
        accumulator |= opaque

    dtype = np.dtype("<u4") if geom.bits_per_pixel == 32 else np.dtype("<u2")
    packed = accumulator.astype(dtype)

    row_bytes = geom.width * geom.bytes_per_pixel
    if geom.stride == row_bytes:
        return packed.tobytes()

    # Pad each row out to the hardware stride.
    padded = np.zeros((geom.height, geom.stride), dtype=np.uint8)
    padded[:, :row_bytes] = packed.view(np.uint8).reshape(geom.height, row_bytes)
    return padded.tobytes()


class FramebufferSurface:
    """Memory-mapped writer for a Linux framebuffer device."""

    def __init__(self, device: str = "/dev/fb0"):
        self.device = device
        self._fd: Optional[int] = None
        self._map: Optional[mmap.mmap] = None
        self.geometry: Optional[FramebufferGeometry] = None

    def open(self) -> FramebufferGeometry:
        if not os.path.exists(self.device):
            raise DisplayUnavailableError(
                f"{self.device} does not exist. The DSI panel overlay "
                "(dtoverlay=vc4-kms-dsi-ili9881-7inch) may not be loaded."
            )
        try:
            self._fd = os.open(self.device, os.O_RDWR)
        except OSError as exc:
            raise DisplayUnavailableError(f"cannot open {self.device}: {exc}") from exc

        self.geometry = read_geometry(self._fd, self.device)
        try:
            self._map = mmap.mmap(self._fd, self.geometry.size_bytes)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise DisplayUnavailableError(
                f"cannot mmap {self.device} ({self.geometry.size_bytes} bytes): {exc}"
            ) from exc

        logger.info("Framebuffer %s ready: %s", self.device, self.geometry.describe())
        return self.geometry

    def write(self, payload: bytes) -> None:
        if self._map is None:
            raise DisplayUnavailableError("framebuffer is not open")
        self._map.seek(0)
        self._map.write(payload)

    def close(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def _hide_console_cursor() -> None:
    """Stop the text console cursor blinking over our frame.

    Best effort: the durable fix is `vt.global_cursor_default=0` on the kernel
    command line, which this only complements for the current boot.
    """
    try:
        Path("/sys/class/graphics/fbcon/cursor_blink").write_text("0")
    except OSError:
        pass


class LcdDisplay:
    """Blits full frames to the DSI panel.

    Mirrors the interface the runner expects from the old `EInkDisplay`
    (`initialize`, `update`), minus everything that was e-ink specific.
    """

    def __init__(self, rotation: int = 0, device: str = "/dev/fb0"):
        if rotation not in (0, 90, 180, 270):
            raise ValueError(f"rotation must be 0, 90, 180 or 270 (got {rotation})")
        self.rotation = rotation
        self.surface = FramebufferSurface(device)
        self.geometry: Optional[FramebufferGeometry] = None

    def initialize(self) -> None:
        self.geometry = self.surface.open()
        _hide_console_cursor()

    def _oriented(self, img: Image.Image) -> Image.Image:
        if self.rotation == 0:
            return img
        # PIL rotates counter-clockwise; expand so 90/270 swap the axes.
        return img.rotate(self.rotation, expand=True)

    def update(
        self,
        img: Image.Image,
        partial: bool = False,
        clear: bool = False,
        metadata=None,
    ) -> None:
        if self.geometry is None:
            raise DisplayUnavailableError("display not initialized")
        try:
            self.surface.write(pack_frame(self._oriented(img), self.geometry))
        except Exception:
            logger.exception("Error writing frame to %s", self.surface.device)
            raise

    def close(self) -> None:
        self.surface.close()
