"""Render the mower's map shapes as a local PNG for Home Assistant."""

from io import BytesIO

from PIL import Image, ImageDraw

from .mapfiles import SVG_NAMESPACE, parse_map_svg

_WIDTH = 800
_HEIGHT = 580
_PADDING = 34
_RASTER_SCALE = 2
_BACKGROUND = "#f3f4ef"
_AREA_COLORS = {
    "green": ("#a9c9a7", "#395e42"),
    "blue": ("#c3d7e5", "#375e79"),
    "red": ("#eac6bb", "#9c4f40"),
}
_REWILDING_COLORS = ("#e6d4a6", "#876538")
_DISABLED_COLORS = ("#e0e1dc", "#898d82")
_PATH_COLORS = {"blue": "#375e79", "cyan": "#277b81", "yellow": "#ad721b"}


def render_map_png(svg: str) -> bytes:
    """Draw only the supported geometry from the Automower SVG."""
    _, root = parse_map_svg(svg, require_unique_ids=False)
    features = [
        (
            shape,
            [
                tuple(map(int, point.split(",")))
                for point in shape.attrib["points"].split()
            ],
        )
        for shape in root
    ]
    x_min = min(x for _, points in features for x, _ in points)
    x_max = max(x for _, points in features for x, _ in points)
    y_min = min(y for _, points in features for _, y in points)
    y_max = max(y for _, points in features for _, y in points)
    x_span, y_span = x_max - x_min, y_max - y_min
    scale = _RASTER_SCALE * min(
        (_WIDTH - 2 * _PADDING) / max(x_span, 1),
        (_HEIGHT - 2 * _PADDING) / max(y_span, 1),
    )
    x_margin = (_WIDTH * _RASTER_SCALE - scale * x_span) / 2
    y_margin = (_HEIGHT * _RASTER_SCALE - scale * y_span) / 2

    image = Image.new(
        "RGB", (_WIDTH * _RASTER_SCALE, _HEIGHT * _RASTER_SCALE), _BACKGROUND
    )
    draw = ImageDraw.Draw(image)
    # Draw work areas first, exclusions above them, and connecting paths last.
    features.sort(
        key=lambda feature: (
            feature[0].tag.removeprefix(SVG_NAMESPACE) == "polyline",
            feature[0].get("fill", "").lower() == "red",
        )
    )
    for shape, points in features:
        pixels = [
            (x_margin + (x - x_min) * scale, y_margin + (y_max - y) * scale)
            for x, y in points
        ]
        disabled = shape.get("display", "").lower() == "none"
        if shape.tag.removeprefix(SVG_NAMESPACE) == "polygon":
            fill, outline = _AREA_COLORS.get(
                shape.get("fill", "").lower(), (None, "#697169")
            )
            if shape.get("rewildingzone", "").lower() == "true":
                fill, outline = _REWILDING_COLORS
            if disabled:
                fill, outline = _DISABLED_COLORS
            draw.polygon(pixels, fill=fill, outline=outline, width=2 * _RASTER_SCALE)
        else:
            stroke = shape.get("stroke", "").lower()
            color = (
                _DISABLED_COLORS[1] if disabled else _PATH_COLORS.get(stroke, "#697169")
            )
            raw_width = shape.get("stroke-width", "")
            width = 3 * _RASTER_SCALE
            if raw_width.isdecimal() and len(raw_width) <= 9:
                width = max(
                    width, min(52 * _RASTER_SCALE, round(int(raw_width) * scale))
                )
            draw.line(pixels, fill=color, width=width, joint="curve")
            if stroke == "yellow":
                x, y = pixels[0]
                marker = 8 * _RASTER_SCALE
                draw.rounded_rectangle(
                    (x - marker, y - marker, x + marker, y + marker),
                    radius=3 * _RASTER_SCALE,
                    fill=color,
                )

    stream = BytesIO()
    image.resize((_WIDTH, _HEIGHT), Image.Resampling.LANCZOS).save(stream, format="PNG")
    return stream.getvalue()
