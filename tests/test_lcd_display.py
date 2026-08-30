from PIL import Image

from ui.lcd_display import ChannelLayout, FramebufferGeometry, fit_to_panel, pack_frame


def _xrgb(width=4, height=2, stride=None) -> FramebufferGeometry:
    bpp = 32
    return FramebufferGeometry(
        width=width,
        height=height,
        bits_per_pixel=bpp,
        stride=stride or width * 4,
        red=ChannelLayout(16, 8),
        green=ChannelLayout(8, 8),
        blue=ChannelLayout(0, 8),
        transp=ChannelLayout(24, 8),
    )


def test_pack_frame_xrgb8888_little_endian():
    img = Image.new("RGB", (1, 1), (0x11, 0x22, 0x33))
    payload = pack_frame(img, _xrgb(1, 1))
    assert payload == bytes((0x33, 0x22, 0x11, 0xFF))


def test_pack_frame_pads_to_stride():
    img = Image.new("RGB", (1, 1), (1, 2, 3))
    payload = pack_frame(img, _xrgb(1, 1, stride=8))
    assert len(payload) == 8
    assert payload[:4] == bytes((3, 2, 1, 0xFF))
    assert payload[4:] == b"\x00\x00\x00\x00"


def test_fit_to_panel_letterboxes_mismatch():
    img = Image.new("RGB", (10, 10), (255, 0, 0))
    fitted = fit_to_panel(img, 20, 10)
    assert fitted.size == (20, 10)
    assert fitted.getpixel((0, 0)) == (0, 0, 0)
    assert fitted.getpixel((10, 5))[0] > 200
