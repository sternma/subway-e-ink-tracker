from ui.palette import PAPER, line_color, line_text_color, temperature_color


def test_f_and_g_use_official_mta_colors():
    assert line_color("F") == (255, 99, 25)
    assert line_color("G") == (108, 190, 69)
    assert line_text_color("F") == PAPER
    assert line_text_color("N") == (16, 16, 16)


def test_temperature_bands():
    assert temperature_color(20) == (26, 96, 168)
    assert temperature_color(70) == (16, 16, 16)
    assert temperature_color(95) == (198, 40, 40)
