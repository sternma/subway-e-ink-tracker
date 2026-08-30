"""Display colors.

The render target is a backlit RGB LCD, so color carries meaning here in a way
it could not on the monochrome e-ink panel this project started on. Two rules
keep it legible: body text stays near-black on near-white, and color is only
used where it encodes something (which train line, how cold, whether it will
rain).

`INK` and `PAPER` exist as named constants because PIL interprets a bare
integer fill in RGB mode as a packed color value, not a gray level -- passing
``fill=255`` to an RGB image yields blue, not white.
"""

from typing import Tuple

Color = Tuple[int, int, int]

PAPER: Color = (255, 255, 255)
INK: Color = (16, 16, 16)
MUTED: Color = (110, 110, 110)
RULE: Color = (70, 70, 70)

# Official MTA route bullet colors, grouped by trunk line.
# https://www.mta.info/document/17836 (MTA brand standards)
_MTA_TRUNK_COLORS: dict[str, Color] = {
    "broadway-7av": (238, 53, 46),      # 1 2 3
    "lexington": (0, 147, 60),          # 4 5 6
    "flushing": (185, 51, 173),         # 7
    "8av": (0, 57, 166),                # A C E
    "6av": (255, 99, 25),               # B D F M
    "crosstown": (108, 190, 69),        # G
    "nassau": (153, 102, 51),           # J Z
    "canarsie": (167, 169, 172),        # L
    "broadway": (252, 204, 10),         # N Q R W
    "shuttle": (128, 129, 131),         # S
    "2av": (0, 173, 208),               # T
    "sir": (0, 57, 166),                # SIR
}

_ROUTE_TO_TRUNK: dict[str, str] = {
    **{r: "broadway-7av" for r in ("1", "2", "3")},
    **{r: "lexington" for r in ("4", "5", "6", "6X")},
    **{r: "flushing" for r in ("7", "7X")},
    **{r: "8av" for r in ("A", "C", "E")},
    **{r: "6av" for r in ("B", "D", "F", "M", "FX")},
    "G": "crosstown",
    **{r: "nassau" for r in ("J", "Z")},
    "L": "canarsie",
    **{r: "broadway" for r in ("N", "Q", "R", "W")},
    **{r: "shuttle" for r in ("S", "FS", "GS", "H")},
    "T": "2av",
    **{r: "sir" for r in ("SI", "SIR")},
}

# The yellow Broadway bullets are the only ones the MTA sets in black; white
# on that yellow fails contrast badly.
_DARK_TEXT_TRUNKS = frozenset({"broadway"})

_UNKNOWN_ROUTE_COLOR: Color = (60, 60, 60)


def line_color(route_id: str) -> Color:
    """Bullet fill for a route, falling back to dark gray for unknown routes."""
    trunk = _ROUTE_TO_TRUNK.get((route_id or "").strip().upper())
    if trunk is None:
        return _UNKNOWN_ROUTE_COLOR
    return _MTA_TRUNK_COLORS[trunk]


def line_text_color(route_id: str) -> Color:
    """Letter color to draw inside a route bullet."""
    trunk = _ROUTE_TO_TRUNK.get((route_id or "").strip().upper())
    if trunk in _DARK_TEXT_TRUNKS:
        return (16, 16, 16)
    return PAPER


# Weather
PRECIP: Color = (31, 111, 181)
SUN: Color = (232, 161, 0)

_TEMP_BANDS: tuple[tuple[float, Color], ...] = (
    (32.0, (26, 96, 168)),      # freezing
    (50.0, (62, 143, 207)),     # cold
    (75.0, INK),                # comfortable
    (88.0, (214, 118, 42)),     # warm
)
_TEMP_HOT: Color = (198, 40, 40)


def temperature_color(temp_f: float | None) -> Color:
    """Color for a temperature reading; comfortable temperatures stay as ink."""
    if temp_f is None:
        return INK
    for ceiling, color in _TEMP_BANDS:
        if temp_f < ceiling:
            return color
    return _TEMP_HOT


# Citi Bike
BIKE_CLASSIC: Color = (0, 116, 200)
BIKE_ELECTRIC: Color = (232, 161, 0)
