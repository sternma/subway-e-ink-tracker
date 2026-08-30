from ui.backlight import Backlight, discover


def test_toggle_flips_bl_power(tmp_path):
    device = tmp_path / "panel"
    device.mkdir()
    (device / "max_brightness").write_text("31\n")
    (device / "brightness").write_text("15\n")
    (device / "bl_power").write_text("0\n")

    backlight = Backlight(device)
    assert backlight.is_on() is True
    assert backlight.toggle() is False
    assert (device / "bl_power").read_text() == "1"
    assert backlight.toggle() is True
    assert (device / "bl_power").read_text() == "0"


def test_brightness_percent_round_trip(tmp_path):
    device = tmp_path / "panel"
    device.mkdir()
    (device / "max_brightness").write_text("31\n")
    (device / "brightness").write_text("0\n")
    (device / "bl_power").write_text("0\n")

    backlight = Backlight(device)
    backlight.set_brightness_percent(50)
    assert backlight.get_brightness() == 16


def test_discover_skips_unwritable_ghost(tmp_path):
    ghost = tmp_path / "ghost"
    ghost.mkdir()
    (ghost / "brightness").write_text("10\n")
    ghost.chmod(0o555)

    real = tmp_path / "panel_backlight@1"
    real.mkdir()
    (real / "max_brightness").write_text("31\n")
    (real / "brightness").write_text("15\n")
    (real / "bl_power").write_text("0\n")

    found = discover(tmp_path)
    assert found is not None
    assert found.path.name == "panel_backlight@1"
